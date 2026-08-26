package com.alertmanagement.backend.importing;

import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.StringReader;
import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.Charset;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.HashSet;
import java.util.Iterator;
import com.alertmanagement.backend.api.BusinessApiException;
import org.apache.commons.csv.CSVFormat;
import org.apache.commons.csv.CSVParser;
import org.apache.commons.csv.CSVRecord;
import org.apache.poi.ss.usermodel.Cell;
import org.apache.poi.ss.usermodel.CellType;
import org.apache.poi.ss.usermodel.DataFormatter;
import org.apache.poi.ss.usermodel.DateUtil;
import org.apache.poi.ss.usermodel.Row;
import org.apache.poi.ss.usermodel.Sheet;
import org.apache.poi.ss.usermodel.SheetVisibility;
import org.apache.poi.ss.usermodel.Workbook;
import org.apache.poi.ss.usermodel.WorkbookFactory;
import org.apache.poi.openxml4j.util.ZipSecureFile;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.web.multipart.MultipartFile;

@Component
class ImportFileParser {

    private static final DateTimeFormatter EXCEL_DATE_TIME =
            DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    static {
        ZipSecureFile.setMinInflateRatio(0.01);
        ZipSecureFile.setMaxEntrySize(64L * 1024 * 1024);
        ZipSecureFile.setMaxTextSize(64L * 1024 * 1024);
    }

    SourceTable parse(MultipartFile file) {
        String filename = file.getOriginalFilename();
        if (filename == null || filename.isBlank()) {
            throw badRequest("文件名不能为空");
        }
        ImportFormat format = detectFormat(filename);
        try {
            byte[] content = file.getBytes();
            return switch (format) {
                case CSV -> parseDelimited(content, ',', format);
                case TXT -> parseDelimited(content, '\t', format);
                case XLSX -> parseWorkbook(content);
            };
        } catch (IOException | RuntimeException exception) {
            if (exception instanceof ResponseStatusException responseStatusException) {
                throw responseStatusException;
            }
            throw badRequest("文件解析失败：" + safeReason(exception));
        }
    }

    private ImportFormat detectFormat(String filename) {
        String lower = filename.toLowerCase(Locale.ROOT);
        if (lower.endsWith(".csv")) {
            return ImportFormat.CSV;
        }
        if (lower.endsWith(".txt")) {
            return ImportFormat.TXT;
        }
        if (lower.endsWith(".xlsx")) {
            return ImportFormat.XLSX;
        }
        throw badRequest("仅支持 CSV、制表符 TXT 和 XLSX 文件");
    }

    private SourceTable parseDelimited(byte[] content, char delimiter, ImportFormat format) throws IOException {
        String text = decodeText(content);
        CSVFormat csvFormat = CSVFormat.DEFAULT.builder()
                .setDelimiter(delimiter)
                .setIgnoreEmptyLines(false)
                .get();
        try (CSVParser parser = csvFormat.parse(new StringReader(text))) {
            Iterator<CSVRecord> records = parser.iterator();
            if (!records.hasNext()) {
                return emptyTable(format);
            }
            List<String> headers = valuesOf(records.next());
            requireColumnCount(headers.size());
            List<ImportError> errors = validateHeaders(headers);
            List<SourceTable.SourceRow> rows = new ArrayList<>();
            while (records.hasNext()) {
                checkInterrupted();
                CSVRecord record = records.next();
                if (rows.size() >= ImportLimits.MAX_ROWS) {
                    throw tooLarge("IMPORT_ROW_LIMIT", "数据行不能超过 100,000 行");
                }
                requireColumnCount(record.size());
                int sourceRow = Math.toIntExact(record.getRecordNumber());
                if (record.size() != headers.size()) {
                    addError(errors, new ImportError(sourceRow, "_row", "COLUMN_COUNT_MISMATCH",
                            "第 " + sourceRow + " 行列数与表头不一致"));
                }
                rows.add(new SourceTable.SourceRow(sourceRow, rawValues(headers, valuesOf(record))));
            }
            return new SourceTable(format, List.copyOf(headers), List.copyOf(rows), List.copyOf(errors));
        }
    }

    private SourceTable parseWorkbook(byte[] content) throws IOException {
        try (Workbook workbook = WorkbookFactory.create(new ByteArrayInputStream(content))) {
            if (workbook.getNumberOfSheets() > ImportLimits.MAX_SHEETS) {
                throw tooLarge("IMPORT_SHEET_LIMIT", "XLSX 工作表不能超过 8 个");
            }
            Sheet sheet = firstVisibleSheet(workbook);
            if (sheet == null) {
                throw badRequest("XLSX 中没有可见工作表");
            }
            DataFormatter formatter = new DataFormatter(Locale.ROOT);
            Row headerRow = sheet.getRow(0);
            if (headerRow == null) {
                return emptyTable(ImportFormat.XLSX);
            }
            int columnCount = headerRow.getLastCellNum();
            if (columnCount <= 0) {
                return emptyTable(ImportFormat.XLSX);
            }
            requireColumnCount(columnCount);
            if (sheet.getLastRowNum() > ImportLimits.MAX_ROWS) {
                throw tooLarge("IMPORT_ROW_LIMIT", "数据行不能超过 100,000 行");
            }
            List<String> headers = rowValues(headerRow, columnCount, formatter);
            List<ImportError> errors = validateHeaders(headers);
            List<SourceTable.SourceRow> rows = new ArrayList<>();
            for (int rowIndex = 1; rowIndex <= sheet.getLastRowNum(); rowIndex++) {
                checkInterrupted();
                Row row = sheet.getRow(rowIndex);
                if (row != null && row.getLastCellNum() > ImportLimits.MAX_COLUMNS) {
                    throw tooLarge("IMPORT_COLUMN_LIMIT", "列数不能超过 256 列");
                }
                List<String> values = row == null
                        ? java.util.Collections.nCopies(columnCount, "")
                        : rowValues(row, columnCount, formatter);
                rows.add(new SourceTable.SourceRow(rowIndex + 1, rawValues(headers, values)));
            }
            return new SourceTable(ImportFormat.XLSX, List.copyOf(headers), List.copyOf(rows), List.copyOf(errors));
        }
    }

    private Sheet firstVisibleSheet(Workbook workbook) {
        for (int index = 0; index < workbook.getNumberOfSheets(); index++) {
            if (workbook.getSheetVisibility(index) == SheetVisibility.VISIBLE) {
                return workbook.getSheetAt(index);
            }
        }
        return null;
    }

    private List<String> rowValues(Row row, int columnCount, DataFormatter formatter) {
        List<String> values = new ArrayList<>(columnCount);
        for (int column = 0; column < columnCount; column++) {
            values.add(cellText(row.getCell(column), formatter));
        }
        return values;
    }

    private String cellText(Cell cell, DataFormatter formatter) {
        if (cell == null || cell.getCellType() == CellType.BLANK) {
            return "";
        }
        if (cell.getCellType() == CellType.FORMULA) {
            return checkedCell("=" + cell.getCellFormula());
        }
        if (cell.getCellType() == CellType.NUMERIC && DateUtil.isCellDateFormatted(cell)) {
            return checkedCell(EXCEL_DATE_TIME.format(cell.getLocalDateTimeCellValue()));
        }
        return checkedCell(formatter.formatCellValue(cell));
    }

    private List<String> valuesOf(CSVRecord record) {
        List<String> values = new ArrayList<>(record.size());
        record.forEach(value -> values.add(checkedCell(value)));
        return values;
    }

    private List<ImportError> validateHeaders(List<String> headers) {
        List<ImportError> errors = new ArrayList<>();
        Set<String> seen = new HashSet<>();
        for (int index = 0; index < headers.size(); index++) {
            String header = headers.get(index).trim();
            if (header.length() > ImportLimits.MAX_HEADER_CHARACTERS) {
                throw tooLarge("IMPORT_HEADER_LIMIT", "表头文本不能超过 120 个字符");
            }
            if (header.isEmpty()) {
                addError(errors, new ImportError(1, "_header", "MISSING_HEADER",
                        "第 " + (index + 1) + " 列表头为空"));
            } else if (!seen.add(header)) {
                addError(errors, new ImportError(1, header, "DUPLICATE_HEADER", "表头重复：" + header));
            }
        }
        return errors;
    }

    private Map<String, String> rawValues(List<String> headers, List<String> values) {
        Map<String, String> raw = new LinkedHashMap<>();
        for (int index = 0; index < headers.size(); index++) {
            String header = headers.get(index).trim();
            String key = header.isEmpty() ? "_column_" + (index + 1) : header;
            raw.putIfAbsent(key, index < values.size() ? values.get(index) : "");
        }
        return raw;
    }

    private SourceTable emptyTable(ImportFormat format) {
        return new SourceTable(format, List.of(), List.of(), List.of(
                new ImportError(1, "_header", "MISSING_HEADER", "文件缺少表头")));
    }

    private String decodeText(byte[] content) throws CharacterCodingException {
        int offset = content.length >= 3
                && content[0] == (byte) 0xEF
                && content[1] == (byte) 0xBB
                && content[2] == (byte) 0xBF ? 3 : 0;
        ByteBuffer bytes = ByteBuffer.wrap(content, offset, content.length - offset).slice();
        try {
            return decode(bytes, StandardCharsets.UTF_8);
        } catch (CharacterCodingException ignored) {
            bytes.rewind();
            return decode(bytes, Charset.forName("GB18030"));
        }
    }

    private String decode(ByteBuffer bytes, Charset charset) throws CharacterCodingException {
        return charset.newDecoder()
                .onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT)
                .decode(bytes)
                .toString();
    }

    private ResponseStatusException badRequest(String message) {
        return new ResponseStatusException(HttpStatus.BAD_REQUEST, message);
    }

    private String checkedCell(String value) {
        if (value.length() > ImportLimits.MAX_CELL_CHARACTERS) {
            throw tooLarge("IMPORT_CELL_LIMIT", "单元格文本不能超过 4,096 个字符");
        }
        return value;
    }

    private void requireColumnCount(int columns) {
        if (columns > ImportLimits.MAX_COLUMNS) {
            throw tooLarge("IMPORT_COLUMN_LIMIT", "列数不能超过 256 列");
        }
    }

    private void addError(List<ImportError> errors, ImportError error) {
        if (errors.size() >= ImportLimits.MAX_ERRORS) {
            throw tooLarge("IMPORT_ERROR_LIMIT", "校验错误不能超过 1,000 个，请离线修正后重试");
        }
        errors.add(error);
    }

    private void checkInterrupted() {
        if (Thread.currentThread().isInterrupted()) {
            throw new BusinessApiException(HttpStatus.BAD_REQUEST,
                    "IMPORT_PARSE_INTERRUPTED", "文件解析已中断，请重试");
        }
    }

    private BusinessApiException tooLarge(String code, String message) {
        return new BusinessApiException(HttpStatus.PAYLOAD_TOO_LARGE, code, message);
    }

    private String safeReason(Exception exception) {
        return exception.getMessage() == null ? exception.getClass().getSimpleName() : exception.getMessage();
    }
}
