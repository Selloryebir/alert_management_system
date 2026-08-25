package com.alertmanagement.backend.analysis;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.dao.EmptyResultDataAccessException;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

@Service
class AnalysisPersistenceService {

    private static final TypeReference<Map<String, Object>> OBJECT_MAP = new TypeReference<>() { };
    private static final TypeReference<Map<String, String>> STRING_MAP = new TypeReference<>() { };
    private static final TypeReference<List<String>> STRING_LIST = new TypeReference<>() { };

    private final JdbcTemplate jdbcTemplate;
    private final ObjectMapper objectMapper;

    AnalysisPersistenceService(JdbcTemplate jdbcTemplate, ObjectMapper objectMapper) {
        this.jdbcTemplate = jdbcTemplate;
        this.objectMapper = objectMapper;
    }

    @Transactional
    public StartedAnalysis begin(UUID batchId, String contractVersion, String algorithmVersion,
            Map<String, Object> parameters) {
        String status;
        try {
            status = jdbcTemplate.queryForObject(
                    "SELECT status FROM import_batch WHERE batch_id = ? FOR UPDATE", String.class, batchId);
        } catch (EmptyResultDataAccessException exception) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "导入批次不存在");
        }
        if (!"IMPORTED".equals(status) && !"FAILED".equals(status)) {
            throw new ResponseStatusException(HttpStatus.CONFLICT,
                    "只有 IMPORTED 或 FAILED 批次可以开始分析，当前状态为 " + status);
        }

        int attempt = jdbcTemplate.queryForObject(
                "SELECT COALESCE(MAX(attempt), 0) + 1 FROM analysis_run WHERE batch_id = ?",
                Integer.class, batchId);
        UUID runId = UUID.randomUUID();
        jdbcTemplate.update("""
                INSERT INTO analysis_run (
                    run_id, batch_id, attempt, status, contract_version, algorithm_version, parameters
                ) VALUES (?, ?, ?, 'ANALYZING', ?, ?, CAST(? AS jsonb))
                """, runId, batchId, attempt, contractVersion, algorithmVersion, writeJson(parameters));
        int updated = jdbcTemplate.update(
                "UPDATE import_batch SET status = 'ANALYZING' WHERE batch_id = ? AND status IN ('IMPORTED', 'FAILED')",
                batchId);
        if (updated != 1) {
            throw new IllegalStateException("分析批次状态更新失败");
        }

        List<AlarmRecordRequest> records = jdbcTemplate.query("""
                SELECT record_id, batch_id, source_row, event_time, return_time, ack_time,
                       site, area, unit_name, tag, description, priority, alarm_state,
                       alarm_value, threshold, engineering_unit, source_system, operator_name, raw_payload::text
                  FROM alarm_record
                 WHERE batch_id = ?
                 ORDER BY source_row
                """, (resultSet, rowNumber) -> new AlarmRecordRequest(
                resultSet.getObject("record_id", UUID.class),
                resultSet.getObject("batch_id", UUID.class),
                resultSet.getInt("source_row"),
                resultSet.getObject("event_time", OffsetDateTime.class),
                resultSet.getObject("return_time", OffsetDateTime.class),
                resultSet.getObject("ack_time", OffsetDateTime.class),
                resultSet.getString("site"), resultSet.getString("area"), resultSet.getString("unit_name"),
                resultSet.getString("tag"), resultSet.getString("description"), resultSet.getString("priority"),
                resultSet.getString("alarm_state"), resultSet.getBigDecimal("alarm_value"),
                resultSet.getBigDecimal("threshold"), resultSet.getString("engineering_unit"),
                resultSet.getString("source_system"), resultSet.getString("operator_name"),
                readJson(resultSet.getString("raw_payload"), STRING_MAP)), batchId);
        if (records.isEmpty()) {
            throw new IllegalStateException("导入批次没有可分析记录");
        }
        return new StartedAnalysis(new AnalysisRequest(
                runId, contractVersion, algorithmVersion, Map.copyOf(parameters), List.copyOf(records)), attempt);
    }

    @Transactional
    public void complete(StartedAnalysis started, ValidatedAnalysis analysis) {
        UUID runId = started.request().analysisRunId();
        UUID batchId = started.request().records().getFirst().batchId();
        requireState(runId, batchId);

        List<Object[]> resultArguments = analysis.results().stream().map(result -> new Object[] {
            runId, result.recordId(), result.noiseType(), result.alarmClass(), result.causeCategory(),
            result.score(), writeJson(result.evidence())
        }).toList();
        jdbcTemplate.batchUpdate("""
                INSERT INTO analysis_result (
                    run_id, record_id, noise_type, alarm_class, cause_category, score, evidence
                ) VALUES (?, ?, ?, ?, ?, ?, CAST(? AS jsonb))
                """, resultArguments);

        for (AlgorithmEventChain chain : analysis.chains()) {
            jdbcTemplate.update("""
                    INSERT INTO event_chain (
                        run_id, chain_id, start_record_id, start_time, end_time, association_rule, explanation
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, runId, chain.chainId(), chain.startRecordId(), chain.startTime(), chain.endTime(),
                    chain.associationRule(), chain.explanation());
            List<Object[]> memberArguments = new ArrayList<>();
            for (int order = 0; order < chain.memberRecordIds().size(); order++) {
                memberArguments.add(new Object[] {runId, chain.chainId(), order, chain.memberRecordIds().get(order)});
            }
            jdbcTemplate.batchUpdate("""
                    INSERT INTO event_chain_member (run_id, chain_id, member_order, record_id)
                    VALUES (?, ?, ?, ?)
                    """, memberArguments);
        }

        int runUpdated = jdbcTemplate.update("""
                UPDATE analysis_run
                   SET status = 'COMPLETED', rule_version = ?, summary = CAST(? AS jsonb),
                       failure_reason = NULL, completed_at = CURRENT_TIMESTAMP
                 WHERE run_id = ? AND status = 'ANALYZING'
                """, analysis.ruleVersion(), writeJson(analysis.summary()), runId);
        int batchUpdated = jdbcTemplate.update(
                "UPDATE import_batch SET status = 'COMPLETED' WHERE batch_id = ? AND status = 'ANALYZING'", batchId);
        if (runUpdated != 1 || batchUpdated != 1) {
            throw new IllegalStateException("分析完成状态更新失败");
        }
    }

    @Transactional
    public void fail(UUID runId, UUID batchId, String reason) {
        jdbcTemplate.update("DELETE FROM event_chain_member WHERE run_id = ?", runId);
        jdbcTemplate.update("DELETE FROM event_chain WHERE run_id = ?", runId);
        jdbcTemplate.update("DELETE FROM analysis_result WHERE run_id = ?", runId);
        int runUpdated = jdbcTemplate.update("""
                UPDATE analysis_run
                   SET status = 'FAILED', failure_reason = ?, completed_at = CURRENT_TIMESTAMP
                 WHERE run_id = ? AND status = 'ANALYZING'
                """, abbreviate(reason), runId);
        int batchUpdated = jdbcTemplate.update(
                "UPDATE import_batch SET status = 'FAILED' WHERE batch_id = ? AND status = 'ANALYZING'", batchId);
        if (runUpdated != 1 || batchUpdated != 1) {
            throw new IllegalStateException("分析失败状态更新失败");
        }
    }

    public AnalysisView find(UUID runId) {
        RunRow run;
        try {
            run = jdbcTemplate.queryForObject("""
                    SELECT run_id, batch_id, attempt, status, contract_version, algorithm_version,
                           rule_version, parameters::text, summary::text, failure_reason, started_at, completed_at
                      FROM analysis_run
                     WHERE run_id = ?
                    """, (resultSet, rowNumber) -> runRow(resultSet), runId);
        } catch (EmptyResultDataAccessException exception) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "分析运行不存在");
        }
        List<AnalysisView.Result> results = jdbcTemplate.query("""
                SELECT r.record_id, a.source_row, r.noise_type, r.alarm_class, r.cause_category,
                       r.score, r.evidence::text
                  FROM analysis_result r
                  JOIN alarm_record a ON a.record_id = r.record_id
                 WHERE r.run_id = ?
                 ORDER BY a.source_row
                """, (resultSet, rowNumber) -> new AnalysisView.Result(
                resultSet.getObject("record_id", UUID.class), resultSet.getInt("source_row"),
                resultSet.getString("noise_type"), resultSet.getString("alarm_class"),
                resultSet.getString("cause_category"), resultSet.getBigDecimal("score"),
                readJson(resultSet.getString("evidence"), STRING_LIST)), runId);

        Map<String, List<AnalysisView.Member>> members = new LinkedHashMap<>();
        jdbcTemplate.query("""
                SELECT m.chain_id, m.record_id, a.source_row, m.member_order
                  FROM event_chain_member m
                  JOIN alarm_record a ON a.record_id = m.record_id
                 WHERE m.run_id = ?
                 ORDER BY m.chain_id, m.member_order
                """, (org.springframework.jdbc.core.RowCallbackHandler) resultSet -> {
                    members.computeIfAbsent(resultSet.getString("chain_id"), ignored -> new ArrayList<>())
                            .add(new AnalysisView.Member(resultSet.getObject("record_id", UUID.class),
                                    resultSet.getInt("source_row"), resultSet.getInt("member_order")));
                }, runId);
        List<AnalysisView.EventChain> chains = jdbcTemplate.query("""
                SELECT chain_id, start_record_id, start_time, end_time, association_rule, explanation
                  FROM event_chain
                 WHERE run_id = ?
                 ORDER BY chain_id
                """, (resultSet, rowNumber) -> new AnalysisView.EventChain(
                resultSet.getString("chain_id"), resultSet.getObject("start_record_id", UUID.class),
                resultSet.getObject("start_time", OffsetDateTime.class),
                resultSet.getObject("end_time", OffsetDateTime.class),
                resultSet.getString("association_rule"), resultSet.getString("explanation"),
                List.copyOf(members.getOrDefault(resultSet.getString("chain_id"), List.of()))), runId);

        AnalysisView.Summary summary = run.summaryJson() == null
                ? null : readJson(run.summaryJson(), AnalysisView.Summary.class);
        return new AnalysisView(run.runId(), run.batchId(), run.attempt(), run.status(), run.failureReason(),
                run.contractVersion(), run.algorithmVersion(), run.ruleVersion(), run.parameters(),
                results, chains, summary, run.startedAt(), run.completedAt());
    }

    private void requireState(UUID runId, UUID batchId) {
        String runStatus = jdbcTemplate.queryForObject(
                "SELECT status FROM analysis_run WHERE run_id = ? FOR UPDATE", String.class, runId);
        String batchStatus = jdbcTemplate.queryForObject(
                "SELECT status FROM import_batch WHERE batch_id = ? FOR UPDATE", String.class, batchId);
        if (!"ANALYZING".equals(runStatus) || !"ANALYZING".equals(batchStatus)) {
            throw new IllegalStateException("分析运行状态已改变");
        }
    }

    private RunRow runRow(ResultSet resultSet) throws SQLException {
        return new RunRow(resultSet.getObject("run_id", UUID.class),
                resultSet.getObject("batch_id", UUID.class), resultSet.getInt("attempt"),
                AnalysisStatus.valueOf(resultSet.getString("status")), resultSet.getString("contract_version"),
                resultSet.getString("algorithm_version"), resultSet.getString("rule_version"),
                readJson(resultSet.getString("parameters"), OBJECT_MAP), resultSet.getString("summary"),
                resultSet.getString("failure_reason"), resultSet.getObject("started_at", OffsetDateTime.class),
                resultSet.getObject("completed_at", OffsetDateTime.class));
    }

    private String abbreviate(String value) {
        return value.length() <= 500 ? value : value.substring(0, 500);
    }

    private String writeJson(Object value) {
        try {
            return objectMapper.writeValueAsString(value);
        } catch (JsonProcessingException exception) {
            throw new IllegalStateException("分析数据 JSON 序列化失败", exception);
        }
    }

    private <T> T readJson(String value, TypeReference<T> type) {
        try {
            return objectMapper.readValue(value, type);
        } catch (JsonProcessingException exception) {
            throw new IllegalStateException("分析数据 JSON 反序列化失败", exception);
        }
    }

    private <T> T readJson(String value, Class<T> type) {
        try {
            return objectMapper.readValue(value, type);
        } catch (JsonProcessingException exception) {
            throw new IllegalStateException("分析数据 JSON 反序列化失败", exception);
        }
    }

    private record RunRow(
            UUID runId,
            UUID batchId,
            int attempt,
            AnalysisStatus status,
            String contractVersion,
            String algorithmVersion,
            String ruleVersion,
            Map<String, Object> parameters,
            String summaryJson,
            String failureReason,
            OffsetDateTime startedAt,
            OffsetDateTime completedAt) {
    }
}
