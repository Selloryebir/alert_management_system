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
import com.alertmanagement.backend.project.ProjectService;
import com.alertmanagement.backend.project.ProjectValidationRules;
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

    ImportService(
            ImportFileParser parser,
            AlarmNormalizer normalizer,
            ImportPersistenceService persistence,
            ObjectMapper objectMapper,
            ProjectService projectService) {
        this.parser = parser;
        this.normalizer = normalizer;
        this.persistence = persistence;
        this.objectMapper = objectMapper;
        this.projectService = projectService;
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
        String fileName = file.getOriginalFilename();
        if (fileName == null || fileName.isBlank() || fileName.length() > 255) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "文件名不能为空且不能超过 255 个字符");
        }
        Map<String, String> mapping = parseMapping(mappingJson);
        SourceTable table = parser.parse(file);
        Map<Integer, Map<String, String>> corrections = parseCorrections(correctionsJson, table);
        ValidatedImport validated = normalizer.applyProjectRules(
                normalizer.normalize(table, mapping, corrections), rules);
        Set<Integer> actionableRows = new LinkedHashSet<>(corrections.keySet());
        validated.errors().stream().map(ImportError::sourceRow).forEach(actionableRows::add);
        List<ImportSourceRow> sourceRows = table.rows().stream()
                .filter(row -> actionableRows.contains(row.sourceRow()))
                .map(row -> new ImportSourceRow(row.sourceRow(), Map.copyOf(row.values())))
                .toList();
        return persistence.savePreview(projectId, fileName, validated, corrections, sourceRows);
    }

    ImportBatchSummary confirm(UUID batchId) {
        return persistence.confirm(batchId);
    }

    ImportBatchSummary get(UUID batchId) {
        return persistence.find(batchId);
    }

    List<ImportBatchSummary> list(int limit) {
        return persistence.list(null, validatedLimit(limit));
    }

    List<ImportBatchSummary> list(UUID projectId, int limit) {
        projectService.get(projectId);
        return persistence.list(projectId, validatedLimit(limit));
    }

    private int validatedLimit(int limit) {
        if (limit < 1 || limit > 100) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "limit 必须在 1 到 100 之间");
        }
        return limit;
    }

    ImportRecordPage records(UUID batchId, int page, int size) {
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
}
