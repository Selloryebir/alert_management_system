package com.alertmanagement.backend.importing;

import com.alertmanagement.backend.audit.AuditService;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Types;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.LinkedHashSet;
import java.util.Map;
import java.util.UUID;
import org.springframework.dao.EmptyResultDataAccessException;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.BatchPreparedStatementSetter;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

@Service
class ImportPersistenceService {

    private static final int PREVIEW_LIMIT = 20;
    private static final TypeReference<List<String>> STRING_LIST = new TypeReference<>() { };
    private static final TypeReference<Map<String, String>> STRING_MAP = new TypeReference<>() { };
    private static final TypeReference<List<ImportError>> ERROR_LIST = new TypeReference<>() { };

    private final JdbcTemplate jdbcTemplate;
    private final ObjectMapper objectMapper;
    private final AuditService auditService;

    ImportPersistenceService(JdbcTemplate jdbcTemplate, ObjectMapper objectMapper, AuditService auditService) {
        this.jdbcTemplate = jdbcTemplate;
        this.objectMapper = objectMapper;
        this.auditService = auditService;
    }

    @Transactional
    public ImportBatchSummary savePreview(String fileName, ValidatedImport validated) {
        UUID batchId = UUID.randomUUID();
        jdbcTemplate.update("""
                INSERT INTO import_batch (
                    batch_id, file_name, file_format, status, total_rows, valid_rows, error_count,
                    headers, field_mapping, errors
                ) VALUES (?, ?, ?, ?, ?, ?, ?, CAST(? AS jsonb), CAST(? AS jsonb), CAST(? AS jsonb))
                """,
                batchId,
                fileName,
                validated.format().name(),
                validated.status().name(),
                validated.totalRows(),
                validated.validRows(),
                validated.errors().size(),
                writeJson(validated.headers()),
                writeJson(validated.mapping()),
                writeJson(validated.errors()));

        if (validated.status() == ImportBatchStatus.READY) {
            insertStaging(batchId, validated.records());
        }
        auditService.record("IMPORT_CREATED", AuditService.DEMO_OPERATOR, "IMPORT_BATCH", batchId, "SUCCESS",
                Map.of("file_name", fileName, "format", validated.format().name(),
                        "record_count", validated.totalRows()));
        if (validated.status() == ImportBatchStatus.REJECTED) {
            auditService.record("IMPORT_REJECTED", AuditService.DEMO_OPERATOR, "IMPORT_BATCH", batchId, "FAILURE",
                    Map.of("error_count", validated.errors().size(), "error_codes",
                            new LinkedHashSet<>(validated.errors().stream().map(ImportError::code).toList())));
        }
        return find(batchId);
    }

    @Transactional
    public ImportBatchSummary confirm(UUID batchId) {
        LockedBatch batch = lockBatch(batchId);
        if (batch.status() == ImportBatchStatus.IMPORTED) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "批次已经确认导入，不能重复确认");
        }
        if (batch.status() != ImportBatchStatus.READY) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "只有 READY 批次可以确认导入");
        }

        int inserted = jdbcTemplate.update("""
                INSERT INTO alarm_record (
                    record_id, batch_id, source_row, event_time, return_time, ack_time,
                    site, area, unit_name, tag, description, priority, alarm_state,
                    alarm_value, threshold, engineering_unit, source_system, operator_name, raw_payload
                )
                SELECT record_id, batch_id, source_row, event_time, return_time, ack_time,
                       site, area, unit_name, tag, description, priority, alarm_state,
                       alarm_value, threshold, engineering_unit, source_system, operator_name, raw_payload
                  FROM import_staging
                 WHERE batch_id = ?
                 ORDER BY source_row
                """, batchId);
        if (inserted != batch.validRows()) {
            throw new IllegalStateException("暂存记录数与批次有效记录数不一致");
        }
        int updated = jdbcTemplate.update("""
                UPDATE import_batch
                   SET status = 'IMPORTED', imported_at = CURRENT_TIMESTAMP
                 WHERE batch_id = ? AND status = 'READY'
                """, batchId);
        if (updated != 1) {
            throw new IllegalStateException("批次状态更新失败");
        }
        auditService.record("IMPORT_CONFIRMED", AuditService.DEMO_OPERATOR, "IMPORT_BATCH", batchId, "SUCCESS",
                Map.of("success_count", inserted, "warning_count", 0));
        return find(batchId);
    }

    public ImportBatchSummary find(UUID batchId) {
        try {
            BatchRow row = jdbcTemplate.queryForObject("""
                    SELECT batch_id, file_name, file_format, status, total_rows, valid_rows, error_count,
                           headers::text, field_mapping::text, errors::text, created_at, imported_at
                      FROM import_batch
                     WHERE batch_id = ?
                    """, (resultSet, rowNumber) -> batchRow(resultSet), batchId);
            return summary(row, previewRows(batchId));
        } catch (EmptyResultDataAccessException exception) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "导入批次不存在");
        }
    }

    public List<ImportBatchSummary> list(int limit) {
        return jdbcTemplate.query("""
                SELECT batch_id, file_name, file_format, status, total_rows, valid_rows, error_count,
                       headers::text, field_mapping::text, errors::text, created_at, imported_at
                  FROM import_batch
                 ORDER BY created_at DESC, batch_id DESC
                 LIMIT ?
                """, (resultSet, rowNumber) -> summary(batchRow(resultSet), List.of()), limit);
    }

    public ImportRecordPage records(UUID batchId, int page, int size) {
        Boolean exists = jdbcTemplate.queryForObject(
                "SELECT EXISTS (SELECT 1 FROM import_batch WHERE batch_id = ?)", Boolean.class, batchId);
        if (!Boolean.TRUE.equals(exists)) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "导入批次不存在");
        }
        long total = jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM import_staging WHERE batch_id = ?", Long.class, batchId);
        long offset = (long) page * size;
        return new ImportRecordPage(recordRows(batchId, size, offset), total, page, size);
    }

    private LockedBatch lockBatch(UUID batchId) {
        try {
            return jdbcTemplate.queryForObject("""
                    SELECT status, valid_rows
                      FROM import_batch
                     WHERE batch_id = ?
                     FOR UPDATE
                    """, (resultSet, rowNumber) -> new LockedBatch(
                    ImportBatchStatus.valueOf(resultSet.getString("status")),
                    resultSet.getInt("valid_rows")), batchId);
        } catch (EmptyResultDataAccessException exception) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "导入批次不存在");
        }
    }

    private void insertStaging(UUID batchId, List<NormalizedAlarm> records) {
        jdbcTemplate.batchUpdate("""
                INSERT INTO import_staging (
                    record_id, batch_id, source_row, event_time, return_time, ack_time,
                    site, area, unit_name, tag, description, priority, alarm_state,
                    alarm_value, threshold, engineering_unit, source_system, operator_name, raw_payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CAST(? AS jsonb))
                """, new BatchPreparedStatementSetter() {
            @Override
            public void setValues(PreparedStatement statement, int index) throws SQLException {
                NormalizedAlarm record = records.get(index);
                statement.setObject(1, record.recordId());
                statement.setObject(2, batchId);
                statement.setInt(3, record.sourceRow());
                statement.setObject(4, record.eventTime());
                setNullable(statement, 5, record.returnTime(), Types.TIMESTAMP_WITH_TIMEZONE);
                setNullable(statement, 6, record.ackTime(), Types.TIMESTAMP_WITH_TIMEZONE);
                statement.setString(7, record.site());
                statement.setString(8, record.area());
                statement.setString(9, record.unit());
                statement.setString(10, record.tag());
                statement.setString(11, record.description());
                statement.setString(12, record.priority());
                statement.setString(13, record.state());
                statement.setBigDecimal(14, record.value());
                statement.setBigDecimal(15, record.threshold());
                statement.setString(16, record.engineeringUnit());
                statement.setString(17, record.sourceSystem());
                statement.setString(18, record.operator());
                statement.setString(19, writeJson(record.rawPayload()));
            }

            @Override
            public int getBatchSize() {
                return records.size();
            }
        });
    }

    private List<AlarmPreview> previewRows(UUID batchId) {
        return recordRows(batchId, PREVIEW_LIMIT, 0);
    }

    private List<AlarmPreview> recordRows(UUID batchId, int limit, long offset) {
        return jdbcTemplate.query("""
                SELECT source_row, event_time, return_time, ack_time, site, area, unit_name, tag,
                       description, priority, alarm_state, alarm_value, threshold, engineering_unit,
                       source_system, operator_name, raw_payload::text
                 FROM import_staging
                 WHERE batch_id = ?
                 ORDER BY source_row
                 LIMIT ?
                OFFSET ?
                """, (resultSet, rowNumber) -> new AlarmPreview(
                resultSet.getInt("source_row"),
                resultSet.getObject("event_time", OffsetDateTime.class),
                resultSet.getObject("return_time", OffsetDateTime.class),
                resultSet.getObject("ack_time", OffsetDateTime.class),
                resultSet.getString("site"),
                resultSet.getString("area"),
                resultSet.getString("unit_name"),
                resultSet.getString("tag"),
                resultSet.getString("description"),
                resultSet.getString("priority"),
                resultSet.getString("alarm_state"),
                resultSet.getBigDecimal("alarm_value"),
                resultSet.getBigDecimal("threshold"),
                resultSet.getString("engineering_unit"),
                resultSet.getString("source_system"),
                resultSet.getString("operator_name"),
                readJson(resultSet.getString("raw_payload"), STRING_MAP)), batchId, limit, offset);
    }

    private BatchRow batchRow(ResultSet resultSet) throws SQLException {
        return new BatchRow(
                resultSet.getObject("batch_id", UUID.class),
                resultSet.getString("file_name"),
                ImportFormat.valueOf(resultSet.getString("file_format")),
                ImportBatchStatus.valueOf(resultSet.getString("status")),
                resultSet.getInt("total_rows"),
                resultSet.getInt("valid_rows"),
                resultSet.getInt("error_count"),
                readJson(resultSet.getString("headers"), STRING_LIST),
                readJson(resultSet.getString("field_mapping"), STRING_MAP),
                readJson(resultSet.getString("errors"), ERROR_LIST),
                resultSet.getObject("created_at", OffsetDateTime.class),
                resultSet.getObject("imported_at", OffsetDateTime.class));
    }

    private ImportBatchSummary summary(BatchRow row, List<AlarmPreview> previewRows) {
        return new ImportBatchSummary(
                row.batchId(), row.fileName(), row.format(), row.status(), row.totalRows(), row.validRows(),
                row.errorCount(), row.headers(), row.mapping(), row.errors(), previewRows,
                row.createdAt(), row.importedAt());
    }

    private void setNullable(PreparedStatement statement, int index, Object value, int sqlType) throws SQLException {
        if (value == null) {
            statement.setNull(index, sqlType);
        } else {
            statement.setObject(index, value);
        }
    }

    private String writeJson(Object value) {
        try {
            return objectMapper.writeValueAsString(value);
        } catch (JsonProcessingException exception) {
            throw new IllegalStateException("导入数据 JSON 序列化失败", exception);
        }
    }

    private <T> T readJson(String value, TypeReference<T> type) {
        try {
            return objectMapper.readValue(value, type);
        } catch (JsonProcessingException exception) {
            throw new IllegalStateException("导入数据 JSON 反序列化失败", exception);
        }
    }

    private record LockedBatch(ImportBatchStatus status, int validRows) {
    }

    private record BatchRow(
            UUID batchId,
            String fileName,
            ImportFormat format,
            ImportBatchStatus status,
            int totalRows,
            int validRows,
            int errorCount,
            List<String> headers,
            Map<String, String> mapping,
            List<ImportError> errors,
            OffsetDateTime createdAt,
            OffsetDateTime importedAt) {
    }
}
