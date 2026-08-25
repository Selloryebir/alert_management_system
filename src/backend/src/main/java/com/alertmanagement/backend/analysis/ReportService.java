package com.alertmanagement.backend.analysis;

import com.alertmanagement.backend.api.BusinessApiException;
import com.alertmanagement.backend.audit.AuditService;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.math.BigDecimal;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.apache.pdfbox.pdmodel.PDDocument;
import org.apache.pdfbox.pdmodel.PDPage;
import org.apache.pdfbox.pdmodel.PDPageContentStream;
import org.apache.pdfbox.pdmodel.common.PDRectangle;
import org.apache.pdfbox.pdmodel.font.PDType0Font;
import org.apache.poi.ss.usermodel.Cell;
import org.apache.poi.ss.usermodel.Row;
import org.apache.poi.ss.usermodel.Sheet;
import org.apache.poi.xssf.streaming.SXSSFWorkbook;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

@Service
class ReportService {

    private static final String IDENTITY = "2026 年灾后重建 Demo";
    private static final String SYNTHETIC_NOTICE = "仅使用合成数据，不代表真实工业准确率";
    private static final String FONT_RESOURCE = "/fonts/NotoSansSC-VF.ttf";

    private final JdbcTemplate jdbcTemplate;
    private final AnalysisWorkflowService workflowService;
    private final AuditService auditService;

    ReportService(JdbcTemplate jdbcTemplate, AnalysisWorkflowService workflowService, AuditService auditService) {
        this.jdbcTemplate = jdbcTemplate;
        this.workflowService = workflowService;
        this.auditService = auditService;
    }

    @Transactional(isolation = Isolation.REPEATABLE_READ)
    public ReportFile generate(UUID runId, String format, ReportRequest request) {
        String operator = requiredOperator(request);
        DashboardView dashboard = workflowService.dashboard(runId);
        RunReportInfo run = runInfo(runId);
        List<ReportAlarmRow> alarms = reportAlarms(runId);
        List<ReportChainRow> chains = reportChains(runId);
        List<ReportDispositionRow> dispositions = reportDispositions(runId);
        long chainCount = chains.stream().map(ReportChainRow::chainId).distinct().count();
        byte[] content = switch (format) {
            case "PDF" -> pdf(run, dashboard, chainCount, operator);
            case "XLSX" -> xlsx(run, dashboard, alarms, chains, dispositions, operator);
            default -> throw new IllegalArgumentException("未知报告格式");
        };
        auditService.record("REPORT_EXPORTED", operator, "ANALYSIS_RUN", runId, "SUCCESS",
                Map.of("format", format, "filters", Map.of(), "record_count", dashboard.total()));
        String extension = format.toLowerCase();
        String mediaType = "PDF".equals(format) ? "application/pdf"
                : "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
        return new ReportFile(content, "alert-report-" + runId + "." + extension, mediaType);
    }

    private byte[] pdf(RunReportInfo run, DashboardView dashboard, long chainCount, String operator) {
        List<String> lines = new ArrayList<>();
        lines.add(IDENTITY);
        lines.add(SYNTHETIC_NOTICE);
        lines.add("分析运行：" + run.runId());
        lines.add("导入批次：" + run.batchId());
        lines.add("算法版本：" + run.algorithmVersion() + "  规则版本：" + run.ruleVersion());
        lines.add("契约版本：" + run.contractVersion());
        lines.add("导出操作者：" + operator + "  导出时间：" + OffsetDateTime.now());
        lines.add("报警总数：" + dashboard.total());
        lines.add("处置状态：" + dashboard.dispositionCounts());
        lines.add("优先级：" + dashboard.priorityCounts());
        lines.add("区域：" + dashboard.areaCounts());
        lines.add("单元：" + dashboard.unitCounts());
        lines.add("噪声类型：" + dashboard.noiseTypeCounts());
        lines.add("原因类别：" + dashboard.causeCategoryCounts());
        lines.add("关联事件链数：" + chainCount);
        lines.add("趋势：");
        for (TrendPoint point : dashboard.trend()) {
            lines.add("  " + point.bucket() + "  " + point.count());
        }
        try (PDDocument document = new PDDocument();
                InputStream fontStream = ReportService.class.getResourceAsStream(FONT_RESOURCE);
                ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            if (fontStream == null) {
                throw new IllegalStateException("报告中文字体资源缺失");
            }
            PDType0Font font = PDType0Font.load(document, fontStream, true);
            PDPage page = null;
            PDPageContentStream content = null;
            int lineOnPage = 0;
            try {
                for (String line : lines) {
                    if (content == null || lineOnPage >= 42) {
                        if (content != null) {
                            content.endText();
                            content.close();
                        }
                        page = new PDPage(PDRectangle.A4);
                        document.addPage(page);
                        content = new PDPageContentStream(document, page);
                        content.beginText();
                        content.setFont(font, 11);
                        content.setLeading(18);
                        content.newLineAtOffset(45, 800);
                        lineOnPage = 0;
                    }
                    content.showText(line);
                    content.newLine();
                    lineOnPage++;
                }
            } finally {
                if (content != null) {
                    content.endText();
                    content.close();
                }
            }
            document.save(output);
            return output.toByteArray();
        } catch (IOException exception) {
            throw new IllegalStateException("PDF 报告生成失败", exception);
        }
    }

    private byte[] xlsx(RunReportInfo run, DashboardView dashboard, List<ReportAlarmRow> alarms,
            List<ReportChainRow> chains, List<ReportDispositionRow> dispositions, String operator) {
        SXSSFWorkbook workbook = new SXSSFWorkbook(100);
        workbook.setCompressTempFiles(true);
        try (workbook; ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            summarySheet(workbook, run, dashboard, operator);
            alarmSheet(workbook, alarms);
            chainSheet(workbook, chains);
            dispositionSheet(workbook, dispositions);
            workbook.write(output);
            return output.toByteArray();
        } catch (IOException exception) {
            throw new IllegalStateException("XLSX 报告生成失败", exception);
        } finally {
            workbook.dispose();
        }
    }

    private void summarySheet(SXSSFWorkbook workbook, RunReportInfo run, DashboardView dashboard, String operator) {
        Sheet sheet = workbook.createSheet("概要");
        int row = 0;
        row = pair(sheet, row, "产品标识", IDENTITY);
        row = pair(sheet, row, "数据声明", SYNTHETIC_NOTICE);
        row = pair(sheet, row, "分析运行", run.runId());
        row = pair(sheet, row, "导入批次", run.batchId());
        row = pair(sheet, row, "契约版本", run.contractVersion());
        row = pair(sheet, row, "算法版本", run.algorithmVersion());
        row = pair(sheet, row, "规则版本", run.ruleVersion());
        row = pair(sheet, row, "导出操作者", operator);
        row = pair(sheet, row, "导出时间", OffsetDateTime.now());
        row = pair(sheet, row, "报警总数", dashboard.total());
        pair(sheet, row++, "处置状态", dashboard.dispositionCounts());
        pair(sheet, row++, "优先级", dashboard.priorityCounts());
        pair(sheet, row++, "区域", dashboard.areaCounts());
        pair(sheet, row++, "单元", dashboard.unitCounts());
        pair(sheet, row++, "噪声类型", dashboard.noiseTypeCounts());
        pair(sheet, row, "原因类别", dashboard.causeCategoryCounts());
        sheet.setColumnWidth(0, 20 * 256);
        sheet.setColumnWidth(1, 100 * 256);
    }

    private void alarmSheet(SXSSFWorkbook workbook, List<ReportAlarmRow> alarms) {
        Sheet sheet = workbook.createSheet("报警明细");
        String[] headers = {"record_id", "source_row", "event_time", "site", "area", "unit", "tag",
            "description", "priority", "state", "算法noise_type", "当前noise_type", "算法alarm_class",
            "当前alarm_class", "算法cause_category", "当前cause_category", "score", "disposition_status"};
        writeRow(sheet.createRow(0), (Object[]) headers);
        int index = 1;
        for (ReportAlarmRow alarm : alarms) {
            writeRow(sheet.createRow(index++), alarm.recordId(), alarm.sourceRow(), alarm.eventTime(), alarm.site(),
                    alarm.area(), alarm.unit(), alarm.tag(), alarm.description(), alarm.priority(), alarm.state(),
                    alarm.algorithmNoiseType(), alarm.currentNoiseType(), alarm.algorithmAlarmClass(),
                    alarm.currentAlarmClass(), alarm.algorithmCauseCategory(), alarm.currentCauseCategory(),
                    alarm.score(), alarm.dispositionStatus());
        }
    }

    private void chainSheet(SXSSFWorkbook workbook, List<ReportChainRow> chains) {
        Sheet sheet = workbook.createSheet("关联事件链");
        writeRow(sheet.createRow(0), "chain_id", "member_order", "record_id", "source_row", "start_time",
                "end_time", "association_rule", "explanation");
        int index = 1;
        for (ReportChainRow chain : chains) {
            writeRow(sheet.createRow(index++), chain.chainId(), chain.memberOrder(), chain.recordId(),
                    chain.sourceRow(), chain.startTime(), chain.endTime(), chain.associationRule(),
                    chain.explanation());
        }
    }

    private void dispositionSheet(SXSSFWorkbook workbook, List<ReportDispositionRow> dispositions) {
        Sheet sheet = workbook.createSheet("处置历史");
        writeRow(sheet.createRow(0), "record_id", "source_row", "from_status", "to_status", "operator",
                "note", "occurred_at");
        int index = 1;
        for (ReportDispositionRow disposition : dispositions) {
            writeRow(sheet.createRow(index++), disposition.recordId(), disposition.sourceRow(),
                    disposition.fromStatus(), disposition.toStatus(), disposition.operator(), disposition.note(),
                    disposition.occurredAt());
        }
    }

    private int pair(Sheet sheet, int rowIndex, String label, Object value) {
        writeRow(sheet.createRow(rowIndex), label, value);
        return rowIndex + 1;
    }

    private void writeRow(Row row, Object... values) {
        for (int index = 0; index < values.length; index++) {
            Cell cell = row.createCell(index);
            Object value = values[index];
            if (value instanceof Number number) {
                cell.setCellValue(number.doubleValue());
            } else {
                cell.setCellValue(value == null ? "" : value.toString());
            }
        }
    }

    private RunReportInfo runInfo(UUID runId) {
        return jdbcTemplate.queryForObject("""
                SELECT run_id, batch_id, contract_version, algorithm_version, rule_version
                  FROM analysis_run WHERE run_id = ? AND status = 'COMPLETED'
                """, (resultSet, rowNumber) -> new RunReportInfo(
                resultSet.getObject("run_id", UUID.class), resultSet.getObject("batch_id", UUID.class),
                resultSet.getString("contract_version"), resultSet.getString("algorithm_version"),
                resultSet.getString("rule_version")), runId);
    }

    private List<ReportAlarmRow> reportAlarms(UUID runId) {
        return jdbcTemplate.query("""
                SELECT a.record_id, a.source_row, a.event_time, a.site, a.area, a.unit_name, a.tag,
                       a.description, a.priority, a.alarm_state, r.noise_type, r.alarm_class,
                       r.cause_category, COALESCE(o.noise_type, r.noise_type) AS current_noise_type,
                       COALESCE(o.alarm_class, r.alarm_class) AS current_alarm_class,
                       COALESCE(o.cause_category, r.cause_category) AS current_cause_category,
                       r.score, COALESCE(d.status, 'OPEN') AS disposition_status
                  FROM analysis_result r
                  JOIN alarm_record a ON a.record_id = r.record_id
                  LEFT JOIN analysis_result_override o
                    ON o.run_id = r.run_id AND o.record_id = r.record_id
                  LEFT JOIN alarm_disposition d
                    ON d.run_id = r.run_id AND d.record_id = r.record_id
                 WHERE r.run_id = ? ORDER BY a.source_row
                """, (resultSet, rowNumber) -> new ReportAlarmRow(
                resultSet.getObject("record_id", UUID.class), resultSet.getInt("source_row"),
                resultSet.getObject("event_time", OffsetDateTime.class), resultSet.getString("site"),
                resultSet.getString("area"), resultSet.getString("unit_name"), resultSet.getString("tag"),
                resultSet.getString("description"), resultSet.getString("priority"),
                resultSet.getString("alarm_state"), resultSet.getString("noise_type"),
                resultSet.getString("current_noise_type"), resultSet.getString("alarm_class"),
                resultSet.getString("current_alarm_class"), resultSet.getString("cause_category"),
                resultSet.getString("current_cause_category"), resultSet.getBigDecimal("score"),
                resultSet.getString("disposition_status")), runId);
    }

    private List<ReportChainRow> reportChains(UUID runId) {
        return jdbcTemplate.query("""
                SELECT c.chain_id, m.member_order, m.record_id, a.source_row, c.start_time, c.end_time,
                       c.association_rule, c.explanation
                  FROM event_chain c
                  JOIN event_chain_member m ON m.run_id = c.run_id AND m.chain_id = c.chain_id
                  JOIN alarm_record a ON a.record_id = m.record_id
                 WHERE c.run_id = ? ORDER BY c.start_time, c.chain_id, m.member_order
                """, (resultSet, rowNumber) -> new ReportChainRow(
                resultSet.getString("chain_id"), resultSet.getInt("member_order"),
                resultSet.getObject("record_id", UUID.class), resultSet.getInt("source_row"),
                resultSet.getObject("start_time", OffsetDateTime.class),
                resultSet.getObject("end_time", OffsetDateTime.class),
                resultSet.getString("association_rule"), resultSet.getString("explanation")), runId);
    }

    private List<ReportDispositionRow> reportDispositions(UUID runId) {
        return jdbcTemplate.query("""
                SELECT h.record_id, a.source_row, h.from_status, h.to_status,
                       h.operator_name, h.note, h.occurred_at
                  FROM disposition_history h
                  JOIN alarm_record a ON a.record_id = h.record_id
                 WHERE h.run_id = ? ORDER BY h.occurred_at, h.history_id
                """, (resultSet, rowNumber) -> new ReportDispositionRow(
                resultSet.getObject("record_id", UUID.class), resultSet.getInt("source_row"),
                resultSet.getString("from_status"), resultSet.getString("to_status"),
                resultSet.getString("operator_name"), resultSet.getString("note"),
                resultSet.getObject("occurred_at", OffsetDateTime.class)), runId);
    }

    private String requiredOperator(ReportRequest request) {
        if (request == null || request.operator() == null || request.operator().isBlank()) {
            throw new BusinessApiException(HttpStatus.BAD_REQUEST, "REPORT_REQUEST_INVALID", "operator 不能为空");
        }
        String operator = request.operator().trim();
        if (operator.length() > 100) {
            throw new BusinessApiException(HttpStatus.BAD_REQUEST, "REPORT_REQUEST_INVALID",
                    "operator 长度不能超过 100");
        }
        return operator;
    }

    private record RunReportInfo(
            UUID runId, UUID batchId, String contractVersion, String algorithmVersion, String ruleVersion) {
    }

    private record ReportAlarmRow(
            UUID recordId, int sourceRow, OffsetDateTime eventTime, String site, String area, String unit,
            String tag, String description, String priority, String state, String algorithmNoiseType,
            String currentNoiseType, String algorithmAlarmClass, String currentAlarmClass,
            String algorithmCauseCategory, String currentCauseCategory, BigDecimal score,
            String dispositionStatus) {
    }

    private record ReportChainRow(
            String chainId, int memberOrder, UUID recordId, int sourceRow, OffsetDateTime startTime,
            OffsetDateTime endTime, String associationRule, String explanation) {
    }

    private record ReportDispositionRow(
            UUID recordId, int sourceRow, String fromStatus, String toStatus, String operator,
            String note, OffsetDateTime occurredAt) {
    }
}
