package com.alertmanagement.backend.analysis;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.multipart;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.patch;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.sun.net.httpserver.HttpServer;
import io.zonky.test.db.postgres.embedded.EmbeddedPostgres;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Function;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestInstance;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;

@SpringBootTest
@AutoConfigureMockMvc
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class AnalysisIntegrationTest {

    private static final ObjectMapper JSON = new ObjectMapper();
    private static final EmbeddedPostgres POSTGRES = startPostgres();
    private static final AtomicReference<Function<JsonNode, StubResponse>> RESPONDER = new AtomicReference<>();
    private static final AtomicReference<JsonNode> LAST_REQUEST = new AtomicReference<>();
    private static final HttpServer ALGORITHM = startAlgorithm();

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private ObjectMapper objectMapper;

    @Autowired
    private JdbcTemplate jdbcTemplate;

    @DynamicPropertySource
    static void properties(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", () -> POSTGRES.getJdbcUrl("postgres", "postgres"));
        registry.add("spring.datasource.username", () -> "postgres");
        registry.add("spring.datasource.password", () -> "");
        registry.add("app.algorithm.analysis-url",
                () -> "http://127.0.0.1:" + ALGORITHM.getAddress().getPort() + "/api/v2/analyze");
        registry.add("app.algorithm.analysis-timeout", () -> "250ms");
    }

    @BeforeEach
    void cleanDatabase() {
        jdbcTemplate.execute("DROP TRIGGER IF EXISTS reject_analysis_completion ON import_batch");
        jdbcTemplate.execute("DROP FUNCTION IF EXISTS reject_analysis_completion()");
        jdbcTemplate.execute("TRUNCATE event_chain_member, event_chain, analysis_result, analysis_run, "
                + "alarm_record, import_staging, import_batch, audit_event CASCADE");
        RESPONDER.set(AnalysisIntegrationTest::successResponse);
        LAST_REQUEST.set(null);
    }

    @AfterAll
    void stopDependencies() throws IOException {
        ALGORITHM.stop(0);
        POSTGRES.close();
    }

    @Test
    void successfulAnalysisPersistsCompleteTraceAndSendsFrozenContract() throws Exception {
        UUID batchId = importedBatch();

        String body = mockMvc.perform(post("/api/v1/imports/{batchId}/analyses", batchId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("COMPLETED"))
                .andExpect(jsonPath("$.failure").doesNotExist())
                .andExpect(jsonPath("$.contract_version").value("v2"))
                .andExpect(jsonPath("$.algorithm_version").value("0.2.0"))
                .andExpect(jsonPath("$.rule_version").value("hybrid-v2.0.0"))
                .andExpect(jsonPath("$.results.length()").value(5))
                .andExpect(jsonPath("$.results[0].source_row").value(2))
                .andExpect(jsonPath("$.event_chains[0].members[0].order").value(0))
                .andExpect(jsonPath("$.event_chains[0].members[4].source_row").value(6))
                .andExpect(jsonPath("$.summary.input_count").value(5))
                .andReturn().getResponse().getContentAsString();
        UUID runId = UUID.fromString(objectMapper.readTree(body).get("run_id").asText());

        JsonNode request = LAST_REQUEST.get();
        assertThat(request.get("rule_version")).isNull();
        assertThat(request.path("parameters").size()).isEqualTo(14);
        assertThat(request.path("parameters").path("duplicate_window_seconds").asInt()).isEqualTo(30);
        assertThat(request.path("parameters").path("chatter_min_count").asInt()).isEqualTo(4);
        assertThat(request.path("parameters").path("chatter_min_transition_ratio").asDouble()).isEqualTo(0.8);
        assertThat(request.path("parameters").path("persistent_requires_ack").asBoolean()).isTrue();
        assertThat(request.path("parameters").path("episode_gap_seconds").asInt()).isEqualTo(60);
        assertThat(request.path("parameters").path("min_episode_support").asInt()).isEqualTo(3);
        assertThat(request.path("parameters").path("min_transition_probability").asDouble()).isEqualTo(0.6);
        assertThat(request.path("parameters").path("min_lift").asDouble()).isEqualTo(2.0);
        assertThat(request.path("parameters").path("expert_min_score").asDouble()).isEqualTo(0.35);
        assertThat(request.path("parameters").path("expert_min_margin").asDouble()).isEqualTo(0.10);
        assertThat(request.path("records").size()).isEqualTo(5);
        assertThat(request.path("records").get(0).path("raw_payload").isObject()).isTrue();
        assertThat(jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM analysis_result WHERE run_id = ?", Integer.class, runId)).isEqualTo(5);
        assertThat(jdbcTemplate.queryForObject(
                "SELECT status FROM import_batch WHERE batch_id = ?", String.class, batchId)).isEqualTo("COMPLETED");
        assertThat(jdbcTemplate.queryForList(
                "SELECT event_type FROM audit_event WHERE target_id = ? ORDER BY occurred_at",
                String.class, runId)).containsExactly("ANALYSIS_STARTED", "ANALYSIS_COMPLETED");

        mockMvc.perform(get("/api/v1/analyses/{runId}", runId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.event_chains[0].members.length()").value(5));
        mockMvc.perform(post("/api/v1/imports/{batchId}/analyses", batchId))
                .andExpect(status().isConflict());
        assertThat(jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM analysis_run WHERE batch_id = ?", Integer.class, batchId)).isOne();
    }

    @Test
    void acceptsExplicitValidatedParametersAndRejectsOutOfRangeValuesBeforeStarting() throws Exception {
        UUID validBatch = importedBatch();
        ObjectNode parameters = objectMapper.valueToTree(AnalysisParameters.defaults().validatedMap());
        parameters.put("duplicate_window_seconds", 45);
        parameters.put("expert_min_margin", 0.2);

        mockMvc.perform(post("/api/v1/imports/{batchId}/analyses", validBatch)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsBytes(parameters)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("COMPLETED"))
                .andExpect(jsonPath("$.parameters.duplicate_window_seconds").value(45))
                .andExpect(jsonPath("$.parameters.expert_min_margin").value(0.2));
        assertThat(LAST_REQUEST.get().path("parameters")).isEqualTo(parameters);

        UUID invalidBatch = importedBatch();
        parameters.put("min_lift", 0.9);
        mockMvc.perform(post("/api/v1/imports/{batchId}/analyses", invalidBatch)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsBytes(parameters)))
                .andExpect(status().isBadRequest());
        assertThat(jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM analysis_run WHERE batch_id = ?", Integer.class, invalidBatch)).isZero();
        assertThat(jdbcTemplate.queryForObject(
                "SELECT status FROM import_batch WHERE batch_id = ?", String.class, invalidBatch)).isEqualTo("IMPORTED");
    }

    @Test
    void httpFailureWritesFailedMetadataWithoutPartialResultsAndRetryCanSucceed() throws Exception {
        UUID batchId = importedBatch();
        RESPONDER.set(request -> new StubResponse(503, "unavailable"));

        String failedBody = mockMvc.perform(post("/api/v1/imports/{batchId}/analyses", batchId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("FAILED"))
                .andExpect(jsonPath("$.failure").value("算法服务返回 HTTP 503，可重试"))
                .andExpect(jsonPath("$.results").isEmpty())
                .andReturn().getResponse().getContentAsString();
        UUID failedRunId = UUID.fromString(objectMapper.readTree(failedBody).get("run_id").asText());
        assertZeroResults(failedRunId);
        assertThat(jdbcTemplate.queryForObject("""
                SELECT details ->> 'failure_code' FROM audit_event
                 WHERE target_id = ? AND event_type = 'ANALYSIS_FAILED'
                """, String.class, failedRunId)).isEqualTo("ALGORITHM_HTTP_ERROR");

        RESPONDER.set(AnalysisIntegrationTest::successResponse);
        mockMvc.perform(post("/api/v1/imports/{batchId}/analyses", batchId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("COMPLETED"))
                .andExpect(jsonPath("$.attempt").value(2));
        assertThat(jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM analysis_run WHERE batch_id = ?", Integer.class, batchId)).isEqualTo(2);
        assertZeroResults(failedRunId);
    }

    @Test
    void illegalJsonAndIllegalContractAreFailedWithoutResults() throws Exception {
        UUID invalidJsonBatch = importedBatch();
        RESPONDER.set(request -> new StubResponse(200, "{broken"));
        String invalidJsonBody = mockMvc.perform(post("/api/v1/imports/{batchId}/analyses", invalidJsonBatch))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("FAILED"))
                .andExpect(jsonPath("$.failure").value("算法响应不是合法 JSON，可重试"))
                .andReturn().getResponse().getContentAsString();
        UUID invalidJsonRun = UUID.fromString(objectMapper.readTree(invalidJsonBody).get("run_id").asText());
        assertZeroResults(invalidJsonRun);
        mockMvc.perform(get("/api/v1/analyses/{runId}/dashboard", invalidJsonRun))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("ANALYSIS_STATUS_CONFLICT"));

        UUID invalidContractBatch = importedBatch();
        RESPONDER.set(request -> {
            ObjectNode response = (ObjectNode) parse(successResponse(request).body());
            response.put("rule_version", "wrong-rules");
            return jsonResponse(response);
        });
        String invalidBody = mockMvc.perform(post("/api/v1/imports/{batchId}/analyses", invalidContractBatch))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("FAILED"))
                .andExpect(jsonPath("$.failure").value("算法规则版本不匹配，可重试"))
                .andReturn().getResponse().getContentAsString();
        assertZeroResults(UUID.fromString(objectMapper.readTree(invalidBody).get("run_id").asText()));
    }

    @Test
    void eventChainWithWrongAssociationRuleFailsWithoutPersistingResultsOrChain() throws Exception {
        UUID batchId = importedBatch();
        RESPONDER.set(request -> {
            ObjectNode response = (ObjectNode) parse(successResponse(request).body());
            ((ObjectNode) response.path("event_chains").get(0))
                    .put("association_rule", "LEGACY_TIME_WINDOW_RULE");
            return jsonResponse(response);
        });

        String body = mockMvc.perform(post("/api/v1/imports/{batchId}/analyses", batchId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("FAILED"))
                .andExpect(jsonPath("$.failure").value("事件链关联规则非法，可重试"))
                .andExpect(jsonPath("$.results").isEmpty())
                .andExpect(jsonPath("$.event_chains").isEmpty())
                .andReturn().getResponse().getContentAsString();
        UUID runId = UUID.fromString(objectMapper.readTree(body).get("run_id").asText());

        assertZeroResults(runId);
        assertThat(jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM event_chain WHERE run_id = ?", Integer.class, runId)).isZero();
    }

    @Test
    void eventChainAcrossSiteAreaUnitRelationFailsWithoutPersistingResultsOrChain() throws Exception {
        UUID batchId = importedBatch();
        jdbcTemplate.update(
                "UPDATE alarm_record SET area = '二区' WHERE batch_id = ? AND source_row = 6", batchId);

        String body = mockMvc.perform(post("/api/v1/imports/{batchId}/analyses", batchId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("FAILED"))
                .andExpect(jsonPath("$.failure").value("事件链成员关系范围不一致，可重试"))
                .andExpect(jsonPath("$.results").isEmpty())
                .andExpect(jsonPath("$.event_chains").isEmpty())
                .andReturn().getResponse().getContentAsString();
        UUID runId = UUID.fromString(objectMapper.readTree(body).get("run_id").asText());

        assertZeroResults(runId);
        assertThat(jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM event_chain WHERE run_id = ?", Integer.class, runId)).isZero();
    }

    @Test
    void timeoutIsPersistedAsRetryableFailure() throws Exception {
        UUID batchId = importedBatch();
        RESPONDER.set(request -> {
            try {
                Thread.sleep(Duration.ofSeconds(1));
            } catch (InterruptedException exception) {
                Thread.currentThread().interrupt();
            }
            return successResponse(request);
        });

        String body = mockMvc.perform(post("/api/v1/imports/{batchId}/analyses", batchId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("FAILED"))
                .andExpect(jsonPath("$.failure").value("算法分析超时，可重试"))
                .andReturn().getResponse().getContentAsString();
        assertZeroResults(UUID.fromString(objectMapper.readTree(body).get("run_id").asText()));
    }

    @Test
    void databaseCompletionFailureRollsBackAllResultsThenMarksRunFailed() throws Exception {
        UUID batchId = importedBatch();
        jdbcTemplate.execute("""
                CREATE FUNCTION reject_analysis_completion() RETURNS trigger AS $$
                BEGIN
                    IF NEW.status = 'COMPLETED' THEN
                        RAISE EXCEPTION 'forced analysis completion failure';
                    END IF;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql
                """);
        jdbcTemplate.execute("""
                CREATE TRIGGER reject_analysis_completion BEFORE UPDATE ON import_batch
                FOR EACH ROW EXECUTE FUNCTION reject_analysis_completion()
                """);

        String body = mockMvc.perform(post("/api/v1/imports/{batchId}/analyses", batchId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("FAILED"))
                .andExpect(jsonPath("$.failure").value("数据库保存分析结果失败，可重试"))
                .andReturn().getResponse().getContentAsString();
        UUID runId = UUID.fromString(objectMapper.readTree(body).get("run_id").asText());

        assertZeroResults(runId);
        assertThat(jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM event_chain WHERE run_id = ?", Integer.class, runId)).isZero();
        assertThat(jdbcTemplate.queryForObject(
                "SELECT status FROM import_batch WHERE batch_id = ?", String.class, batchId)).isEqualTo("FAILED");
    }

    @Test
    void wrongBatchStatesConflictAndUnknownRunReturns404() throws Exception {
        UUID readyBatch = previewBatch();
        mockMvc.perform(post("/api/v1/imports/{batchId}/analyses", readyBatch))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("IMPORT_STATUS_CONFLICT"));

        UUID analyzingBatch = importedBatch();
        jdbcTemplate.update("UPDATE import_batch SET status = 'ANALYZING' WHERE batch_id = ?", analyzingBatch);
        mockMvc.perform(post("/api/v1/imports/{batchId}/analyses", analyzingBatch))
                .andExpect(status().isConflict());
        mockMvc.perform(get("/api/v1/analyses/{runId}", UUID.randomUUID()))
                .andExpect(status().isNotFound());
    }

    @Test
    void dashboardAlarmListDetailAndLatestAreBackedByPostgresFacts() throws Exception {
        UUID batchId = importedBatch();
        JsonNode analysis = completedAnalysis(batchId);
        UUID runId = UUID.fromString(analysis.get("run_id").asText());
        UUID firstRecordId = recordId(batchId, 2);
        jdbcTemplate.update("UPDATE alarm_record SET priority = 'P2', area = '二区' "
                + "WHERE batch_id = ? AND source_row = 3", batchId);
        jdbcTemplate.update("UPDATE alarm_record SET unit_name = '反应单元' "
                + "WHERE batch_id = ? AND source_row = 4", batchId);
        jdbcTemplate.update("UPDATE alarm_record SET event_time = event_time + INTERVAL '1 hour' "
                + "WHERE batch_id = ? AND source_row = 6", batchId);

        mockMvc.perform(get("/api/v1/imports/{batchId}/analyses/latest", batchId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.run_id").value(runId.toString()))
                .andExpect(jsonPath("$.status").value("COMPLETED"));
        mockMvc.perform(get("/api/v1/imports/{batchId}/analyses/latest", UUID.randomUUID()))
                .andExpect(status().isNotFound());

        String dashboardBody = mockMvc.perform(get("/api/v1/analyses/{runId}/dashboard", runId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.run_id").value(runId.toString()))
                .andExpect(jsonPath("$.batch_id").value(batchId.toString()))
                .andExpect(jsonPath("$.total").value(5))
                .andExpect(jsonPath("$.disposition_counts.OPEN").value(5))
                .andExpect(jsonPath("$.disposition_counts.IN_PROGRESS").value(0))
                .andExpect(jsonPath("$.disposition_counts.CLOSED").value(0))
                .andExpect(jsonPath("$.trend.length()").value(2))
                .andExpect(jsonPath("$.priority_counts.P1").value(4))
                .andExpect(jsonPath("$.priority_counts.P2").value(1))
                .andExpect(jsonPath("$.area_counts.一区").value(4))
                .andExpect(jsonPath("$.area_counts.二区").value(1))
                .andExpect(jsonPath("$.unit_counts.未指定单元").value(4))
                .andExpect(jsonPath("$.unit_counts.反应单元").value(1))
                .andExpect(jsonPath("$.noise_type_counts.NORMAL").value(5))
                .andExpect(jsonPath("$.cause_category_counts.UNKNOWN").value(5))
                .andReturn().getResponse().getContentAsString();
        assertThat(objectMapper.readTree(dashboardBody).get("total").asLong()).isEqualTo(
                jdbcTemplate.queryForObject(
                        "SELECT COUNT(*) FROM analysis_result WHERE run_id = ?", Long.class, runId));

        mockMvc.perform(get("/api/v1/analyses/{runId}/alarms", runId).param("size", "2"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.page").value(0))
                .andExpect(jsonPath("$.size").value(2))
                .andExpect(jsonPath("$.total").value(5))
                .andExpect(jsonPath("$.items.length()").value(2))
                .andExpect(jsonPath("$.items[0].source_row").value(2))
                .andExpect(jsonPath("$.items[0].disposition_status").value("OPEN"));
        mockMvc.perform(get("/api/v1/analyses/{runId}/alarms", runId)
                        .param("page", "2").param("size", "2"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items.length()").value(1))
                .andExpect(jsonPath("$.items[0].source_row").value(6));
        mockMvc.perform(get("/api/v1/analyses/{runId}/alarms", runId)
                        .param("priority", "P2").param("area", "二区")
                        .param("noise_type", "NORMAL").param("cause_category", "UNKNOWN")
                        .param("disposition_status", "OPEN"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.total").value(1))
                .andExpect(jsonPath("$.items[0].source_row").value(3));
        mockMvc.perform(get("/api/v1/analyses/{runId}/alarms", runId)
                        .param("unit", "未指定单元"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.total").value(4));
        mockMvc.perform(get("/api/v1/analyses/{runId}/alarms", runId).param("priority", "p1"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("ANALYSIS_REQUEST_INVALID"));
        mockMvc.perform(get("/api/v1/analyses/{runId}/alarms", runId).param("page", "-1"))
                .andExpect(status().isBadRequest());
        mockMvc.perform(get("/api/v1/analyses/{runId}/alarms", runId).param("size", "201"))
                .andExpect(status().isBadRequest());

        mockMvc.perform(get("/api/v1/analyses/{runId}/alarms/{recordId}", runId, firstRecordId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.source_row").value(2))
                .andExpect(jsonPath("$.raw_payload.event_time").isNotEmpty())
                .andExpect(jsonPath("$.evidence[0]").value("规则校验通过"))
                .andExpect(jsonPath("$.disposition.status").value("OPEN"))
                .andExpect(jsonPath("$.disposition_history").isEmpty())
                .andExpect(jsonPath("$.event_chains[0].members.length()").value(5))
                .andExpect(jsonPath("$.event_chains[0].members[0].source_row").value(2));
    }

    @Test
    void dispositionTransitionsAreAtomicAuditedAndIsolatedByRun() throws Exception {
        UUID firstBatch = importedBatch();
        UUID firstRun = UUID.fromString(completedAnalysis(firstBatch).get("run_id").asText());
        UUID firstRecord = recordId(firstBatch, 2);
        UUID secondBatch = importedBatch();
        UUID secondRun = UUID.fromString(completedAnalysis(secondBatch).get("run_id").asText());
        UUID secondRecord = recordId(secondBatch, 2);

        mockMvc.perform(patch("/api/v1/analyses/{runId}/alarms/{recordId}/disposition", firstRun, firstRecord)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"status\":\"CLOSED\",\"operator\":\"审核员\",\"note\":\"直接关闭\"}"))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("DISPOSITION_STATUS_CONFLICT"));
        mockMvc.perform(patch("/api/v1/analyses/{runId}/alarms/{recordId}/disposition", firstRun, firstRecord)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"status\":\"IN_PROGRESS\",\"operator\":\"\",\"note\":\"接单\"}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("ANALYSIS_REQUEST_INVALID"));
        mockMvc.perform(patch("/api/v1/analyses/{runId}/alarms/{recordId}/disposition", firstRun, firstRecord)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"status\":\"PENDING\",\"operator\":\"审核员\",\"note\":\"非法状态\"}"))
                .andExpect(status().isBadRequest());

        patchDisposition(firstRun, firstRecord, "IN_PROGRESS", "值班员", "开始核查")
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("IN_PROGRESS"))
                .andExpect(jsonPath("$.closed_at").doesNotExist());
        patchDisposition(firstRun, firstRecord, "CLOSED", "班长", "确认并关闭")
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("CLOSED"))
                .andExpect(jsonPath("$.closed_at").isNotEmpty());

        mockMvc.perform(get("/api/v1/analyses/{runId}/alarms/{recordId}", firstRun, firstRecord))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.disposition.status").value("CLOSED"))
                .andExpect(jsonPath("$.disposition_history.length()").value(2))
                .andExpect(jsonPath("$.disposition_history[0].from_status").value("OPEN"))
                .andExpect(jsonPath("$.disposition_history[0].to_status").value("IN_PROGRESS"))
                .andExpect(jsonPath("$.disposition_history[1].from_status").value("IN_PROGRESS"))
                .andExpect(jsonPath("$.disposition_history[1].to_status").value("CLOSED"));
        patchDisposition(firstRun, firstRecord, "IN_PROGRESS", "班长", "重新打开")
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.closed_at").doesNotExist());

        mockMvc.perform(get("/api/v1/analyses/{runId}/dashboard", firstRun))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.disposition_counts.IN_PROGRESS").value(1))
                .andExpect(jsonPath("$.disposition_counts.OPEN").value(4));
        mockMvc.perform(get("/api/v1/analyses/{runId}/dashboard", secondRun))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.disposition_counts.OPEN").value(5))
                .andExpect(jsonPath("$.disposition_counts.IN_PROGRESS").value(0));
        assertThat(jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM disposition_history WHERE run_id = ?", Integer.class, secondRun)).isZero();

        mockMvc.perform(get("/api/v1/analyses/{runId}/alarms/{recordId}", firstRun, secondRecord))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("ANALYSIS_RESOURCE_NOT_FOUND"));
        mockMvc.perform(patch("/api/v1/analyses/{runId}/alarms/{recordId}/disposition", firstRun, secondRecord)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"status\":\"IN_PROGRESS\",\"operator\":\"审核员\",\"note\":\"跨运行\"}"))
                .andExpect(status().isNotFound());
        mockMvc.perform(get("/api/v1/analyses/{runId}/dashboard", UUID.randomUUID()))
                .andExpect(status().isNotFound());
    }

    private UUID importedBatch() throws Exception {
        return importedBatch(10);
    }

    private JsonNode completedAnalysis(UUID batchId) throws Exception {
        String body = mockMvc.perform(post("/api/v1/imports/{batchId}/analyses", batchId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("COMPLETED"))
                .andReturn().getResponse().getContentAsString();
        return objectMapper.readTree(body);
    }

    private UUID recordId(UUID batchId, int sourceRow) {
        return jdbcTemplate.queryForObject(
                "SELECT record_id FROM alarm_record WHERE batch_id = ? AND source_row = ?",
                UUID.class, batchId, sourceRow);
    }

    private org.springframework.test.web.servlet.ResultActions patchDisposition(
            UUID runId, UUID recordId, String statusValue, String operator, String note) throws Exception {
        String content = objectMapper.writeValueAsString(Map.of(
                "status", statusValue, "operator", operator, "note", note));
        return mockMvc.perform(patch("/api/v1/analyses/{runId}/alarms/{recordId}/disposition", runId, recordId)
                .contentType(MediaType.APPLICATION_JSON)
                .content(content));
    }

    private UUID importedBatch(int intervalSeconds) throws Exception {
        UUID batchId = previewBatch(intervalSeconds);
        mockMvc.perform(post("/api/v1/imports/{batchId}/confirm", batchId)).andExpect(status().isOk());
        return batchId;
    }

    private UUID previewBatch() throws Exception {
        return previewBatch(10);
    }

    private UUID previewBatch(int intervalSeconds) throws Exception {
        StringBuilder csv = new StringBuilder(
                "event_time,site,area,tag,description,priority,state,source_system\n");
        OffsetDateTime start = OffsetDateTime.parse("2026-08-25T08:00:00+08:00");
        for (int index = 0; index < 5; index++) {
            csv.append(start.plusSeconds((long) index * intervalSeconds)).append(",厂区,一区,TAG-")
                    .append(index + 1).append(",压力高,P1,ACTIVE,SYNTHETIC_DCS\n");
        }
        MockMultipartFile file = new MockMultipartFile("file", "analysis.csv", "text/csv",
                csv.toString().getBytes(StandardCharsets.UTF_8));
        String body = mockMvc.perform(multipart("/api/v1/imports/preview").file(file))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("READY"))
                .andReturn().getResponse().getContentAsString();
        return UUID.fromString(objectMapper.readTree(body).get("batch_id").asText());
    }

    private void assertZeroResults(UUID runId) {
        assertThat(jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM analysis_result WHERE run_id = ?", Integer.class, runId)).isZero();
    }

    private static StubResponse successResponse(JsonNode request) {
        ObjectNode response = JSON.createObjectNode();
        response.set("analysis_run_id", request.get("analysis_run_id"));
        response.put("contract_version", "v2");
        response.put("algorithm_version", "0.2.0");
        response.put("rule_version", "hybrid-v2.0.0");
        response.set("parameters", request.get("parameters"));

        ArrayNode results = response.putArray("record_results");
        ArrayNode ids = JSON.createArrayNode();
        JsonNode records = request.get("records");
        for (JsonNode record : records) {
            ids.add(record.get("record_id"));
            ObjectNode result = results.addObject();
            result.set("record_id", record.get("record_id"));
            result.put("noise_type", "NORMAL");
            result.put("alarm_class", "STANDARD");
            result.put("cause_category", "UNKNOWN");
            result.put("score", 0.95);
            result.putArray("evidence").add("规则校验通过");
        }

        ObjectNode chain = response.putArray("event_chains").addObject();
        chain.put("chain_id", "CHAIN-001");
        chain.set("member_record_ids", ids);
        chain.set("start_time", records.get(0).get("event_time"));
        chain.set("end_time", records.get(records.size() - 1).get("event_time"));
        chain.set("start_record_id", records.get(0).get("record_id"));
        chain.put("association_rule", "MARKOV_TRANSITION_HYBRID_V2");
        chain.put("explanation", "五条记录按时间形成事件链；" + "可解释统计证据".repeat(80));

        ObjectNode summary = response.putObject("summary");
        summary.put("input_count", records.size());
        summary.put("success_count", records.size());
        summary.put("failure_count", 0);
        ObjectNode noiseCounts = summary.putObject("noise_type_counts");
        noiseCounts.put("NORMAL", records.size());
        noiseCounts.put("DUPLICATE", 0);
        noiseCounts.put("CHATTER", 0);
        noiseCounts.put("SHORT_LIVED", 0);
        noiseCounts.put("PERSISTENT", 0);
        ObjectNode causeCounts = summary.putObject("cause_category_counts");
        causeCounts.put("PROCESS_DISTURBANCE", 0);
        causeCounts.put("EQUIPMENT_FAULT", 0);
        causeCounts.put("INSTRUMENT_ISSUE", 0);
        causeCounts.put("MAINTENANCE_TEST", 0);
        causeCounts.put("UNKNOWN", records.size());
        summary.put("event_chain_count", 1);
        response.putArray("errors");
        return jsonResponse(response);
    }

    private static StubResponse jsonResponse(JsonNode body) {
        try {
            return new StubResponse(200, JSON.writeValueAsString(body));
        } catch (IOException exception) {
            throw new IllegalStateException(exception);
        }
    }

    private static JsonNode parse(String body) {
        try {
            return JSON.readTree(body);
        } catch (IOException exception) {
            throw new IllegalStateException(exception);
        }
    }

    private static EmbeddedPostgres startPostgres() {
        try {
            return EmbeddedPostgres.start();
        } catch (IOException exception) {
            throw new ExceptionInInitializerError(exception);
        }
    }

    private static HttpServer startAlgorithm() {
        try {
            HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            server.createContext("/api/v2/analyze", exchange -> {
                try {
                    JsonNode request = JSON.readTree(exchange.getRequestBody());
                    LAST_REQUEST.set(request);
                    StubResponse response = RESPONDER.get().apply(request);
                    byte[] body = response.body().getBytes(StandardCharsets.UTF_8);
                    exchange.getResponseHeaders().set("Content-Type", "application/json");
                    exchange.sendResponseHeaders(response.status(), body.length);
                    exchange.getResponseBody().write(body);
                } catch (IOException ignored) {
                    // 客户端超时断开时，stub 写回失败不影响待测行为。
                } finally {
                    exchange.close();
                }
            });
            server.setExecutor(Executors.newCachedThreadPool(runnable -> {
                Thread thread = new Thread(runnable, "analysis-test-stub");
                thread.setDaemon(true);
                return thread;
            }));
            server.start();
            return server;
        } catch (IOException exception) {
            throw new ExceptionInInitializerError(exception);
        }
    }

    private record StubResponse(int status, String body) {
    }
}
