package com.alertmanagement.backend.analysis;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.patch;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.alertmanagement.backend.project.ProjectService;
import io.zonky.test.db.postgres.embedded.EmbeddedPostgres;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.math.BigDecimal;
import java.nio.file.Path;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.apache.pdfbox.Loader;
import org.apache.pdfbox.pdmodel.PDDocument;
import org.apache.pdfbox.text.PDFTextStripper;
import org.apache.poi.ss.usermodel.Workbook;
import org.apache.poi.ss.usermodel.WorkbookFactory;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestInstance;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.security.test.context.support.WithMockUser;

@SpringBootTest
@AutoConfigureMockMvc(addFilters = false)
@WithMockUser(username = "test-admin", roles = "SYSTEM_ADMIN")
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class ReportAuditResetIntegrationTest {

    private static final EmbeddedPostgres POSTGRES = startPostgres();

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private JdbcTemplate jdbcTemplate;

    @Autowired
    private ObjectMapper objectMapper;

    @DynamicPropertySource
    static void properties(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", () -> POSTGRES.getJdbcUrl("postgres", "postgres"));
        registry.add("spring.datasource.username", () -> "postgres");
        registry.add("spring.datasource.password", () -> "");
        registry.add("app.deployment-mode", () -> "LOCAL_NATIVE");
        registry.add("app.bootstrap-admin-username", () -> "test-admin");
        registry.add("app.bootstrap-admin-password-file",
                () -> Path.of("src/test/resources/bootstrap-password.txt").toAbsolutePath().toString());
    }

    @BeforeEach
    void cleanDatabase() {
        jdbcTemplate.execute("DROP TABLE IF EXISTS external_reference");
        jdbcTemplate.execute("DROP TABLE IF EXISTS external_sentinel");
        jdbcTemplate.execute("DROP TRIGGER IF EXISTS reject_override_audit ON audit_event");
        jdbcTemplate.execute("DROP FUNCTION IF EXISTS reject_override_audit()");
        jdbcTemplate.execute("DROP TRIGGER IF EXISTS reject_demo_reset ON alarm_record");
        jdbcTemplate.execute("DROP FUNCTION IF EXISTS reject_demo_reset()");
        jdbcTemplate.execute("TRUNCATE disposition_history, alarm_disposition, event_chain_member, event_chain, "
                + "analysis_result_override, analysis_result, analysis_run, alarm_record, import_staging, "
                + "import_batch, audit_event RESTART IDENTITY");
    }

    @AfterAll
    void stopPostgres() throws IOException {
        POSTGRES.close();
    }

    @Test
    void overrideReportsAndAuditUseEffectivePostgresFacts() throws Exception {
        Seed seed = seedCompletedRun();
        UUID recordId = seed.recordIds().getFirst();
        jdbcTemplate.update("UPDATE alarm_record SET area = ? WHERE record_id = ?", "北区\n二线🚀", recordId);

        String invalidAlarmClass = """
                {"noise_type":"CHATTER","alarm_class":"CUSTOM",
                 "cause_category":"INSTRUMENT_ISSUE","operator":"审核员A","reason":"非法枚举"}
                """;
        mockMvc.perform(patch("/api/v1/analyses/{runId}/alarms/{recordId}/classification",
                        seed.runId(), recordId).contentType(MediaType.APPLICATION_JSON).content(invalidAlarmClass))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("ANALYSIS_REQUEST_INVALID"));
        assertThat(jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM analysis_result_override", Integer.class)).isZero();
        assertThat(jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM audit_event", Integer.class)).isZero();

        String override = """
                {"noise_type":"CHATTER","alarm_class":"ACTIONABLE",
                 "cause_category":"INSTRUMENT_ISSUE","operator":"审核员A","reason":"根据事件序列复核"}
                """;
        mockMvc.perform(patch("/api/v1/analyses/{runId}/alarms/{recordId}/classification",
                        seed.runId(), recordId).contentType(MediaType.APPLICATION_JSON).content(override))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.noise_type").value("CHATTER"))
                .andExpect(jsonPath("$.alarm_class").value("ACTIONABLE"))
                .andExpect(jsonPath("$.algorithm_classification.noise_type").value("NORMAL"))
                .andExpect(jsonPath("$.classification_override.operator").value("test-admin"))
                .andExpect(jsonPath("$.classification_override.reason").value("根据事件序列复核"));
        mockMvc.perform(patch("/api/v1/analyses/{runId}/alarms/{recordId}/classification",
                        seed.runId(), recordId).contentType(MediaType.APPLICATION_JSON).content(override))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("RESULT_OVERRIDE_NO_CHANGE"));

        disposition(seed.runId(), recordId, "IN_PROGRESS", "开始处置");
        disposition(seed.runId(), recordId, "CLOSED", "处置完成");
        mockMvc.perform(get("/api/v1/analyses/{runId}/dashboard", seed.runId()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.noise_type_counts.CHATTER").value(1))
                .andExpect(jsonPath("$.noise_type_counts.NORMAL").value(4))
                .andExpect(jsonPath("$.cause_category_counts.INSTRUMENT_ISSUE").value(1))
                .andExpect(jsonPath("$.disposition_counts.CLOSED").value(1));
        mockMvc.perform(get("/api/v1/analyses/{runId}/alarms", seed.runId()).param("noise_type", "CHATTER"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.total").value(1))
                .andExpect(jsonPath("$.items[0].record_id").value(recordId.toString()));

        byte[] pdf = mockMvc.perform(post("/api/v1/analyses/{runId}/reports/pdf", seed.runId())
                        .contentType(MediaType.APPLICATION_JSON).content("{\"operator\":\"审核员A\"}"))
                .andExpect(status().isOk())
                .andExpect(header().string("Content-Type", "application/pdf"))
                .andExpect(header().string("Content-Disposition",
                        org.hamcrest.Matchers.containsString("alert-report-" + seed.runId() + ".pdf")))
                .andReturn().getResponse().getContentAsByteArray();
        try (PDDocument document = Loader.loadPDF(pdf)) {
            String text = new PDFTextStripper().getText(document);
            assertThat(text).contains("报警管理系统", "仅使用合成数据", "报警总数：5", "CHATTER=1",
                    "北区 二线?=1");
            assertThat(text).doesNotContain("北区\n二线");
        }

        byte[] xlsx = mockMvc.perform(post("/api/v1/analyses/{runId}/reports/xlsx", seed.runId())
                        .contentType(MediaType.APPLICATION_JSON).content("{\"operator\":\"审核员A\"}"))
                .andExpect(status().isOk())
                .andExpect(header().string("Content-Type",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
                .andReturn().getResponse().getContentAsByteArray();
        try (Workbook workbook = WorkbookFactory.create(new ByteArrayInputStream(xlsx))) {
            assertThat(workbook.getNumberOfSheets()).isEqualTo(4);
            assertThat(workbook.getSheet("概要").getRow(0).getCell(1).getStringCellValue())
                    .isEqualTo("报警管理系统");
            assertThat(workbook.getSheet("报警明细").getLastRowNum()).isEqualTo(5);
            assertThat(workbook.getSheet("报警明细").getRow(1).getCell(10).getStringCellValue())
                    .isEqualTo("NORMAL");
            assertThat(workbook.getSheet("报警明细").getRow(1).getCell(11).getStringCellValue())
                    .isEqualTo("CHATTER");
            assertThat(workbook.getSheet("关联事件链").getLastRowNum()).isEqualTo(5);
            assertThat(workbook.getSheet("处置历史").getLastRowNum()).isEqualTo(2);
        }

        String auditBody = mockMvc.perform(get("/api/v1/audit-events")
                        .param("page", "0").param("size", "20"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.total").value(5))
                .andExpect(jsonPath("$.items[0].event_type").value("REPORT_EXPORTED"))
                .andExpect(jsonPath("$.items[0].trace_id").isNotEmpty())
                .andReturn().getResponse().getContentAsString();
        JsonNode audit = objectMapper.readTree(auditBody);
        assertThat(audit.path("items")).allSatisfy(item -> {
            assertThat(item.path("operator").asText()).isNotBlank();
            assertThat(item.path("target_id").asText()).isNotBlank();
            assertThat(item.path("result").asText()).isEqualTo("SUCCESS");
            assertThat(item.path("details").isObject()).isTrue();
        });
        mockMvc.perform(get("/api/v1/audit-events")
                        .param("event_type", "RESULT_OVERRIDDEN").param("target_id", recordId.toString()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.total").value(1))
                .andExpect(jsonPath("$.items[0].details.old_value.noise_type").value("NORMAL"))
                .andExpect(jsonPath("$.items[0].details.new_value.noise_type").value("CHATTER"));
    }

    @Test
    void overrideAuditFailureRollsBackAndRunFactsRemainIsolated() throws Exception {
        Seed seed = seedCompletedRun();
        UUID recordId = seed.recordIds().getFirst();
        UUID secondRun = UUID.randomUUID();
        jdbcTemplate.update("""
                INSERT INTO analysis_run (
                    run_id, batch_id, attempt, status, contract_version, algorithm_version,
                    rule_version, parameters, summary, completed_at
                ) VALUES (?, ?, 2, 'COMPLETED', 'v1', '0.1.0', 'rules-v1.0.0', '{}'::jsonb,
                          '{"input_count":5,"success_count":5,"failure_count":0,
                            "noise_type_counts":{"NORMAL":5},"cause_category_counts":{"UNKNOWN":5},
                            "event_chain_count":0}'::jsonb, CURRENT_TIMESTAMP)
                """, secondRun, seed.batchId());
        jdbcTemplate.update("""
                INSERT INTO analysis_result (run_id, record_id, noise_type, alarm_class, cause_category, score, evidence)
                SELECT ?, record_id, noise_type, alarm_class, cause_category, score, evidence
                  FROM analysis_result WHERE run_id = ?
                """, secondRun, seed.runId());
        jdbcTemplate.execute("""
                CREATE FUNCTION reject_override_audit() RETURNS trigger AS $$
                BEGIN
                    IF NEW.event_type = 'RESULT_OVERRIDDEN' THEN
                        RAISE EXCEPTION 'forced audit failure';
                    END IF;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql
                """);
        jdbcTemplate.execute("""
                CREATE TRIGGER reject_override_audit BEFORE INSERT ON audit_event
                FOR EACH ROW EXECUTE FUNCTION reject_override_audit()
                """);
        String request = """
                {"noise_type":"CHATTER","alarm_class":"NUISANCE",
                 "cause_category":"INSTRUMENT_ISSUE","operator":"审核员A","reason":"强制回滚"}
                """;
        assertThatThrownBy(() -> mockMvc.perform(
                patch("/api/v1/analyses/{runId}/alarms/{recordId}/classification", seed.runId(), recordId)
                        .contentType(MediaType.APPLICATION_JSON).content(request)))
                .rootCause().hasMessageContaining("forced audit failure");
        assertThat(jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM analysis_result_override", Integer.class)).isZero();
        jdbcTemplate.execute("DROP TRIGGER reject_override_audit ON audit_event");

        mockMvc.perform(patch("/api/v1/analyses/{runId}/alarms/{recordId}/classification",
                        seed.runId(), recordId).contentType(MediaType.APPLICATION_JSON).content(request))
                .andExpect(status().isOk());
        mockMvc.perform(get("/api/v1/analyses/{runId}/dashboard", secondRun))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.noise_type_counts.NORMAL").value(5))
                .andExpect(jsonPath("$.noise_type_counts.CHATTER").doesNotExist());
    }

    @Test
    void resetIsConfirmedAtomicWhitelistedAndRepeatable() throws Exception {
        Seed seed = seedCompletedRun();
        UUID extraProject = UUID.randomUUID();
        jdbcTemplate.update("""
                INSERT INTO business_project (
                    project_id, code, name, client_name, site, unit_name, status, report_title,
                    report_fields, validation_rules
                ) VALUES (?, 'RESET-EXTRA', '待复位项目', '客户', '厂区', '装置', 'ACTIVE', '临时报告',
                          '["summary"]'::jsonb, '{"required_fields":["operator"]}'::jsonb)
                """, extraProject);
        jdbcTemplate.update("""
                UPDATE business_project SET code='CHANGED', name='已修改默认项目', client_name='其他客户',
                       site='其他厂区', unit_name='其他装置', status='ARCHIVED', report_title='其他报告',
                       report_fields='["summary"]'::jsonb,
                       validation_rules='{"required_fields":["operator"],"value_min":1}'::jsonb
                 WHERE project_id=?
                """, ProjectService.DEFAULT_PROJECT_ID);
        jdbcTemplate.execute("CREATE TABLE external_sentinel (id INTEGER PRIMARY KEY, value VARCHAR(20) NOT NULL)");
        jdbcTemplate.update("INSERT INTO external_sentinel VALUES (1, 'keep')");

        mockMvc.perform(post("/api/v1/demo/reset").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"operator\":\"demo-reviewer\",\"confirmation\":\"WRONG\"}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("DEMO_RESET_REQUEST_INVALID"));
        assertThat(count("alarm_record")).isEqualTo(5);

        jdbcTemplate.update("UPDATE analysis_run SET status = 'ANALYZING' WHERE run_id = ?", seed.runId());
        mockMvc.perform(post("/api/v1/demo/reset").contentType(MediaType.APPLICATION_JSON)
                        .content(resetRequest()))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("DEMO_RESET_ANALYSIS_ACTIVE"));
        assertThat(count("alarm_record")).isEqualTo(5);
        jdbcTemplate.update("UPDATE analysis_run SET status = 'COMPLETED' WHERE run_id = ?", seed.runId());

        jdbcTemplate.execute("""
                CREATE FUNCTION reject_demo_reset() RETURNS trigger AS $$
                BEGIN
                    RAISE EXCEPTION 'forced reset failure';
                END;
                $$ LANGUAGE plpgsql
                """);
        jdbcTemplate.execute("""
                CREATE TRIGGER reject_demo_reset BEFORE TRUNCATE ON alarm_record
                FOR EACH STATEMENT EXECUTE FUNCTION reject_demo_reset()
                """);
        assertThatThrownBy(() -> mockMvc.perform(post("/api/v1/demo/reset")
                        .contentType(MediaType.APPLICATION_JSON).content(resetRequest())))
                .rootCause().hasMessageContaining("forced reset failure");
        assertThat(count("alarm_record")).isEqualTo(5);
        assertThat(count("import_batch")).isOne();
        jdbcTemplate.execute("DROP TRIGGER reject_demo_reset ON alarm_record");

        mockMvc.perform(post("/api/v1/demo/reset").contentType(MediaType.APPLICATION_JSON)
                        .content(resetRequest()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.business_state").value("EMPTY"))
                .andExpect(jsonPath("$.deleted_counts.alarm_record").value(5))
                .andExpect(jsonPath("$.deleted_counts.import_batch").value(1))
                .andExpect(jsonPath("$.deleted_counts.audit_event").value(0))
                .andExpect(jsonPath("$.deleted_counts.business_project").value(1));
        assertThat(count("alarm_record")).isZero();
        assertThat(count("analysis_run")).isZero();
        assertThat(count("audit_event")).isOne();
        assertThat(count("app_metadata")).isZero();
        assertThat(count("flyway_schema_history")).isEqualTo(9);
        assertThat(count("business_project")).isOne();
        assertThat(jdbcTemplate.queryForMap("""
                SELECT code, name, client_name, site, unit_name, status, report_title,
                       report_fields::text AS report_fields, validation_rules::text AS validation_rules
                  FROM business_project WHERE project_id=?
                """, ProjectService.DEFAULT_PROJECT_ID)).containsAllEntriesOf(Map.of(
                        "code", "DEFAULT-DEMO", "name", "默认演示项目", "client_name", "演示客户",
                        "site", "合成厂区", "unit_name", "演示装置", "status", "ACTIVE",
                        "report_title", "报警分析报告",
                        "report_fields", "[\"summary\", \"priority\", \"area\", \"unit\", \"noise\", \"cause\", \"disposition\", \"chains\"]",
                        "validation_rules", "{\"required_fields\": []}"));
        assertThat(jdbcTemplate.queryForObject(
                "SELECT value FROM external_sentinel WHERE id = 1", String.class)).isEqualTo("keep");

        mockMvc.perform(post("/api/v1/demo/reset").contentType(MediaType.APPLICATION_JSON)
                        .content(resetRequest()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.business_state").value("EMPTY"))
                .andExpect(jsonPath("$.deleted_counts.alarm_record").value(0))
                .andExpect(jsonPath("$.deleted_counts.import_batch").value(0))
                .andExpect(jsonPath("$.deleted_counts.business_project").value(0));
    }

    private void disposition(UUID runId, UUID recordId, String statusValue, String note) throws Exception {
        mockMvc.perform(patch("/api/v1/analyses/{runId}/alarms/{recordId}/disposition", runId, recordId)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"status\":\"" + statusValue
                                + "\",\"operator\":\"审核员A\",\"note\":\"" + note + "\"}"))
                .andExpect(status().isOk());
    }

    private Seed seedCompletedRun() {
        UUID batchId = UUID.randomUUID();
        UUID runId = UUID.randomUUID();
        jdbcTemplate.update("""
                INSERT INTO import_batch (
                    batch_id, project_id, file_name, file_format, status, total_rows, valid_rows, error_count,
                    headers, field_mapping, errors, imported_at
                ) VALUES (?, ?, 'synthetic.csv', 'CSV', 'COMPLETED', 5, 5, 0,
                          '[]'::jsonb, '{}'::jsonb, '[]'::jsonb, CURRENT_TIMESTAMP)
                """, batchId, ProjectService.DEFAULT_PROJECT_ID);
        List<UUID> records = new java.util.ArrayList<>();
        OffsetDateTime start = OffsetDateTime.parse("2026-08-25T08:00:00+08:00");
        for (int index = 0; index < 5; index++) {
            UUID recordId = UUID.randomUUID();
            records.add(recordId);
            jdbcTemplate.update("""
                    INSERT INTO alarm_record (
                        record_id, batch_id, source_row, event_time, site, area, unit_name, tag,
                        description, priority, alarm_state, alarm_value, threshold, engineering_unit,
                        source_system, operator_name, raw_payload
                    ) VALUES (?, ?, ?, ?, '合成厂区', '一区', NULL, ?, '压力高', 'P1', 'ACTIVE',
                              12.5, 10, 'MPa', 'SYNTHETIC_DCS', NULL,
                              jsonb_build_object('event_time', ?, 'tag', ?))
                    """, recordId, batchId, index + 2, start.plusSeconds(index * 10L), "TAG-" + (index + 1),
                    start.plusSeconds(index * 10L).toString(), "TAG-" + (index + 1));
        }
        jdbcTemplate.update("""
                INSERT INTO analysis_run (
                    run_id, batch_id, attempt, status, contract_version, algorithm_version,
                    rule_version, parameters, summary, completed_at
                ) VALUES (?, ?, 1, 'COMPLETED', 'v1', '0.1.0', 'rules-v1.0.0', '{}'::jsonb,
                          '{"input_count":5,"success_count":5,"failure_count":0,
                            "noise_type_counts":{"NORMAL":5},"cause_category_counts":{"UNKNOWN":5},
                            "event_chain_count":1}'::jsonb, CURRENT_TIMESTAMP)
                """, runId, batchId);
        for (UUID record : records) {
            jdbcTemplate.update("""
                    INSERT INTO analysis_result (
                        run_id, record_id, noise_type, alarm_class, cause_category, score, evidence
                    ) VALUES (?, ?, 'NORMAL', 'STANDARD', 'UNKNOWN', ?, '["规则校验通过"]'::jsonb)
                    """, runId, record, new BigDecimal("0.95"));
        }
        jdbcTemplate.update("""
                INSERT INTO event_chain (
                    run_id, chain_id, start_record_id, start_time, end_time, association_rule, explanation
                ) VALUES (?, 'CHAIN-001', ?, ?, ?, '同区域时间序列', '五条记录构成关联事件链')
                """, runId, records.getFirst(), start, start.plusSeconds(40));
        for (int index = 0; index < records.size(); index++) {
            jdbcTemplate.update("""
                    INSERT INTO event_chain_member (run_id, chain_id, member_order, record_id)
                    VALUES (?, 'CHAIN-001', ?, ?)
                    """, runId, index, records.get(index));
        }
        return new Seed(batchId, runId, List.copyOf(records));
    }

    private long count(String table) {
        return jdbcTemplate.queryForObject("SELECT COUNT(*) FROM " + table, Long.class);
    }

    private String resetRequest() {
        return "{\"operator\":\"demo-reviewer\",\"confirmation\":\"RESET_DEMO\"}";
    }

    private static EmbeddedPostgres startPostgres() {
        try {
            return EmbeddedPostgres.start();
        } catch (IOException exception) {
            throw new ExceptionInInitializerError(exception);
        }
    }

    private record Seed(UUID batchId, UUID runId, List<UUID> recordIds) {
    }
}
