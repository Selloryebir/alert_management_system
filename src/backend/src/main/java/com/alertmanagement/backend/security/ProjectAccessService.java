package com.alertmanagement.backend.security;

import com.alertmanagement.backend.api.BusinessApiException;
import java.util.List;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

@Service
public class ProjectAccessService {

    private final JdbcTemplate jdbcTemplate;
    private final CurrentActor currentActor;

    public ProjectAccessService(JdbcTemplate jdbcTemplate, CurrentActor currentActor) {
        this.jdbcTemplate = jdbcTemplate;
        this.currentActor = currentActor;
    }

    public Actor actor() {
        return currentActor.require();
    }

    public void requireSystemAdmin() {
        if (!actor().systemAdmin()) {
            throw forbidden("只有系统管理员可以执行此操作");
        }
    }

    public String requireRead(UUID projectId) {
        requireProjectExists(projectId);
        Actor actor = actor();
        if (actor.systemAdmin()) {
            return "SYSTEM_ADMIN";
        }
        return membershipRole(projectId, actor.userId()).orElseThrow(this::notFound);
    }

    public String requireManager(UUID projectId) {
        String role = requireRead(projectId);
        if (!"SYSTEM_ADMIN".equals(role) && !"MANAGER".equals(role)) {
            throw forbidden("只有项目负责人可以执行此操作");
        }
        return role;
    }

    public UUID requireBatch(UUID batchId) {
        UUID projectId = singleProject("SELECT project_id FROM import_batch WHERE batch_id = ?", batchId);
        requireRead(projectId);
        return projectId;
    }

    public UUID requireRun(UUID runId) {
        UUID projectId = singleProject("""
                SELECT b.project_id FROM analysis_run r
                JOIN import_batch b ON b.batch_id = r.batch_id
                WHERE r.run_id = ?
                """, runId);
        requireRead(projectId);
        return projectId;
    }

    public void requireAssignee(UUID projectId, String username) {
        if (username == null || actor().userId() == null) {
            return;
        }
        Boolean activeMember = jdbcTemplate.queryForObject("""
                SELECT EXISTS (
                    SELECT 1 FROM project_membership m JOIN user_account u ON u.user_id=m.user_id
                     WHERE m.project_id=? AND u.username=? AND u.status='ACTIVE')
                """, Boolean.class, projectId, username.trim().toLowerCase(java.util.Locale.ROOT));
        if (!Boolean.TRUE.equals(activeMember)) {
            throw new BusinessApiException(HttpStatus.BAD_REQUEST, "ASSIGNEE_INVALID", "责任人必须是该项目的有效成员账号");
        }
    }

    public String projectRole(UUID projectId) {
        return requireRead(projectId);
    }

    private void requireProjectExists(UUID projectId) {
        Boolean exists = jdbcTemplate.queryForObject(
                "SELECT EXISTS (SELECT 1 FROM business_project WHERE project_id=?)", Boolean.class, projectId);
        if (!Boolean.TRUE.equals(exists)) {
            throw notFound();
        }
    }

    private java.util.Optional<String> membershipRole(UUID projectId, UUID userId) {
        List<String> roles = jdbcTemplate.queryForList("""
                SELECT m.project_role FROM project_membership m JOIN user_account u ON u.user_id=m.user_id
                 WHERE m.project_id=? AND m.user_id=? AND u.status='ACTIVE'
                """, String.class, projectId, userId);
        return roles.stream().findFirst();
    }

    private UUID singleProject(String sql, UUID id) {
        List<UUID> projects = jdbcTemplate.queryForList(sql, UUID.class, id);
        if (projects.isEmpty()) {
            throw notFound();
        }
        return projects.getFirst();
    }

    private BusinessApiException notFound() {
        return new BusinessApiException(HttpStatus.NOT_FOUND, "RESOURCE_NOT_FOUND", "资源不存在或无权访问");
    }

    private BusinessApiException forbidden(String message) {
        return new BusinessApiException(HttpStatus.FORBIDDEN, "PERMISSION_DENIED", message);
    }
}
