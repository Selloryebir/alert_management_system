package com.alertmanagement.backend.project;

import com.alertmanagement.backend.api.BusinessApiException;
import com.alertmanagement.backend.audit.AuditService;
import com.alertmanagement.backend.security.ProjectAccessService;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.math.BigDecimal;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.OffsetDateTime;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import org.springframework.dao.EmptyResultDataAccessException;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
class ManualAlarmService {

    private static final Set<String> PRIORITIES = Set.of("P1", "P2", "P3", "P4");
    private static final Set<String> STATES = Set.of("ACTIVE", "RETURNED", "ACKNOWLEDGED");
    private static final TypeReference<Map<String, Object>> OBJECT_MAP = new TypeReference<>() { };

    private final JdbcTemplate jdbcTemplate;
    private final ObjectMapper objectMapper;
    private final AuditService auditService;
    private final ProjectService projectService;
    private final ProjectAccessService accessService;

    ManualAlarmService(JdbcTemplate jdbcTemplate, ObjectMapper objectMapper,
            AuditService auditService, ProjectService projectService, ProjectAccessService accessService) {
        this.jdbcTemplate = jdbcTemplate;
        this.objectMapper = objectMapper;
        this.auditService = auditService;
        this.projectService = projectService;
        this.accessService = accessService;
    }

    @Transactional
    ManualAlarmView create(UUID projectId, ManualAlarmRequest request) {
        projectService.requireActive(projectId);
        ManualData data = validate(request);
        UUID batchId = UUID.randomUUID();
        UUID recordId = UUID.randomUUID();
        Map<String, Object> original = snapshot(data);
        jdbcTemplate.update("""
                INSERT INTO import_batch (
                    batch_id, project_id, file_name, file_format, source_type, status,
                    total_rows, valid_rows, error_count, headers, field_mapping, errors, imported_at
                ) VALUES (?, ?, 'MANUAL_ENTRY', 'CSV', 'MANUAL_ENTRY', 'IMPORTED',
                          1, 1, 0, '[]'::jsonb, '{}'::jsonb, '[]'::jsonb, CURRENT_TIMESTAMP)
                """, batchId, projectId);
        jdbcTemplate.update("""
                INSERT INTO alarm_record (
                    record_id, batch_id, source_row, event_time, return_time, ack_time,
                    site, area, unit_name, tag, description, priority, alarm_state,
                    alarm_value, threshold, engineering_unit, source_system, operator_name, raw_payload
                ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CAST(? AS jsonb))
                """, recordId, batchId, data.eventTime(), data.returnTime(), data.ackTime(), data.site(),
                data.area(), data.unit(), data.tag(), data.description(), data.priority(), data.state(),
                data.value(), data.threshold(), data.engineeringUnit(), data.sourceSystem(), data.operator(),
                writeJson(original));
        auditService.record("MANUAL_ALARM_CREATED", "ALARM_RECORD", recordId, projectId,
                "SUCCESS", Map.of("project_id", projectId, "batch_id", batchId, "original", original));
        return find(projectId, recordId);
    }

    List<ManualAlarmView> list(UUID projectId) {
        accessService.requireRead(projectId);
        projectService.get(projectId);
        return jdbcTemplate.query(manualAlarmSelect() + """
                 WHERE b.project_id=? AND b.source_type='MANUAL_ENTRY'
                 ORDER BY b.created_at DESC, a.record_id DESC
                """, (resultSet, rowNumber) -> manualAlarm(resultSet), projectId);
    }

    @Transactional
    ManualAlarmView update(UUID projectId, UUID recordId, ManualAlarmPatch patch) {
        projectService.requireActive(projectId);
        if (patch == null) {
            throw badRequest("请求体不能为空");
        }
        String reason = required(patch.reason(), "reason", 500);
        ManualAlarmView current = lock(projectId, recordId);
        rejectAnalyzed(recordId);
        rejectAnalyzedOrInvalidated(current);
        ManualData data = validate(new ManualAlarmRequest(
                patch.eventTime() == null ? current.eventTime() : patch.eventTime(),
                patch.returnTime() == null ? current.returnTime() : patch.returnTime(),
                patch.ackTime() == null ? current.ackTime() : patch.ackTime(),
                patch.site() == null ? current.site() : patch.site(),
                patch.area() == null ? current.area() : patch.area(),
                patch.unit() == null ? current.unit() : patch.unit(),
                patch.tag() == null ? current.tag() : patch.tag(),
                patch.description() == null ? current.description() : patch.description(),
                patch.priority() == null ? current.priority() : patch.priority(),
                patch.state() == null ? current.state() : patch.state(),
                patch.value() == null ? current.value() : patch.value(),
                patch.threshold() == null ? current.threshold() : patch.threshold(),
                patch.engineeringUnit() == null ? current.engineeringUnit() : patch.engineeringUnit(),
                patch.sourceSystem() == null ? current.sourceSystem() : patch.sourceSystem(),
                patch.operator() == null ? current.operator() : patch.operator()));
        Map<String, Object> before = snapshot(current);
        Map<String, Object> after = snapshot(data);
        if (before.equals(after)) {
            throw new BusinessApiException(HttpStatus.CONFLICT, "MANUAL_ALARM_NO_CHANGE", "修订值与当前记录相同");
        }
        jdbcTemplate.update("""
                UPDATE alarm_record SET event_time=?, return_time=?, ack_time=?, site=?, area=?, unit_name=?,
                       tag=?, description=?, priority=?, alarm_state=?, alarm_value=?, threshold=?,
                       engineering_unit=?, source_system=?, operator_name=?
                 WHERE record_id=?
                """, data.eventTime(), data.returnTime(), data.ackTime(), data.site(), data.area(), data.unit(),
                data.tag(), data.description(), data.priority(), data.state(), data.value(), data.threshold(),
                data.engineeringUnit(), data.sourceSystem(), data.operator(), recordId);
        auditService.record("MANUAL_ALARM_UPDATED", "ALARM_RECORD", recordId, projectId, "SUCCESS",
                Map.of("project_id", projectId, "before", before, "after", after, "reason", reason));
        return find(projectId, recordId);
    }

    @Transactional
    ManualAlarmView invalidate(UUID projectId, UUID recordId, ManualAlarmInvalidation request) {
        projectService.requireActive(projectId);
        if (request == null) {
            throw badRequest("请求体不能为空");
        }
        String operator = accessService.actor().displayName();
        String reason = required(request.reason(), "reason", 500);
        ManualAlarmView current = lock(projectId, recordId);
        if (current.invalidatedAt() != null) {
            throw new BusinessApiException(HttpStatus.CONFLICT, "MANUAL_ALARM_INVALIDATED", "报警已经作废");
        }
        jdbcTemplate.update("""
                UPDATE alarm_record SET invalidated_at=CURRENT_TIMESTAMP, invalidated_by=?, invalidation_reason=?
                 WHERE record_id=?
                """, operator, reason, recordId);
        auditService.record("MANUAL_ALARM_INVALIDATED", "ALARM_RECORD", recordId, projectId, "SUCCESS",
                Map.of("project_id", projectId, "reason", reason));
        return find(projectId, recordId);
    }

    private ManualAlarmView lock(UUID projectId, UUID recordId) {
        return find(projectId, recordId, " FOR UPDATE OF a");
    }

    private void rejectAnalyzed(UUID recordId) {
        Boolean analyzed = jdbcTemplate.queryForObject("""
                SELECT EXISTS (
                    SELECT 1 FROM analysis_run r JOIN alarm_record a ON a.batch_id=r.batch_id
                     WHERE a.record_id=?
                )
                """, Boolean.class, recordId);
        if (Boolean.TRUE.equals(analyzed)) {
            throw new BusinessApiException(HttpStatus.CONFLICT, "MANUAL_ALARM_ANALYZED", "已发起分析的人工报警不能修订");
        }
    }

    private void rejectAnalyzedOrInvalidated(ManualAlarmView current) {
        if (current.invalidatedAt() != null) {
            throw new BusinessApiException(HttpStatus.CONFLICT, "MANUAL_ALARM_INVALIDATED", "已作废报警不能修订");
        }
    }

    private ManualAlarmView find(UUID projectId, UUID recordId) {
        return find(projectId, recordId, "");
    }

    private ManualAlarmView find(UUID projectId, UUID recordId, String suffix) {
        try {
            return jdbcTemplate.queryForObject(manualAlarmSelect() + """
                     WHERE b.project_id=? AND b.source_type='MANUAL_ENTRY' AND a.record_id=?
                    """ + suffix, (resultSet, rowNumber) -> manualAlarm(resultSet),
                    projectId, recordId);
        } catch (EmptyResultDataAccessException exception) {
            throw new BusinessApiException(HttpStatus.NOT_FOUND, "MANUAL_ALARM_NOT_FOUND", "人工补录报警不存在");
        }
    }

    private String manualAlarmSelect() {
        return """
                SELECT b.project_id, a.batch_id, a.record_id, a.event_time, a.return_time, a.ack_time,
                       a.site, a.area, a.unit_name, a.tag, a.description, a.priority, a.alarm_state,
                       a.alarm_value, a.threshold, a.engineering_unit, a.source_system, a.operator_name,
                       a.raw_payload::text, a.invalidated_at, a.invalidated_by, a.invalidation_reason
                  FROM alarm_record a JOIN import_batch b ON b.batch_id=a.batch_id
                """;
    }

    private ManualAlarmView manualAlarm(ResultSet resultSet) throws SQLException {
        return new ManualAlarmView(
                resultSet.getObject("project_id", UUID.class), resultSet.getObject("batch_id", UUID.class),
                resultSet.getObject("record_id", UUID.class), resultSet.getObject("event_time", OffsetDateTime.class),
                resultSet.getObject("return_time", OffsetDateTime.class),
                resultSet.getObject("ack_time", OffsetDateTime.class), resultSet.getString("site"),
                resultSet.getString("area"), resultSet.getString("unit_name"), resultSet.getString("tag"),
                resultSet.getString("description"), resultSet.getString("priority"),
                resultSet.getString("alarm_state"), resultSet.getBigDecimal("alarm_value"),
                resultSet.getBigDecimal("threshold"), resultSet.getString("engineering_unit"),
                resultSet.getString("source_system"), resultSet.getString("operator_name"),
                readJson(resultSet.getString("raw_payload")),
                resultSet.getObject("invalidated_at", OffsetDateTime.class),
                resultSet.getString("invalidated_by"), resultSet.getString("invalidation_reason"));
    }

    private ManualData validate(ManualAlarmRequest request) {
        if (request == null) {
            throw badRequest("请求体不能为空");
        }
        if (request.eventTime() == null) {
            throw badRequest("event_time 不能为空");
        }
        if (request.returnTime() != null && request.returnTime().isBefore(request.eventTime())) {
            throw badRequest("return_time 不能早于 event_time");
        }
        if (request.ackTime() != null && request.ackTime().isBefore(request.eventTime())) {
            throw badRequest("ack_time 不能早于 event_time");
        }
        String priority = required(request.priority(), "priority", 2);
        String state = required(request.state(), "state", 20);
        if (!PRIORITIES.contains(priority)) {
            throw badRequest("priority 必须是 P1、P2、P3 或 P4");
        }
        if (!STATES.contains(state)) {
            throw badRequest("state 必须是 ACTIVE、RETURNED 或 ACKNOWLEDGED");
        }
        return new ManualData(request.eventTime(), request.returnTime(), request.ackTime(),
                required(request.site(), "site", 100), required(request.area(), "area", 100),
                optional(request.unit(), "unit", 100), required(request.tag(), "tag", 120),
                required(request.description(), "description", 500), priority, state, request.value(),
                request.threshold(), optional(request.engineeringUnit(), "engineering_unit", 40),
                required(request.sourceSystem(), "source_system", 100), optional(request.operator(), "operator", 100));
    }

    private String required(String value, String field, int maximumLength) {
        String result = optional(value, field, maximumLength);
        if (result == null) {
            throw badRequest(field + " 不能为空");
        }
        return result;
    }

    private String optional(String value, String field, int maximumLength) {
        if (value == null) {
            return null;
        }
        String result = value.trim();
        if (result.isEmpty()) {
            return null;
        }
        if (result.length() > maximumLength) {
            throw badRequest(field + " 长度不能超过 " + maximumLength);
        }
        return result;
    }

    private Map<String, Object> snapshot(ManualData data) {
        Map<String, Object> values = new LinkedHashMap<>();
        values.put("event_time", data.eventTime());
        values.put("return_time", data.returnTime());
        values.put("ack_time", data.ackTime());
        values.put("site", data.site());
        values.put("area", data.area());
        values.put("unit", data.unit());
        values.put("tag", data.tag());
        values.put("description", data.description());
        values.put("priority", data.priority());
        values.put("state", data.state());
        values.put("value", data.value());
        values.put("threshold", data.threshold());
        values.put("engineering_unit", data.engineeringUnit());
        values.put("source_system", data.sourceSystem());
        values.put("operator", data.operator());
        return values;
    }

    private Map<String, Object> snapshot(ManualAlarmView view) {
        return snapshot(new ManualData(view.eventTime(), view.returnTime(), view.ackTime(), view.site(), view.area(),
                view.unit(), view.tag(), view.description(), view.priority(), view.state(), view.value(),
                view.threshold(), view.engineeringUnit(), view.sourceSystem(), view.operator()));
    }

    private String writeJson(Object value) {
        try {
            return objectMapper.writeValueAsString(value);
        } catch (JsonProcessingException exception) {
            throw new IllegalStateException("人工报警 JSON 序列化失败", exception);
        }
    }

    private Map<String, Object> readJson(String value) {
        try {
            return objectMapper.readValue(value, OBJECT_MAP);
        } catch (JsonProcessingException exception) {
            throw new IllegalStateException("人工报警 JSON 反序列化失败", exception);
        }
    }

    private BusinessApiException badRequest(String message) {
        return new BusinessApiException(HttpStatus.BAD_REQUEST, "MANUAL_ALARM_REQUEST_INVALID", message);
    }

    private record ManualData(
            OffsetDateTime eventTime, OffsetDateTime returnTime, OffsetDateTime ackTime,
            String site, String area, String unit, String tag, String description, String priority,
            String state, BigDecimal value, BigDecimal threshold, String engineeringUnit,
            String sourceSystem, String operator) {
    }
}
