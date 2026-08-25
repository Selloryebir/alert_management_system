package com.alertmanagement.backend.analysis;

import com.alertmanagement.backend.api.BusinessApiException;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
class AnalysisWorkflowService {

    private static final String UNSPECIFIED_UNIT = "未指定单元";
    private static final Set<String> PRIORITIES = Set.of("P1", "P2", "P3", "P4");
    private static final Set<String> NOISE_TYPES = Set.of(
            "NORMAL", "DUPLICATE", "CHATTER", "SHORT_LIVED", "PERSISTENT");
    private static final Set<String> CAUSE_CATEGORIES = Set.of(
            "PROCESS_DISTURBANCE", "EQUIPMENT_FAULT", "INSTRUMENT_ISSUE", "MAINTENANCE_TEST", "UNKNOWN");
    private static final Set<String> DISPOSITION_STATUSES = Set.of("OPEN", "IN_PROGRESS", "CLOSED");
    private static final TypeReference<Map<String, String>> STRING_MAP = new TypeReference<>() { };
    private static final TypeReference<List<String>> STRING_LIST = new TypeReference<>() { };

    private final JdbcTemplate jdbcTemplate;
    private final ObjectMapper objectMapper;

    AnalysisWorkflowService(JdbcTemplate jdbcTemplate, ObjectMapper objectMapper) {
        this.jdbcTemplate = jdbcTemplate;
        this.objectMapper = objectMapper;
    }

    DashboardView dashboard(UUID runId) {
        RunIdentity run = requireCompleted(runId);
        long total = jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM analysis_result WHERE run_id = ?", Long.class, runId);
        Map<String, Long> dispositions = new LinkedHashMap<>();
        dispositions.put("OPEN", 0L);
        dispositions.put("IN_PROGRESS", 0L);
        dispositions.put("CLOSED", 0L);
        dispositions.putAll(counts("""
                SELECT COALESCE(d.status, 'OPEN') AS category, COUNT(*) AS amount
                  FROM analysis_result r
                  LEFT JOIN alarm_disposition d ON d.run_id = r.run_id AND d.record_id = r.record_id
                 WHERE r.run_id = ?
                 GROUP BY COALESCE(d.status, 'OPEN')
                 ORDER BY category
                """, runId));
        List<TrendPoint> trend = jdbcTemplate.query("""
                SELECT date_trunc('hour', a.event_time) AS bucket, COUNT(*) AS amount
                  FROM analysis_result r
                  JOIN alarm_record a ON a.record_id = r.record_id
                 WHERE r.run_id = ?
                 GROUP BY date_trunc('hour', a.event_time)
                 ORDER BY bucket
                """, (resultSet, rowNumber) -> new TrendPoint(
                resultSet.getObject("bucket", OffsetDateTime.class), resultSet.getLong("amount")), runId);
        return new DashboardView(runId, run.batchId(), total, dispositions, trend,
                counts("""
                        SELECT a.priority AS category, COUNT(*) AS amount
                          FROM analysis_result r JOIN alarm_record a ON a.record_id = r.record_id
                         WHERE r.run_id = ? GROUP BY a.priority ORDER BY category
                        """, runId),
                counts("""
                        SELECT a.area AS category, COUNT(*) AS amount
                          FROM analysis_result r JOIN alarm_record a ON a.record_id = r.record_id
                         WHERE r.run_id = ? GROUP BY a.area ORDER BY category
                        """, runId),
                counts("""
                        SELECT COALESCE(NULLIF(a.unit_name, ''), '未指定单元') AS category, COUNT(*) AS amount
                          FROM analysis_result r JOIN alarm_record a ON a.record_id = r.record_id
                         WHERE r.run_id = ?
                         GROUP BY COALESCE(NULLIF(a.unit_name, ''), '未指定单元') ORDER BY category
                        """, runId),
                counts("""
                        SELECT r.noise_type AS category, COUNT(*) AS amount
                          FROM analysis_result r WHERE r.run_id = ? GROUP BY r.noise_type ORDER BY category
                        """, runId),
                counts("""
                        SELECT r.cause_category AS category, COUNT(*) AS amount
                          FROM analysis_result r WHERE r.run_id = ? GROUP BY r.cause_category ORDER BY category
                        """, runId));
    }

    AlarmPage alarms(UUID runId, int page, int size, String priority, String area, String unit,
            String noiseType, String causeCategory, String dispositionStatus) {
        requireCompleted(runId);
        if (page < 0 || size < 1 || size > 200) {
            throw badRequest("page 必须大于等于 0，size 必须在 1 到 200 之间");
        }
        validateEnum("priority", priority, PRIORITIES);
        validateEnum("noise_type", noiseType, NOISE_TYPES);
        validateEnum("cause_category", causeCategory, CAUSE_CATEGORIES);
        validateEnum("disposition_status", dispositionStatus, DISPOSITION_STATUSES);
        validateTextFilter("area", area);
        validateTextFilter("unit", unit);

        StringBuilder where = new StringBuilder(" WHERE r.run_id = ?");
        List<Object> arguments = new ArrayList<>();
        arguments.add(runId);
        addFilter(where, arguments, "a.priority = ?", priority);
        addFilter(where, arguments, "a.area = ?", area);
        if (unit != null) {
            if (UNSPECIFIED_UNIT.equals(unit)) {
                where.append(" AND NULLIF(a.unit_name, '') IS NULL");
            } else {
                addFilter(where, arguments, "a.unit_name = ?", unit);
            }
        }
        addFilter(where, arguments, "r.noise_type = ?", noiseType);
        addFilter(where, arguments, "r.cause_category = ?", causeCategory);
        addFilter(where, arguments, "COALESCE(d.status, 'OPEN') = ?", dispositionStatus);

        String from = """
                 FROM analysis_result r
                 JOIN alarm_record a ON a.record_id = r.record_id
                 LEFT JOIN alarm_disposition d ON d.run_id = r.run_id AND d.record_id = r.record_id
                """;
        long total = jdbcTemplate.queryForObject(
                "SELECT COUNT(*)" + from + where, Long.class, arguments.toArray());
        List<Object> pageArguments = new ArrayList<>(arguments);
        pageArguments.add(size);
        pageArguments.add((long) page * size);
        List<AlarmItem> items = jdbcTemplate.query("""
                SELECT a.record_id, a.source_row, a.event_time, a.site, a.area, a.unit_name,
                       a.tag, a.description, a.priority, a.alarm_state,
                       r.noise_type, r.alarm_class, r.cause_category, r.score,
                       COALESCE(d.status, 'OPEN') AS disposition_status
                """ + from + where + " ORDER BY a.source_row LIMIT ? OFFSET ?",
                (resultSet, rowNumber) -> new AlarmItem(
                        resultSet.getObject("record_id", UUID.class), resultSet.getInt("source_row"),
                        resultSet.getObject("event_time", OffsetDateTime.class), resultSet.getString("site"),
                        resultSet.getString("area"), resultSet.getString("unit_name"), resultSet.getString("tag"),
                        resultSet.getString("description"), resultSet.getString("priority"),
                        resultSet.getString("alarm_state"), resultSet.getString("noise_type"),
                        resultSet.getString("alarm_class"), resultSet.getString("cause_category"),
                        resultSet.getBigDecimal("score"), resultSet.getString("disposition_status")),
                pageArguments.toArray());
        return new AlarmPage(runId, page, size, total, items);
    }

    AlarmDetail alarm(UUID runId, UUID recordId) {
        requireCompleted(runId);
        List<AlarmDetail> rows = jdbcTemplate.query("""
                SELECT a.record_id, a.source_row, a.event_time, a.return_time, a.ack_time,
                       a.site, a.area, a.unit_name, a.tag, a.description, a.priority, a.alarm_state,
                       a.alarm_value, a.threshold, a.engineering_unit, a.source_system,
                       a.operator_name AS alarm_operator, a.raw_payload::text,
                       r.noise_type, r.alarm_class, r.cause_category, r.score, r.evidence::text,
                       COALESCE(d.status, 'OPEN') AS disposition_status,
                       d.operator_name AS disposition_operator, d.note, d.updated_at, d.closed_at
                  FROM analysis_result r
                  JOIN alarm_record a ON a.record_id = r.record_id
                  LEFT JOIN alarm_disposition d ON d.run_id = r.run_id AND d.record_id = r.record_id
                 WHERE r.run_id = ? AND r.record_id = ?
                """, (resultSet, rowNumber) -> new AlarmDetail(
                resultSet.getObject("record_id", UUID.class), resultSet.getInt("source_row"),
                resultSet.getObject("event_time", OffsetDateTime.class), resultSet.getString("site"),
                resultSet.getString("area"), resultSet.getString("unit_name"), resultSet.getString("tag"),
                resultSet.getString("description"), resultSet.getString("priority"),
                resultSet.getString("alarm_state"), resultSet.getString("noise_type"),
                resultSet.getString("alarm_class"), resultSet.getString("cause_category"),
                resultSet.getBigDecimal("score"), resultSet.getString("disposition_status"),
                resultSet.getObject("return_time", OffsetDateTime.class),
                resultSet.getObject("ack_time", OffsetDateTime.class), resultSet.getBigDecimal("alarm_value"),
                resultSet.getBigDecimal("threshold"), resultSet.getString("engineering_unit"),
                resultSet.getString("source_system"), resultSet.getString("alarm_operator"),
                readJson(resultSet.getString("raw_payload"), STRING_MAP),
                readJson(resultSet.getString("evidence"), STRING_LIST),
                new DispositionView(resultSet.getString("disposition_status"),
                        resultSet.getString("disposition_operator"), resultSet.getString("note"),
                        resultSet.getObject("updated_at", OffsetDateTime.class),
                        resultSet.getObject("closed_at", OffsetDateTime.class)),
                dispositionHistory(runId, recordId), eventChains(runId, recordId)), runId, recordId);
        if (rows.isEmpty()) {
            throw notFound("该分析运行中不存在此报警记录");
        }
        return rows.getFirst();
    }

    @Transactional
    public DispositionView updateDisposition(UUID runId, UUID recordId, DispositionRequest request) {
        requireCompleted(runId);
        if (request == null) {
            throw badRequest("请求体不能为空");
        }
        String target = required(request.status(), "status", 20);
        String operator = required(request.operator(), "operator", 100);
        String note = required(request.note(), "note", 500);
        if (!DISPOSITION_STATUSES.contains(target)) {
            throw badRequest("status 必须是 OPEN、IN_PROGRESS 或 CLOSED");
        }
        List<UUID> records = jdbcTemplate.queryForList("""
                SELECT record_id FROM analysis_result
                 WHERE run_id = ? AND record_id = ?
                 FOR UPDATE
                """, UUID.class, runId, recordId);
        if (records.isEmpty()) {
            throw notFound("该分析运行中不存在此报警记录");
        }
        List<String> currentRows = jdbcTemplate.queryForList("""
                SELECT status FROM alarm_disposition
                 WHERE run_id = ? AND record_id = ?
                 FOR UPDATE
                """, String.class, runId, recordId);
        String current = currentRows.isEmpty() ? "OPEN" : currentRows.getFirst();
        if (!allowed(current, target)) {
            throw new BusinessApiException(HttpStatus.CONFLICT, "DISPOSITION_STATUS_CONFLICT",
                    "不允许从 " + current + " 流转到 " + target);
        }
        if (currentRows.isEmpty()) {
            jdbcTemplate.update("""
                    INSERT INTO alarm_disposition (
                        run_id, record_id, status, operator_name, note, updated_at, closed_at
                    ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP,
                              CASE WHEN ? = 'CLOSED' THEN CURRENT_TIMESTAMP ELSE NULL END)
                    """, runId, recordId, target, operator, note, target);
        } else {
            jdbcTemplate.update("""
                    UPDATE alarm_disposition
                       SET status = ?, operator_name = ?, note = ?, updated_at = CURRENT_TIMESTAMP,
                           closed_at = CASE WHEN ? = 'CLOSED' THEN CURRENT_TIMESTAMP ELSE NULL END
                     WHERE run_id = ? AND record_id = ?
                    """, target, operator, note, target, runId, recordId);
        }
        jdbcTemplate.update("""
                INSERT INTO disposition_history (
                    run_id, record_id, from_status, to_status, operator_name, note
                ) VALUES (?, ?, ?, ?, ?, ?)
                """, runId, recordId, current, target, operator, note);
        return disposition(runId, recordId);
    }

    private List<DispositionHistoryView> dispositionHistory(UUID runId, UUID recordId) {
        return jdbcTemplate.query("""
                SELECT from_status, to_status, operator_name, note, occurred_at
                  FROM disposition_history
                 WHERE run_id = ? AND record_id = ?
                 ORDER BY occurred_at, history_id
                """, (resultSet, rowNumber) -> new DispositionHistoryView(
                resultSet.getString("from_status"), resultSet.getString("to_status"),
                resultSet.getString("operator_name"), resultSet.getString("note"),
                resultSet.getObject("occurred_at", OffsetDateTime.class)), runId, recordId);
    }

    private List<AnalysisView.EventChain> eventChains(UUID runId, UUID recordId) {
        Map<String, List<AnalysisView.Member>> members = new LinkedHashMap<>();
        jdbcTemplate.query("""
                SELECT m.chain_id, m.record_id, a.source_row, m.member_order
                  FROM event_chain_member m
                  JOIN alarm_record a ON a.record_id = m.record_id
                 WHERE m.run_id = ? AND EXISTS (
                       SELECT 1 FROM event_chain_member selected
                        WHERE selected.run_id = m.run_id AND selected.chain_id = m.chain_id
                          AND selected.record_id = ?)
                 ORDER BY m.chain_id, m.member_order
                """, (org.springframework.jdbc.core.RowCallbackHandler) resultSet ->
                        members.computeIfAbsent(resultSet.getString("chain_id"), ignored -> new ArrayList<>())
                                .add(new AnalysisView.Member(resultSet.getObject("record_id", UUID.class),
                                        resultSet.getInt("source_row"), resultSet.getInt("member_order"))),
                runId, recordId);
        return jdbcTemplate.query("""
                SELECT c.chain_id, c.start_record_id, c.start_time, c.end_time,
                       c.association_rule, c.explanation
                  FROM event_chain c
                 WHERE c.run_id = ? AND EXISTS (
                       SELECT 1 FROM event_chain_member selected
                        WHERE selected.run_id = c.run_id AND selected.chain_id = c.chain_id
                          AND selected.record_id = ?)
                 ORDER BY c.start_time, c.chain_id
                """, (resultSet, rowNumber) -> new AnalysisView.EventChain(
                resultSet.getString("chain_id"), resultSet.getObject("start_record_id", UUID.class),
                resultSet.getObject("start_time", OffsetDateTime.class),
                resultSet.getObject("end_time", OffsetDateTime.class),
                resultSet.getString("association_rule"), resultSet.getString("explanation"),
                List.copyOf(members.getOrDefault(resultSet.getString("chain_id"), List.of()))), runId, recordId);
    }

    private DispositionView disposition(UUID runId, UUID recordId) {
        return jdbcTemplate.queryForObject("""
                SELECT status, operator_name, note, updated_at, closed_at
                  FROM alarm_disposition
                 WHERE run_id = ? AND record_id = ?
                """, (resultSet, rowNumber) -> new DispositionView(
                resultSet.getString("status"), resultSet.getString("operator_name"), resultSet.getString("note"),
                resultSet.getObject("updated_at", OffsetDateTime.class),
                resultSet.getObject("closed_at", OffsetDateTime.class)), runId, recordId);
    }

    private RunIdentity requireCompleted(UUID runId) {
        List<RunIdentity> rows = jdbcTemplate.query(
                "SELECT batch_id, status FROM analysis_run WHERE run_id = ?",
                (resultSet, rowNumber) -> new RunIdentity(
                        resultSet.getObject("batch_id", UUID.class), resultSet.getString("status")), runId);
        if (rows.isEmpty()) {
            throw notFound("分析运行不存在");
        }
        RunIdentity run = rows.getFirst();
        if (!"COMPLETED".equals(run.status())) {
            throw new BusinessApiException(HttpStatus.CONFLICT, "ANALYSIS_STATUS_CONFLICT",
                    "只有 COMPLETED 分析运行可以查询业务结果或处置");
        }
        return run;
    }

    private Map<String, Long> counts(String sql, UUID runId) {
        Map<String, Long> result = new LinkedHashMap<>();
        jdbcTemplate.query(sql, (org.springframework.jdbc.core.RowCallbackHandler) resultSet ->
                result.put(resultSet.getString("category"), resultSet.getLong("amount")), runId);
        return result;
    }

    private void validateEnum(String name, String value, Set<String> allowed) {
        if (value != null && !allowed.contains(value)) {
            throw badRequest(name + " 过滤值非法");
        }
    }

    private void validateTextFilter(String name, String value) {
        if (value != null && value.isBlank()) {
            throw badRequest(name + " 过滤值不能为空");
        }
    }

    private void addFilter(StringBuilder where, List<Object> arguments, String condition, String value) {
        if (value != null) {
            where.append(" AND ").append(condition);
            arguments.add(value);
        }
    }

    private String required(String value, String name, int maximumLength) {
        if (value == null || value.isBlank()) {
            throw badRequest(name + " 不能为空");
        }
        String trimmed = value.trim();
        if (trimmed.length() > maximumLength) {
            throw badRequest(name + " 长度不能超过 " + maximumLength);
        }
        return trimmed;
    }

    private boolean allowed(String from, String to) {
        return switch (from) {
            case "OPEN" -> "IN_PROGRESS".equals(to);
            case "IN_PROGRESS" -> "OPEN".equals(to) || "CLOSED".equals(to);
            case "CLOSED" -> "IN_PROGRESS".equals(to);
            default -> false;
        };
    }

    private BusinessApiException badRequest(String message) {
        return new BusinessApiException(HttpStatus.BAD_REQUEST, "ANALYSIS_REQUEST_INVALID", message);
    }

    private BusinessApiException notFound(String message) {
        return new BusinessApiException(HttpStatus.NOT_FOUND, "ANALYSIS_RESOURCE_NOT_FOUND", message);
    }

    private <T> T readJson(String value, TypeReference<T> type) {
        try {
            return objectMapper.readValue(value, type);
        } catch (JsonProcessingException exception) {
            throw new IllegalStateException("分析详情 JSON 反序列化失败", exception);
        }
    }

    private record RunIdentity(UUID batchId, String status) {
    }
}
