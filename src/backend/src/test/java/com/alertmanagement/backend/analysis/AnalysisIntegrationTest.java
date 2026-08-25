package com.alertmanagement.backend.analysis;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.multipart;
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
                () -> "http://127.0.0.1:" + ALGORITHM.getAddress().getPort() + "/api/v1/analyze");
        registry.add("app.algorithm.analysis-timeout", () -> "250ms");
    }

    @BeforeEach
    void cleanDatabase() {
        jdbcTemplate.execute("DROP TRIGGER IF EXISTS reject_analysis_completion ON import_batch");
        jdbcTemplate.execute("DROP FUNCTION IF EXISTS reject_analysis_completion()");
        jdbcTemplate.execute("TRUNCATE event_chain_member, event_chain, analysis_result, analysis_run, "
                + "alarm_record, import_staging, import_batch CASCADE");
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
                .andExpect(jsonPath("$.contract_version").value("v1"))
                .andExpect(jsonPath("$.algorithm_version").value("0.1.0"))
                .andExpect(jsonPath("$.rule_version").value("rules-v1.0.0"))
                .andExpect(jsonPath("$.results.length()").value(5))
                .andExpect(jsonPath("$.results[0].source_row").value(2))
                .andExpect(jsonPath("$.event_chains[0].members[0].order").value(0))
                .andExpect(jsonPath("$.event_chains[0].members[4].source_row").value(6))
                .andExpect(jsonPath("$.summary.input_count").value(5))
                .andReturn().getResponse().getContentAsString();
        UUID runId = UUID.fromString(objectMapper.readTree(body).get("run_id").asText());

        JsonNode request = LAST_REQUEST.get();
        assertThat(request.get("rule_version")).isNull();
        assertThat(request.path("parameters").path("duplicate_window_seconds").asInt()).isEqualTo(30);
        assertThat(request.path("parameters").path("chatter_min_count").asInt()).isEqualTo(4);
        assertThat(request.path("parameters").path("persistent_requires_ack").asBoolean()).isTrue();
        assertThat(request.path("records").size()).isEqualTo(5);
        assertThat(request.path("records").get(0).path("raw_payload").isObject()).isTrue();
        assertThat(jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM analysis_result WHERE run_id = ?", Integer.class, runId)).isEqualTo(5);
        assertThat(jdbcTemplate.queryForObject(
                "SELECT status FROM import_batch WHERE batch_id = ?", String.class, batchId)).isEqualTo("COMPLETED");

        mockMvc.perform(get("/api/v1/analyses/{runId}", runId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.event_chains[0].members.length()").value(5));
        mockMvc.perform(post("/api/v1/imports/{batchId}/analyses", batchId))
                .andExpect(status().isConflict());
        assertThat(jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM analysis_run WHERE batch_id = ?", Integer.class, batchId)).isOne();
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
        assertZeroResults(UUID.fromString(objectMapper.readTree(invalidJsonBody).get("run_id").asText()));

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
    void eventChainBeyondConfiguredWindowFailsWithoutPersistingResultsOrChain() throws Exception {
        UUID batchId = importedBatch(20);

        String body = mockMvc.perform(post("/api/v1/imports/{batchId}/analyses", batchId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("FAILED"))
                .andExpect(jsonPath("$.failure").value("事件链跨度超过规则窗口，可重试"))
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

    private UUID importedBatch() throws Exception {
        return importedBatch(10);
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
        response.put("contract_version", "v1");
        response.put("algorithm_version", "0.1.0");
        response.put("rule_version", "rules-v1.0.0");
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
        chain.put("association_rule", "同区域时间序列");
        chain.put("explanation", "五条记录按时间形成事件链");

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
            server.createContext("/api/v1/analyze", exchange -> {
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
