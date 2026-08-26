package com.alertmanagement.backend.project;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.multipart;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.patch;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.zonky.test.db.postgres.embedded.EmbeddedPostgres;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.Map;
import java.util.UUID;
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
import org.springframework.security.test.context.support.WithMockUser;

@SpringBootTest
@AutoConfigureMockMvc(addFilters = false)
@WithMockUser(username = "test-admin", roles = "SYSTEM_ADMIN")
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class ProjectIntegrationTest {

    private static final EmbeddedPostgres POSTGRES = startPostgres();

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
        registry.add("app.deployment-mode", () -> "LOCAL_NATIVE");
        registry.add("app.bootstrap-admin-username", () -> "test-admin");
        registry.add("app.bootstrap-admin-password-file",
                () -> Path.of("src/test/resources/bootstrap-password.txt").toAbsolutePath().toString());
    }

    @BeforeEach
    void cleanDatabase() {
        jdbcTemplate.execute("TRUNCATE disposition_history, alarm_disposition, event_chain_member, event_chain, "
                + "analysis_result_override, analysis_result, analysis_run, alarm_record, import_staging, "
                + "import_batch, audit_event RESTART IDENTITY");
        jdbcTemplate.update("DELETE FROM business_project WHERE project_id <> ?", ProjectService.DEFAULT_PROJECT_ID);
        jdbcTemplate.update("""
                UPDATE business_project SET status='ACTIVE', validation_rules='{"required_fields":[]}'::jsonb,
                       updated_at=CURRENT_TIMESTAMP WHERE project_id=?
                """, ProjectService.DEFAULT_PROJECT_ID);
    }

    @AfterAll
    void stopPostgres() throws IOException {
        POSTGRES.close();
    }

    @Test
    void projectLifecycleEnforcesUniqueNameCodeAndControlledDelete() throws Exception {
        UUID first = createProject("P-100", "一期项目", null);

        mockMvc.perform(post("/api/v1/projects").contentType(MediaType.APPLICATION_JSON)
                        .content(projectJson("P-100", "另一个项目", null)))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("PROJECT_CODE_CONFLICT"))
                .andExpect(jsonPath("$.message").value("项目编号已存在"));
        mockMvc.perform(post("/api/v1/projects").contentType(MediaType.APPLICATION_JSON)
                        .content(projectJson("P-101", "一期项目", null)))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("PROJECT_NAME_CONFLICT"))
                .andExpect(jsonPath("$.message").value("项目名称已存在"));

        mockMvc.perform(patch("/api/v1/projects/{projectId}", first)
                        .contentType(MediaType.APPLICATION_JSON).content("{\"report_title\":\"一期报警报告\"}"))
                .andExpect(status().isOk()).andExpect(jsonPath("$.report_title").value("一期报警报告"));
        mockMvc.perform(post("/api/v1/projects/{projectId}/archive", first)).andExpect(status().isOk());
        mockMvc.perform(patch("/api/v1/projects/{projectId}", first)
                        .contentType(MediaType.APPLICATION_JSON).content("{\"site\":\"新厂区\"}"))
                .andExpect(status().isConflict()).andExpect(jsonPath("$.message").value("项目已归档，不能修改"));
        mockMvc.perform(post("/api/v1/projects/{projectId}/restore", first)).andExpect(status().isOk());

        UUID empty = createProject("P-EMPTY", "空项目", null);
        mockMvc.perform(post("/api/v1/projects/{projectId}/archive", empty)).andExpect(status().isOk());
        mockMvc.perform(delete("/api/v1/projects/{projectId}", empty)).andExpect(status().isNoContent());
        mockMvc.perform(get("/api/v1/projects/{projectId}", empty)).andExpect(status().isNotFound());

        mockMvc.perform(get("/api/v1/projects").param("q", "一期"))
                .andExpect(status().isOk()).andExpect(jsonPath("$[0].project_id").value(first.toString()));
        mockMvc.perform(get("/api/v1/projects/{projectId}/export", first))
                .andExpect(status().isOk()).andExpect(jsonPath("$.manifest_type").value("PROJECT_SUMMARY"))
                .andExpect(jsonPath("$.project.project_id").value(first.toString()));
    }

    @Test
    void projectRulesManualAlarmInvalidationAndArchiveGuardsUseProjectFacts() throws Exception {
        Map<String, Object> rules = Map.of(
                "required_fields", java.util.List.of("operator"),
                "value_min", 0,
                "value_max", 10);
        UUID projectId = createProject("P-200", "二期项目", rules);
        String csv = "event_time,site,area,tag,description,priority,state,value,source_system\n"
                + "2026-08-25 08:00:00,厂区,一区,TAG-1,压力高,P1,ACTIVE,12,DCS\n";
        MockMultipartFile file = new MockMultipartFile(
                "file", "rules.csv", "text/csv", csv.getBytes(StandardCharsets.UTF_8));
        mockMvc.perform(multipart("/api/v1/imports/preview").file(file)
                        .param("project_id", projectId.toString()))
                .andExpect(status().isOk()).andExpect(jsonPath("$.project_id").value(projectId.toString()))
                .andExpect(jsonPath("$.status").value("REJECTED"))
                .andExpect(jsonPath("$.errors[?(@.code == 'PROJECT_RULE_REQUIRED')]").exists())
                .andExpect(jsonPath("$.errors[?(@.code == 'PROJECT_RULE_RANGE')]").exists());

        String createBody = """
                {"event_time":"2026-08-25T08:00:00+08:00","site":"厂区","area":"一区",
                 "unit":"装置A","tag":"TAG-M1","description":"原始描述","priority":"P1",
                 "state":"ACTIVE","value":8,"threshold":7,"engineering_unit":"MPa",
                 "source_system":"MANUAL","operator":"录入员"}
                """;
        JsonNode manual = body(mockMvc.perform(post("/api/v1/projects/{projectId}/manual-alarms", projectId)
                .contentType(MediaType.APPLICATION_JSON).content(createBody))
                .andExpect(status().isOk()).andExpect(jsonPath("$.raw_payload.description").value("原始描述"))
                .andReturn().getResponse().getContentAsString());
        UUID batchId = UUID.fromString(manual.get("batch_id").asText());
        UUID recordId = UUID.fromString(manual.get("record_id").asText());

        mockMvc.perform(patch("/api/v1/projects/{projectId}/manual-alarms/{recordId}", projectId, recordId)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"description\":\"修订描述\",\"edited_by\":\"复核员\",\"reason\":\"纠正录入\"}"))
                .andExpect(status().isOk()).andExpect(jsonPath("$.description").value("修订描述"))
                .andExpect(jsonPath("$.raw_payload.description").value("原始描述"));
        mockMvc.perform(post("/api/v1/projects/{projectId}/manual-alarms/{recordId}/invalidate", projectId, recordId)
                        .contentType(MediaType.APPLICATION_JSON)
                .content("{\"operator\":\"复核员\",\"reason\":\"无效报警\"}"))
                .andExpect(status().isOk()).andExpect(jsonPath("$.invalidated_at").isNotEmpty());
        mockMvc.perform(get("/api/v1/projects/{projectId}/manual-alarms", projectId))
                .andExpect(status().isOk()).andExpect(jsonPath("$.length()").value(1))
                .andExpect(jsonPath("$[0].record_id").value(recordId.toString()))
                .andExpect(jsonPath("$[0].description").value("修订描述"))
                .andExpect(jsonPath("$[0].raw_payload.description").value("原始描述"))
                .andExpect(jsonPath("$[0].invalidated_by").value("test-admin"))
                .andExpect(jsonPath("$[0].invalidation_reason").value("无效报警"));
        mockMvc.perform(get("/api/v1/projects/{projectId}/overview", projectId))
                .andExpect(status().isOk()).andExpect(jsonPath("$.statistics.batch_count").value(2))
                .andExpect(jsonPath("$.statistics.alarm_count").value(1))
                .andExpect(jsonPath("$.statistics.invalid_alarm_count").value(1));

        mockMvc.perform(post("/api/v1/projects/{projectId}/archive", projectId)).andExpect(status().isOk());
        mockMvc.perform(get("/api/v1/projects/{projectId}/manual-alarms", projectId))
                .andExpect(status().isOk()).andExpect(jsonPath("$.length()").value(1));
        mockMvc.perform(post("/api/v1/imports/{batchId}/analyses", batchId))
                .andExpect(status().isConflict()).andExpect(jsonPath("$.message").value("项目已归档，不能启动分析"));
        mockMvc.perform(delete("/api/v1/projects/{projectId}", projectId))
                .andExpect(status().isConflict()).andExpect(jsonPath("$.message").value("项目已经产生业务数据，不能删除"));
        assertThat(jdbcTemplate.queryForObject("""
                SELECT COUNT(*) FROM audit_event WHERE target_id=? AND event_type IN (
                    'MANUAL_ALARM_CREATED','MANUAL_ALARM_UPDATED','MANUAL_ALARM_INVALIDATED')
                """, Integer.class, recordId)).isEqualTo(3);
    }

    private UUID createProject(String code, String name, Map<String, Object> rules) throws Exception {
        String response = mockMvc.perform(post("/api/v1/projects").contentType(MediaType.APPLICATION_JSON)
                        .content(projectJson(code, name, rules)))
                .andExpect(status().isOk()).andExpect(jsonPath("$.status").value("ACTIVE"))
                .andReturn().getResponse().getContentAsString();
        return UUID.fromString(body(response).get("project_id").asText());
    }

    private String projectJson(String code, String name, Map<String, Object> rules) throws Exception {
        Map<String, Object> request = new java.util.LinkedHashMap<>();
        request.put("code", code);
        request.put("name", name);
        request.put("client_name", "测试客户");
        request.put("site", "测试厂区");
        request.put("unit_name", "测试装置");
        if (rules != null) request.put("validation_rules", rules);
        return objectMapper.writeValueAsString(request);
    }

    private JsonNode body(String response) throws Exception {
        return objectMapper.readTree(response);
    }

    private static EmbeddedPostgres startPostgres() {
        try {
            return EmbeddedPostgres.start();
        } catch (IOException exception) {
            throw new ExceptionInInitializerError(exception);
        }
    }
}
