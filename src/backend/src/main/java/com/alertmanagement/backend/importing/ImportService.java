package com.alertmanagement.backend.importing;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.server.ResponseStatusException;

@Service
class ImportService {

    private static final TypeReference<LinkedHashMap<String, String>> MAPPING_TYPE = new TypeReference<>() { };

    private final ImportFileParser parser;
    private final AlarmNormalizer normalizer;
    private final ImportPersistenceService persistence;
    private final ObjectMapper objectMapper;

    ImportService(
            ImportFileParser parser,
            AlarmNormalizer normalizer,
            ImportPersistenceService persistence,
            ObjectMapper objectMapper) {
        this.parser = parser;
        this.normalizer = normalizer;
        this.persistence = persistence;
        this.objectMapper = objectMapper;
    }

    ImportBatchSummary preview(MultipartFile file, String mappingJson) {
        if (file == null || file.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "导入文件不能为空");
        }
        String fileName = file.getOriginalFilename();
        if (fileName == null || fileName.isBlank() || fileName.length() > 255) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "文件名不能为空且不能超过 255 个字符");
        }
        Map<String, String> mapping = parseMapping(mappingJson);
        SourceTable table = parser.parse(file);
        ValidatedImport validated = normalizer.normalize(table, mapping);
        return persistence.savePreview(fileName, validated);
    }

    ImportBatchSummary confirm(UUID batchId) {
        return persistence.confirm(batchId);
    }

    ImportBatchSummary get(UUID batchId) {
        return persistence.find(batchId);
    }

    List<ImportBatchSummary> list(int limit) {
        if (limit < 1 || limit > 100) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "limit 必须在 1 到 100 之间");
        }
        return persistence.list(limit);
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
}
