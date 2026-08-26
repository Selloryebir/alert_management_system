package com.alertmanagement.backend.security;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.csrf;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.patch;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.alertmanagement.backend.project.ProjectService;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.zonky.test.db.postgres.embedded.EmbeddedPostgres;
import java.io.IOException;
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
import org.springframework.mock.web.MockHttpSession;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;

@SpringBootTest
@AutoConfigureMockMvc
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class SecurityIntegrationTest {

    private static final String INITIAL_PASSWORD = "Test-Admin-Password-2026!";
    private static final String ADMIN_PASSWORD = "Admin-Changed-Password-2026!";
    private static final EmbeddedPostgres POSTGRES = startPostgres();

    @Autowired private MockMvc mockMvc;
    @Autowired private JdbcTemplate jdbcTemplate;
    @Autowired private ObjectMapper objectMapper;
    @Autowired private PasswordEncoder passwordEncoder;

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
    void resetSecurityState() {
        jdbcTemplate.execute("TRUNCATE disposition_history, alarm_disposition, event_chain_member, event_chain, "
                + "analysis_result_override, analysis_result, analysis_run, alarm_record, import_staging, "
                + "import_batch, audit_event RESTART IDENTITY CASCADE");
        jdbcTemplate.execute("DELETE FROM project_membership");
        jdbcTemplate.update("DELETE FROM user_account WHERE username <> 'test-admin'");
        jdbcTemplate.update("DELETE FROM business_project WHERE project_id <> ?", ProjectService.DEFAULT_PROJECT_ID);
        jdbcTemplate.update("""
                UPDATE user_account
                   SET password_hash=?, display_name='测试管理员', global_role='SYSTEM_ADMIN', status='ACTIVE',
                       must_change_password=FALSE, failed_login_attempts=0, locked_until=NULL,
                       credential_version=credential_version+1, updated_at=CURRENT_TIMESTAMP
                 WHERE username='test-admin'
                """, passwordEncoder.encode(ADMIN_PASSWORD));
        UUID adminId = jdbcTemplate.queryForObject(
                "SELECT user_id FROM user_account WHERE username='test-admin'", UUID.class);
        jdbcTemplate.update("""
                INSERT INTO project_membership(project_id, user_id, project_role)
                VALUES (?, ?, 'MANAGER')
                """, ProjectService.DEFAULT_PROJECT_ID, adminId);
    }

    @AfterAll
    void stopPostgres() throws IOException {
        POSTGRES.close();
    }

    @Test
    void csrfFirstLoginPasswordChangeAndSessionInvalidationFormClosedLoop() throws Exception {
        jdbcTemplate.update("""
                UPDATE user_account SET password_hash=?, must_change_password=TRUE,
                    credential_version=credential_version+1 WHERE username='test-admin'
                """, passwordEncoder.encode(INITIAL_PASSWORD));

        mockMvc.perform(get("/api/v1/auth/csrf"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.token").isNotEmpty())
                .andExpect(jsonPath("$.header_name").value("X-CSRF-TOKEN"));
        mockMvc.perform(post("/api/v1/auth/login").contentType(MediaType.APPLICATION_JSON)
                        .content(loginJson("test-admin", INITIAL_PASSWORD)))
                .andExpect(status().isForbidden()).andExpect(jsonPath("$.code").value("CSRF_INVALID"));
        mockMvc.perform(get("/api/v1/projects"))
                .andExpect(status().isUnauthorized()).andExpect(jsonPath("$.code").value("AUTH_REQUIRED"));

        MockHttpSession firstSession = login("test-admin", INITIAL_PASSWORD, true);
        mockMvc.perform(get("/api/v1/projects").session(firstSession))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.code").value("PASSWORD_CHANGE_REQUIRED"));
        mockMvc.perform(post("/api/v1/auth/password").session(firstSession).with(csrf())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of(
                                "current_password", INITIAL_PASSWORD,
                                "new_password", ADMIN_PASSWORD))))
                .andExpect(status().isOk()).andExpect(jsonPath("$.must_change_password").value(false));
        assertThat(firstSession.isInvalid()).isTrue();

        MockHttpSession changedSession = login("test-admin", ADMIN_PASSWORD, false);
        mockMvc.perform(get("/api/v1/projects").session(changedSession))
                .andExpect(status().isOk()).andExpect(jsonPath("$[0].project_role").value("SYSTEM_ADMIN"));
    }

    @Test
    void globalAndProjectRolesEnforceManagerAnalystAndCrossProjectBoundaries() throws Exception {
        MockHttpSession admin = login("test-admin", ADMIN_PASSWORD, false);
        UUID managerId = createUser(admin, "manager-a", "项目负责人", "Manager-Password-2026!");
        UUID analystId = createUser(admin, "analyst-a", "分析人员", "Analyst-Password-2026!");
        UUID outsiderId = createUser(admin, "outsider-a", "项目外人员", "Outsider-Password-2026!");
        UUID projectA = createProject(admin, "SEC-A", "安全项目 A");
        UUID projectB = createProject(admin, "SEC-B", "安全项目 B");
        putMember(admin, projectA, managerId, "MANAGER");
        putMember(admin, projectA, analystId, "ANALYST");
        jdbcTemplate.update("UPDATE user_account SET must_change_password=FALSE WHERE user_id IN (?, ?, ?)",
                managerId, analystId, outsiderId);

        MockHttpSession manager = login("manager-a", "Manager-Password-2026!", false);
        MockHttpSession analyst = login("analyst-a", "Analyst-Password-2026!", false);
        MockHttpSession outsider = login("outsider-a", "Outsider-Password-2026!", false);

        mockMvc.perform(patch("/api/v1/projects/{id}", projectA).session(manager).with(csrf())
                        .contentType(MediaType.APPLICATION_JSON).content("{\"site\":\"负责人厂区\"}"))
                .andExpect(status().isOk());
        mockMvc.perform(post("/api/v1/projects").session(manager).with(csrf())
                        .contentType(MediaType.APPLICATION_JSON).content(projectJson("SEC-C", "越权项目")))
                .andExpect(status().isForbidden());
        mockMvc.perform(get("/api/v1/projects/{id}", projectA).session(analyst))
                .andExpect(status().isOk()).andExpect(jsonPath("$.project_role").value("ANALYST"));
        mockMvc.perform(patch("/api/v1/projects/{id}", projectA).session(analyst).with(csrf())
                        .contentType(MediaType.APPLICATION_JSON).content("{\"site\":\"越权修改\"}"))
                .andExpect(status().isForbidden());
        mockMvc.perform(get("/api/v1/projects/{id}", projectB).session(analyst))
                .andExpect(status().isNotFound()).andExpect(jsonPath("$.code").value("RESOURCE_NOT_FOUND"));
        mockMvc.perform(get("/api/v1/projects/{id}", projectA).session(outsider))
                .andExpect(status().isNotFound());
        mockMvc.perform(get("/api/v1/admin/users").session(analyst))
                .andExpect(status().isForbidden());
        mockMvc.perform(get("/api/v1/projects").session(analyst))
                .andExpect(status().isOk()).andExpect(jsonPath("$.length()").value(1))
                .andExpect(jsonPath("$[0].project_id").value(projectA.toString()));
    }

    @Test
    void accountDisableInvalidatesSessionAndFailedLoginLocksAccount() throws Exception {
        MockHttpSession admin = login("test-admin", ADMIN_PASSWORD, false);
        UUID userId = createUser(admin, "locked-user", "待锁定账号", "Locked-User-Password-2026!");
        jdbcTemplate.update("UPDATE user_account SET must_change_password=FALSE WHERE user_id=?", userId);
        MockHttpSession user = login("locked-user", "Locked-User-Password-2026!", false);

        mockMvc.perform(patch("/api/v1/admin/users/{id}", userId).session(admin).with(csrf())
                        .contentType(MediaType.APPLICATION_JSON).content("{\"status\":\"DISABLED\"}"))
                .andExpect(status().isOk());
        mockMvc.perform(get("/api/v1/auth/me").session(user))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("AUTH_SESSION_INVALID"));

        UUID lockId = createUser(admin, "five-fails", "登录锁定账号", "Five-Fails-Password-2026!");
        for (int attempt = 0; attempt < 5; attempt++) {
            mockMvc.perform(post("/api/v1/auth/login").with(csrf())
                            .contentType(MediaType.APPLICATION_JSON)
                            .content(loginJson("five-fails", "wrong-password")))
                    .andExpect(status().isUnauthorized())
                    .andExpect(jsonPath("$.code").value("LOGIN_FAILED"));
        }
        assertThat(jdbcTemplate.queryForObject(
                "SELECT locked_until IS NOT NULL FROM user_account WHERE user_id=?", Boolean.class, lockId)).isTrue();
        mockMvc.perform(post("/api/v1/auth/login").with(csrf()).contentType(MediaType.APPLICATION_JSON)
                        .content(loginJson("five-fails", "Five-Fails-Password-2026!")))
                .andExpect(status().isUnauthorized()).andExpect(jsonPath("$.code").value("LOGIN_FAILED"));
    }

    @Test
    void requestByteLimitsRejectBeforeBusinessHandling() throws Exception {
        String unicodeQuery = "中".repeat(700);
        mockMvc.perform(get("/api/v1/projects").queryParam("q", unicodeQuery))
                .andExpect(status().isUriTooLong()).andExpect(jsonPath("$.code").value("QUERY_TOO_LARGE"));
        String oversizedJson = "{\"value\":\"" + "a".repeat(1024 * 1024) + "\"}";
        mockMvc.perform(post("/api/v1/auth/login").with(csrf()).contentType(MediaType.APPLICATION_JSON)
                        .content(oversizedJson))
                .andExpect(status().isPayloadTooLarge())
                .andExpect(jsonPath("$.code").value("REQUEST_BODY_TOO_LARGE"));
    }

    private MockHttpSession login(String username, String password, boolean mustChange) throws Exception {
        MvcResult result = mockMvc.perform(post("/api/v1/auth/login").with(csrf())
                        .contentType(MediaType.APPLICATION_JSON).content(loginJson(username, password)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.username").value(username))
                .andExpect(jsonPath("$.must_change_password").value(mustChange))
                .andReturn();
        return (MockHttpSession) result.getRequest().getSession(false);
    }

    private UUID createUser(MockHttpSession admin, String username, String displayName, String password)
            throws Exception {
        String response = mockMvc.perform(post("/api/v1/admin/users").session(admin).with(csrf())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of(
                                "username", username, "display_name", displayName,
                                "password", password, "global_role", "NONE"))))
                .andExpect(status().isOk()).andExpect(jsonPath("$.password").doesNotExist())
                .andReturn().getResponse().getContentAsString();
        return UUID.fromString(objectMapper.readTree(response).get("user_id").asText());
    }

    private UUID createProject(MockHttpSession admin, String code, String name) throws Exception {
        String response = mockMvc.perform(post("/api/v1/projects").session(admin).with(csrf())
                        .contentType(MediaType.APPLICATION_JSON).content(projectJson(code, name)))
                .andExpect(status().isOk()).andReturn().getResponse().getContentAsString();
        return UUID.fromString(objectMapper.readTree(response).get("project_id").asText());
    }

    private void putMember(MockHttpSession admin, UUID projectId, UUID userId, String role) throws Exception {
        mockMvc.perform(put("/api/v1/projects/{projectId}/members/{userId}", projectId, userId)
                        .session(admin).with(csrf()).contentType(MediaType.APPLICATION_JSON)
                        .content("{\"project_role\":\"" + role + "\"}"))
                .andExpect(status().isOk()).andExpect(jsonPath("$.project_role").value(role));
    }

    private String loginJson(String username, String password) throws Exception {
        return objectMapper.writeValueAsString(Map.of("username", username, "password", password));
    }

    private String projectJson(String code, String name) throws Exception {
        return objectMapper.writeValueAsString(Map.of(
                "code", code, "name", name, "client_name", "测试客户",
                "site", "测试厂区", "unit_name", "测试装置"));
    }

    private static EmbeddedPostgres startPostgres() {
        try {
            return EmbeddedPostgres.start();
        } catch (IOException exception) {
            throw new ExceptionInInitializerError(exception);
        }
    }
}
