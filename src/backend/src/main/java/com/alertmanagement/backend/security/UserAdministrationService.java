package com.alertmanagement.backend.security;

import com.alertmanagement.backend.api.BusinessApiException;
import com.alertmanagement.backend.audit.AuditService;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
class UserAdministrationService {

    private static final Set<String> GLOBAL_ROLES = Set.of("SYSTEM_ADMIN", "NONE");
    private static final Set<String> STATUSES = Set.of("ACTIVE", "DISABLED");
    private static final Set<String> PROJECT_ROLES = Set.of("MANAGER", "ANALYST");

    private final JdbcTemplate jdbcTemplate;
    private final ProjectAccessService accessService;
    private final PasswordPolicy passwordPolicy;
    private final PasswordEncoder encoder;
    private final AuditService auditService;

    UserAdministrationService(JdbcTemplate jdbcTemplate, ProjectAccessService accessService,
            PasswordPolicy passwordPolicy, PasswordEncoder encoder, AuditService auditService) {
        this.jdbcTemplate = jdbcTemplate;
        this.accessService = accessService;
        this.passwordPolicy = passwordPolicy;
        this.encoder = encoder;
        this.auditService = auditService;
    }

    List<UserView> listUsers() {
        accessService.requireSystemAdmin();
        return jdbcTemplate.query(userSelect() + " ORDER BY username", (resultSet, rowNumber) -> user(resultSet));
    }

    @Transactional
    UserView createUser(UserCreateRequest request) {
        accessService.requireSystemAdmin();
        if (request == null) throw invalid("请求体不能为空");
        String username = AuthService.normalizeUsername(request.username());
        String displayName = text(request.displayName(), "display_name", 100);
        String role = allowed(request.globalRole(), GLOBAL_ROLES, "global_role");
        passwordPolicy.validate(username, request.password(), null, encoder);
        UUID userId = UUID.randomUUID();
        try {
            jdbcTemplate.update("""
                    INSERT INTO user_account (user_id, username, display_name, password_hash, global_role, status)
                    VALUES (?, ?, ?, ?, ?, 'ACTIVE')
                    """, userId, username, displayName, encoder.encode(request.password()), role);
        } catch (org.springframework.dao.DuplicateKeyException exception) {
            throw new BusinessApiException(HttpStatus.CONFLICT, "USERNAME_CONFLICT", "账号名已存在");
        }
        auditService.record("USER_CREATED", "USER", userId, null, "SUCCESS",
                Map.of("username", username, "global_role", role));
        return getUser(userId);
    }

    @Transactional
    UserView patchUser(UUID userId, UserPatchRequest request) {
        accessService.requireSystemAdmin();
        if (request == null) throw invalid("请求体不能为空");
        UserView current = getUser(userId);
        String displayName = request.displayName() == null ? current.displayName()
                : text(request.displayName(), "display_name", 100);
        String status = request.status() == null ? current.status()
                : allowed(request.status(), STATUSES, "status");
        String role = request.globalRole() == null ? current.globalRole()
                : allowed(request.globalRole(), GLOBAL_ROLES, "global_role");
        if ("SYSTEM_ADMIN".equals(current.globalRole()) && "ACTIVE".equals(current.status())
                && (!"SYSTEM_ADMIN".equals(role) || !"ACTIVE".equals(status))) {
            requireAnotherAdmin(userId);
        }
        if ("ACTIVE".equals(current.status()) && "DISABLED".equals(status)) {
            requireNoOrphanedProject(userId);
        }
        boolean credentialsChanged = !status.equals(current.status()) || !role.equals(current.globalRole());
        jdbcTemplate.update("""
                UPDATE user_account SET display_name=?, status=?, global_role=?,
                       credential_version=credential_version + ?, updated_at=CURRENT_TIMESTAMP
                 WHERE user_id=?
                """, displayName, status, role, credentialsChanged ? 1 : 0, userId);
        auditService.record("USER_UPDATED", "USER", userId, null, "SUCCESS",
                Map.of("status", status, "global_role", role));
        return getUser(userId);
    }

    @Transactional
    UserView resetPassword(UUID userId, PasswordResetRequest request) {
        accessService.requireSystemAdmin();
        if (request == null) throw invalid("请求体不能为空");
        UserView current = getUser(userId);
        String currentHash = jdbcTemplate.queryForObject(
                "SELECT password_hash FROM user_account WHERE user_id=? FOR UPDATE", String.class, userId);
        passwordPolicy.validate(current.username(), request.newPassword(), currentHash, encoder);
        jdbcTemplate.update("""
                UPDATE user_account SET password_hash=?, must_change_password=TRUE,
                       credential_version=credential_version+1, failed_login_attempts=0, locked_until=NULL,
                       updated_at=CURRENT_TIMESTAMP WHERE user_id=?
                """, encoder.encode(request.newPassword()), userId);
        auditService.record("USER_PASSWORD_RESET", "USER", userId, null, "SUCCESS", Map.of());
        return getUser(userId);
    }

    List<ProjectMemberView> listMembers(UUID projectId) {
        accessService.requireManager(projectId);
        return jdbcTemplate.query("""
                SELECT u.user_id, u.username, u.display_name, u.status, m.project_role
                  FROM project_membership m JOIN user_account u ON u.user_id=m.user_id
                 WHERE m.project_id=? ORDER BY u.username
                """, (resultSet, rowNumber) -> new ProjectMemberView(
                        resultSet.getObject("user_id", UUID.class), resultSet.getString("username"),
                        resultSet.getString("display_name"), resultSet.getString("status"),
                        resultSet.getString("project_role")), projectId);
    }

    @Transactional
    ProjectMemberView putMember(UUID projectId, UUID userId, ProjectMemberRequest request) {
        accessService.requireManager(projectId);
        if (request == null) throw invalid("请求体不能为空");
        String projectRole = allowed(request.projectRole(), PROJECT_ROLES, "project_role");
        UserView user = getUser(userId);
        if (!"ACTIVE".equals(user.status())) {
            throw new BusinessApiException(HttpStatus.CONFLICT, "USER_DISABLED", "停用账号不能加入项目");
        }
        List<String> previous = jdbcTemplate.queryForList(
                "SELECT project_role FROM project_membership WHERE project_id=? AND user_id=?",
                String.class, projectId, userId);
        if (!previous.isEmpty() && "MANAGER".equals(previous.getFirst()) && !"MANAGER".equals(projectRole)) {
            requireAnotherManager(projectId, userId);
        }
        jdbcTemplate.update("""
                INSERT INTO project_membership (project_id, user_id, project_role)
                VALUES (?, ?, ?)
                ON CONFLICT (project_id, user_id) DO UPDATE SET
                    project_role=EXCLUDED.project_role, updated_at=CURRENT_TIMESTAMP
                """, projectId, userId, projectRole);
        auditService.record(previous.isEmpty() ? "PROJECT_MEMBER_ADDED" : "PROJECT_MEMBER_UPDATED",
                "USER", userId, projectId, "SUCCESS", Map.of("project_role", projectRole));
        return member(projectId, userId);
    }

    @Transactional
    void deleteMember(UUID projectId, UUID userId) {
        accessService.requireManager(projectId);
        ProjectMemberView current = member(projectId, userId);
        if ("MANAGER".equals(current.projectRole())) {
            requireAnotherManager(projectId, userId);
        }
        jdbcTemplate.update("DELETE FROM project_membership WHERE project_id=? AND user_id=?", projectId, userId);
        auditService.record("PROJECT_MEMBER_REMOVED", "USER", userId, projectId, "SUCCESS",
                Map.of("project_role", current.projectRole()));
    }

    private UserView getUser(UUID userId) {
        List<UserView> users = jdbcTemplate.query(userSelect() + " WHERE user_id=?",
                (resultSet, rowNumber) -> user(resultSet), userId);
        if (users.isEmpty()) {
            throw new BusinessApiException(HttpStatus.NOT_FOUND, "USER_NOT_FOUND", "账号不存在");
        }
        return users.getFirst();
    }

    private ProjectMemberView member(UUID projectId, UUID userId) {
        return listMembers(projectId).stream().filter(item -> item.userId().equals(userId)).findFirst()
                .orElseThrow(() -> new BusinessApiException(HttpStatus.NOT_FOUND,
                        "PROJECT_MEMBER_NOT_FOUND", "项目成员不存在"));
    }

    private void requireAnotherAdmin(UUID excluded) {
        Long count = jdbcTemplate.queryForObject("""
                SELECT COUNT(*) FROM user_account
                 WHERE global_role='SYSTEM_ADMIN' AND status='ACTIVE' AND user_id<>?
                """, Long.class, excluded);
        if (count == null || count == 0) {
            throw new BusinessApiException(HttpStatus.CONFLICT, "LAST_SYSTEM_ADMIN", "不能停用或降级最后一个系统管理员");
        }
    }

    private void requireAnotherManager(UUID projectId, UUID excluded) {
        Long count = jdbcTemplate.queryForObject("""
                SELECT COUNT(*) FROM project_membership m JOIN user_account u ON u.user_id=m.user_id
                 WHERE m.project_id=? AND m.project_role='MANAGER' AND u.status='ACTIVE' AND m.user_id<>?
                """, Long.class, projectId, excluded);
        if (count == null || count == 0) {
            throw new BusinessApiException(HttpStatus.CONFLICT, "LAST_PROJECT_MANAGER", "不能移除项目最后一个有效负责人");
        }
    }

    private void requireNoOrphanedProject(UUID excluded) {
        List<UUID> projects = jdbcTemplate.queryForList("""
                SELECT target.project_id
                  FROM project_membership target
                 WHERE target.user_id=? AND target.project_role='MANAGER'
                   AND NOT EXISTS (
                       SELECT 1 FROM project_membership other
                       JOIN user_account u ON u.user_id=other.user_id
                        WHERE other.project_id=target.project_id
                          AND other.user_id<>target.user_id
                          AND other.project_role='MANAGER' AND u.status='ACTIVE')
                 LIMIT 1
                """, UUID.class, excluded);
        if (!projects.isEmpty()) {
            throw new BusinessApiException(HttpStatus.CONFLICT, "LAST_PROJECT_MANAGER",
                    "不能停用仍是项目最后一个有效负责人的账号");
        }
    }

    private UserView user(java.sql.ResultSet resultSet) throws java.sql.SQLException {
        return new UserView(resultSet.getObject("user_id", UUID.class), resultSet.getString("username"),
                resultSet.getString("display_name"), resultSet.getString("global_role"),
                resultSet.getString("status"), resultSet.getBoolean("must_change_password"),
                resultSet.getObject("locked_until", OffsetDateTime.class),
                resultSet.getObject("created_at", OffsetDateTime.class));
    }

    private String userSelect() {
        return "SELECT user_id, username, display_name, global_role, status, must_change_password, locked_until, created_at FROM user_account";
    }

    private String text(String value, String field, int max) {
        String result = value == null ? "" : value.trim();
        if (result.isEmpty() || result.length() > max) throw invalid(field + " 长度必须为 1 到 " + max);
        return result;
    }

    private String allowed(String value, Set<String> values, String field) {
        if (value == null || !values.contains(value)) throw invalid(field + " 取值非法");
        return value;
    }

    private BusinessApiException invalid(String message) {
        return new BusinessApiException(HttpStatus.BAD_REQUEST, "USER_REQUEST_INVALID", message);
    }
}
