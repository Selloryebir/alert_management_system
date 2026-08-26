package com.alertmanagement.backend.audit;

import com.alertmanagement.backend.api.BusinessApiException;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

@Service
public class AuditService {

    public static final String DEMO_OPERATOR = "demo-reviewer";
    private static final Set<String> EVENT_TYPES = Set.of(
            "IMPORT_CREATED", "IMPORT_REJECTED", "IMPORT_CONFIRMED",
            "ANALYSIS_STARTED", "ANALYSIS_COMPLETED", "ANALYSIS_FAILED",
            "RESULT_OVERRIDDEN", "DISPOSITION_CHANGED", "REPORT_EXPORTED",
            "PROJECT_CREATED", "PROJECT_UPDATED", "PROJECT_ARCHIVED", "PROJECT_RESTORED", "PROJECT_DELETED",
            "MANUAL_ALARM_CREATED", "MANUAL_ALARM_UPDATED", "MANUAL_ALARM_INVALIDATED");
    private static final Set<String> TARGET_TYPES = Set.of(
            "IMPORT_BATCH", "ANALYSIS_RUN", "ALARM_RECORD", "PROJECT");

    private final JdbcTemplate jdbcTemplate;
    private final ObjectMapper objectMapper;

    public AuditService(JdbcTemplate jdbcTemplate, ObjectMapper objectMapper) {
        this.jdbcTemplate = jdbcTemplate;
        this.objectMapper = objectMapper;
    }

    public UUID record(String eventType, String operator, String targetType, UUID targetId,
            String result, Map<String, ?> details) {
        UUID eventId = UUID.randomUUID();
        jdbcTemplate.update("""
                INSERT INTO audit_event (
                    event_id, event_type, operator_name, target_type, target_id, result, trace_id, details
                ) VALUES (?, ?, ?, ?, ?, ?, ?, CAST(? AS jsonb))
                """, eventId, eventType, operator, targetType, targetId, result,
                UUID.randomUUID(), writeJson(details));
        return eventId;
    }

    public AuditPage list(int page, int size, String eventType, String targetType, UUID targetId) {
        if (page < 0 || size < 1 || size > 200) {
            throw badRequest("page 必须大于等于 0，size 必须在 1 到 200 之间");
        }
        if (eventType != null && !EVENT_TYPES.contains(eventType)) {
            throw badRequest("event_type 过滤值非法");
        }
        if (targetType != null && !TARGET_TYPES.contains(targetType)) {
            throw badRequest("target_type 过滤值非法");
        }
        StringBuilder where = new StringBuilder(" WHERE 1 = 1");
        List<Object> arguments = new ArrayList<>();
        addFilter(where, arguments, "event_type = ?", eventType);
        addFilter(where, arguments, "target_type = ?", targetType);
        if (targetId != null) {
            where.append(" AND target_id = ?");
            arguments.add(targetId);
        }
        long total = jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM audit_event" + where, Long.class, arguments.toArray());
        List<Object> pageArguments = new ArrayList<>(arguments);
        pageArguments.add(size);
        pageArguments.add((long) page * size);
        List<AuditEventView> items = jdbcTemplate.query("""
                SELECT event_id, event_type, occurred_at, operator_name, target_type,
                       target_id, result, trace_id, details::text
                  FROM audit_event
                """ + where + " ORDER BY occurred_at DESC, event_id DESC LIMIT ? OFFSET ?",
                (resultSet, rowNumber) -> new AuditEventView(
                        resultSet.getObject("event_id", UUID.class), resultSet.getString("event_type"),
                        resultSet.getObject("occurred_at", OffsetDateTime.class),
                        resultSet.getString("operator_name"), resultSet.getString("target_type"),
                        resultSet.getObject("target_id", UUID.class), resultSet.getString("result"),
                        resultSet.getObject("trace_id", UUID.class), readDetails(resultSet.getString("details"))),
                pageArguments.toArray());
        return new AuditPage(page, size, total, items);
    }

    private void addFilter(StringBuilder where, List<Object> arguments, String condition, String value) {
        if (value != null) {
            where.append(" AND ").append(condition);
            arguments.add(value);
        }
    }

    private String writeJson(Map<String, ?> details) {
        try {
            return objectMapper.writeValueAsString(details);
        } catch (JsonProcessingException exception) {
            throw new IllegalStateException("审计详情 JSON 序列化失败", exception);
        }
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> readDetails(String value) {
        try {
            return objectMapper.readValue(value, Map.class);
        } catch (JsonProcessingException exception) {
            throw new IllegalStateException("审计详情 JSON 反序列化失败", exception);
        }
    }

    private BusinessApiException badRequest(String message) {
        return new BusinessApiException(HttpStatus.BAD_REQUEST, "AUDIT_REQUEST_INVALID", message);
    }
}
