package com.alertmanagement.backend.audit;

import com.alertmanagement.backend.api.BusinessApiException;
import com.alertmanagement.backend.security.Actor;
import com.alertmanagement.backend.security.CurrentActor;
import com.alertmanagement.backend.security.ProjectAccessService;
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
    private static final Set<String> EVENT_TYPES = Set.of(
            "IMPORT_CREATED", "IMPORT_REJECTED", "IMPORT_CONFIRMED",
            "ANALYSIS_STARTED", "ANALYSIS_COMPLETED", "ANALYSIS_FAILED",
            "RESULT_OVERRIDDEN", "DISPOSITION_CHANGED", "REPORT_EXPORTED",
            "PROJECT_CREATED", "PROJECT_UPDATED", "PROJECT_ARCHIVED", "PROJECT_RESTORED", "PROJECT_DELETED",
            "MANUAL_ALARM_CREATED", "MANUAL_ALARM_UPDATED", "MANUAL_ALARM_INVALIDATED",
            "LOGIN_SUCCEEDED", "LOGIN_FAILED", "LOGOUT", "PASSWORD_CHANGED",
            "USER_CREATED", "USER_UPDATED", "USER_PASSWORD_RESET",
            "PROJECT_MEMBER_ADDED", "PROJECT_MEMBER_UPDATED", "PROJECT_MEMBER_REMOVED", "DEMO_RESET");
    private static final Set<String> TARGET_TYPES = Set.of(
            "IMPORT_BATCH", "ANALYSIS_RUN", "ALARM_RECORD", "PROJECT", "USER", "SYSTEM");

    private final JdbcTemplate jdbcTemplate;
    private final ObjectMapper objectMapper;
    private final CurrentActor currentActor;
    private final ProjectAccessService accessService;

    public AuditService(JdbcTemplate jdbcTemplate, ObjectMapper objectMapper,
            CurrentActor currentActor, ProjectAccessService accessService) {
        this.jdbcTemplate = jdbcTemplate;
        this.objectMapper = objectMapper;
        this.currentActor = currentActor;
        this.accessService = accessService;
    }

    public UUID record(String eventType, String targetType, UUID targetId, UUID projectId,
            String result, Map<String, ?> details) {
        Actor actor = currentActor.require();
        return insert(eventType, actor.userId(), actor.displayName(), targetType, targetId, projectId, result, details);
    }

    public UUID recordAnonymous(String eventType, String operator, String targetType, UUID targetId,
            String result, Map<String, ?> details) {
        return insert(eventType, null, operator, targetType, targetId, null, result, details);
    }

    private UUID insert(String eventType, UUID actorUserId, String operator, String targetType, UUID targetId,
            UUID projectId, String result, Map<String, ?> details) {
        UUID eventId = UUID.randomUUID();
        jdbcTemplate.update("""
                INSERT INTO audit_event (
                    event_id, event_type, operator_name, target_type, target_id, result, trace_id,
                    details, actor_user_id, project_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, CAST(? AS jsonb), ?, ?)
                """, eventId, eventType, operator, targetType, targetId, result,
                UUID.randomUUID(), writeJson(details), actorUserId, projectId);
        return eventId;
    }

    public AuditPage list(int page, int size, String eventType, String targetType, UUID targetId, UUID projectId) {
        if (page < 0 || size < 1 || size > 200) {
            throw badRequest("page 必须大于等于 0，size 必须在 1 到 200 之间");
        }
        if (eventType != null && !EVENT_TYPES.contains(eventType)) {
            throw badRequest("event_type 过滤值非法");
        }
        if (targetType != null && !TARGET_TYPES.contains(targetType)) {
            throw badRequest("target_type 过滤值非法");
        }
        Actor actor = currentActor.require();
        StringBuilder where = new StringBuilder(" WHERE 1 = 1");
        List<Object> arguments = new ArrayList<>();
        if (!actor.systemAdmin()) {
            if (projectId != null) {
                accessService.requireManager(projectId);
                where.append(" AND project_id = ?");
                arguments.add(projectId);
            } else {
                where.append(" AND project_id IN (SELECT project_id FROM project_membership"
                        + " WHERE user_id = ? AND project_role = 'MANAGER')");
                arguments.add(actor.userId());
            }
        } else if (projectId != null) {
            accessService.requireRead(projectId);
            where.append(" AND project_id = ?");
            arguments.add(projectId);
        }
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
                       target_id, result, trace_id, details::text, actor_user_id, project_id
                  FROM audit_event
                """ + where + " ORDER BY occurred_at DESC, event_id DESC LIMIT ? OFFSET ?",
                (resultSet, rowNumber) -> new AuditEventView(
                        resultSet.getObject("event_id", UUID.class), resultSet.getString("event_type"),
                        resultSet.getObject("occurred_at", OffsetDateTime.class),
                        resultSet.getString("operator_name"), resultSet.getString("target_type"),
                        resultSet.getObject("target_id", UUID.class), resultSet.getString("result"),
                        resultSet.getObject("trace_id", UUID.class),
                        resultSet.getObject("actor_user_id", UUID.class),
                        resultSet.getObject("project_id", UUID.class), readDetails(resultSet.getString("details"))),
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
