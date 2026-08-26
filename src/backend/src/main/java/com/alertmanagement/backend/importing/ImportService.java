package com.alertmanagement.backend.importing;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.nio.charset.StandardCharsets;
import com.alertmanagement.backend.api.BusinessApiException;
import com.alertmanagement.backend.project.ProjectService;
import com.alertmanagement.backend.project.ProjectValidationRules;
import com.alertmanagement.backend.security.ProjectAccessService;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.server.ResponseStatusException;

@Service
class ImportService {

    private static final TypeReference<LinkedHashMap<String, String>> MAPPING_TYPE = new TypeReference<>() { };
    private static final TypeReference<LinkedHashMap<Integer, LinkedHashMap<String, String>>> CORRECTIONS_TYPE =
            new TypeReference<>() { };

    private final ImportFileParser parser;
    private final AlarmNormalizer normalizer;
    private final ImportPersistenceService persistence;
    private final ObjectMapper objectMapper;
    private final ProjectService projectService;
    private final ProjectAccessService accessService;
    private final ImportParsingExecutor parsingExecutor;

    ImportService(
            ImportFileParser parser,
            AlarmNormalizer normalizer,
            ImportPersistenceService persistence,
            ObjectMapper objectMapper,
            ProjectService projectService,
            ProjectAccessService accessService,
            ImportParsingExecutor parsingExecutor) {
        this.parser = parser;
        this.normalizer = normalizer;
        this.persistence = persistence;
        this.objectMapper = objectMapper;
        this.projectService = projectService;
        this.accessService = accessService;
        this.parsingExecutor = parsingExecutor;
    }

    ImportBatchSummary preview(MultipartFile file, String mappingJson) {
        return preview(ProjectService.DEFAULT_PROJECT_ID, file, mappingJson, null);
    }

    ImportBatchSummary preview(UUID projectId, MultipartFile file, String mappingJson) {
        return preview(projectId, file, mappingJson, null);
    }

    ImportBatchSummary preview(UUID projectId, MultipartFile file, String mappingJson, String correctionsJson) {
        ProjectValidationRules rules = projectService.requireActive(projectId);
        if (file == null || file.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "导入文件不能为空");
        }
        if (file.getSize() > ImportLimits.MAX_FILE_BYTES) {
            throw tooLarge("IMPORT_FILE_TOO_LARGE", "单个导入文件不能超过 50 MiB");
        }
        String fileName = file.getOriginalFilename();
        if (fileName == null || fileName.isBlank() || fileName.length() > 255) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "文件名不能为空且不能超过 255 个字符");
        }
        requireJsonBytes(mappingJson, ImportLimits.MAX_MAPPING_BYTES, "字段映射 JSON 不能超过 32 KiB");
        requireJsonBytes(correctionsJson, ImportLimits.MAX_CORRECTIONS_BYTES, "行修正 JSON 不能超过 1 MiB");
        Map<String, String> mapping = parseMapping(mappingJson);
        SourceTable table = parsingExecutor.parse(parser, file);
        Map<Integer, Map<String, String>> corrections = parseCorrections(correctionsJson, table);
        ValidatedImport validated = normalizer.applyProjectRules(
                normalizer.normalize(table, mapping, corrections), rules);
        Set<Integer> actionableRows = new LinkedHashSet<>(corrections.keySet());
        validated.errors().stream().map(ImportError::sourceRow).forEach(actionableRows::add);
        if (actionableRows.size() > ImportLimits.MAX_ACTIONABLE_ROWS) {
            throw new BusinessApiException(HttpStatus.UNPROCESSABLE_ENTITY,
                    "IMPORT_OFFLINE_CORRECTION_REQUIRED", "可修正错误行超过 200 行，请离线修正后重新导入");
        }
        List<ImportSourceRow> sourceRows = table.rows().stream()
                .filter(row -> actionableRows.contains(row.sourceRow()))
                .map(row -> new ImportSourceRow(row.sourceRow(), Map.copyOf(row.values())))
                .toList();
        return persistence.savePreview(projectId, fileName, validated, corrections, sourceRows);
    }

    ImportBatchSummary confirm(UUID batchId) {
        accessService.requireBatch(batchId);
        return persistence.confirm(batchId);
    }

    ImportBatchSummary get(UUID batchId) {
        accessService.requireBatch(batchId);
        return persistence.find(batchId);
    }

    List<ImportBatchSummary> list(int limit) {
        return persistence.list(null, validatedLimit(limit));
    }

    List<ImportBatchSummary> list(UUID projectId, int limit) {
        accessService.requireRead(projectId);
        return persistence.list(projectId, validatedLimit(limit));
    }

    private int validatedLimit(int limit) {
        if (limit < 1 || limit > 100) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "limit 必须在 1 到 100 之间");
        }
        return limit;
    }

    ImportRecordPage records(UUID batchId, int page, int size) {
        accessService.requireBatch(batchId);
        if (page < 0) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "page 不能小于 0");
        }
        if (size < 1 || size > 200) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "size 必须在 1 到 200 之间");
        }
        return persistence.records(batchId, page, size);
    }

    private Map<String, String> parseMapping(String mappingJson) {
        if (mappingJson == null || mappingJson.isBlank()) {
            return Map.of();
        }
        try {
            return objectMapper.readValue(mappingJson, MAPPING_TYPE);
        } catch (JsonProcessingException exception) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "mapping 必须是目标字段到源表头的 JSON 对象");
        }
    }

    private Map<Integer, Map<String, String>> parseCorrections(String correctionsJson, SourceTable table) {
        if (correctionsJson == null || correctionsJson.isBlank()) {
            return Map.of();
        }
        final Map<Integer, LinkedHashMap<String, String>> parsed;
        try {
            parsed = objectMapper.readValue(correctionsJson, CORRECTIONS_TYPE);
        } catch (JsonProcessingException exception) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST,
                    "corrections 必须是源行号到目标字段修正文本的 JSON 对象");
        }
        if (parsed.size() > ImportLimits.MAX_CORRECTION_ROWS) {
            throw tooLarge("IMPORT_CORRECTIONS_TOO_LARGE", "行修正最多包含 1,000 行");
        }
        Set<Integer> sourceRows = new LinkedHashSet<>(
                table.rows().stream().map(SourceTable.SourceRow::sourceRow).toList());
        Map<Integer, Map<String, String>> validated = new LinkedHashMap<>();
        for (Map.Entry<Integer, LinkedHashMap<String, String>> rowEntry : parsed.entrySet()) {
            Integer sourceRow = rowEntry.getKey();
            if (sourceRow == null || !sourceRows.contains(sourceRow)) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST,
                        "corrections 包含不存在的源行号：" + sourceRow);
            }
            if (rowEntry.getValue() == null || rowEntry.getValue().isEmpty()) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST,
                        "corrections 的源行修正不能为空：" + sourceRow);
            }
            Map<String, String> fields = new LinkedHashMap<>();
            for (Map.Entry<String, String> fieldEntry : rowEntry.getValue().entrySet()) {
                String field = fieldEntry.getKey() == null ? "" : fieldEntry.getKey().trim();
                if (!normalizer.supportsField(field)) {
                    throw new ResponseStatusException(HttpStatus.BAD_REQUEST,
                            "corrections 包含未知目标字段：" + field);
                }
                if (fieldEntry.getValue() == null) {
                    throw new ResponseStatusException(HttpStatus.BAD_REQUEST,
                            "corrections 修正文本不能为 null：" + field);
                }
                fields.put(field, fieldEntry.getValue());
            }
            validated.put(sourceRow, Map.copyOf(fields));
        }
        return java.util.Collections.unmodifiableMap(validated);
    }

    private void requireJsonBytes(String value, int limit, String message) {
        if (value != null && value.getBytes(StandardCharsets.UTF_8).length > limit) {
            throw tooLarge("IMPORT_REQUEST_TOO_LARGE", message);
        }
    }

    private BusinessApiException tooLarge(String code, String message) {
        return new BusinessApiException(HttpStatus.PAYLOAD_TOO_LARGE, code, message);
    }
}
