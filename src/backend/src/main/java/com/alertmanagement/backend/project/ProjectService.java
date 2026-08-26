package com.alertmanagement.backend.project;

import com.alertmanagement.backend.api.BusinessApiException;
import com.alertmanagement.backend.audit.AuditService;
import com.alertmanagement.backend.security.Actor;
import com.alertmanagement.backend.security.ProjectAccessService;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.charset.StandardCharsets;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.dao.EmptyResultDataAccessException;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class ProjectService {

    public static final UUID DEFAULT_PROJECT_ID = UUID.fromString("00000000-0000-0000-0000-000000000001");
    public static final List<String> DEFAULT_REPORT_FIELDS = List.of(
            "summary", "priority", "area", "unit", "noise", "cause", "disposition", "chains");
    private static final Set<String> REPORT_FIELDS = Set.copyOf(DEFAULT_REPORT_FIELDS);
    private static final Set<String> VALIDATION_FIELDS = Set.of(
            "event_time", "return_time", "ack_time", "site", "area", "unit", "tag", "description",
            "priority", "state", "value", "threshold", "engineering_unit", "source_system", "operator");
    private static final TypeReference<List<String>> STRING_LIST = new TypeReference<>() { };

    private final JdbcTemplate jdbcTemplate;
    private final ObjectMapper objectMapper;
    private final AuditService auditService;
    private final ProjectAccessService accessService;

    public ProjectService(JdbcTemplate jdbcTemplate, ObjectMapper objectMapper, AuditService auditService,
            ProjectAccessService accessService) {
        this.jdbcTemplate = jdbcTemplate;
        this.objectMapper = objectMapper;
        this.auditService = auditService;
        this.accessService = accessService;
    }

    List<ProjectView> list(String q, boolean includeArchived) {
        Actor actor = accessService.actor();
        if (q != null && q.length() > 160) {
            throw badRequest("q 长度不能超过 160");
        }
        String query = q == null ? null : q.trim();
        StringBuilder where = new StringBuilder(" WHERE 1=1");
        List<Object> arguments = new ArrayList<>();
        if (!includeArchived) {
            where.append(" AND status = 'ACTIVE'");
        }
        if (!actor.systemAdmin()) {
            where.append(" AND EXISTS (SELECT 1 FROM project_membership m"
                    + " WHERE m.project_id=business_project.project_id AND m.user_id=?)");
            arguments.add(actor.userId());
        }
        if (query != null && !query.isEmpty()) {
            where.append(" AND (code ILIKE ? OR name ILIKE ? OR client_name ILIKE ? OR site ILIKE ? OR unit_name ILIKE ?)");
            for (int i = 0; i < 5; i++) {
                arguments.add("%" + query + "%");
            }
        }
        return jdbcTemplate.query(projectSelect() + where + " ORDER BY updated_at DESC, project_id",
                (resultSet, rowNumber) -> project(resultSet, false), arguments.toArray());
    }

    @Transactional
    ProjectView create(ProjectRequest request) {
        accessService.requireSystemAdmin();
        if (request == null) {
            throw badRequest("请求体不能为空");
        }
        ProjectData data = new ProjectData(
                required(request.code(), "code", 80), required(request.name(), "name", 160),
                required(request.clientName(), "client_name", 160), required(request.site(), "site", 100),
                required(request.unitName(), "unit_name", 100),
                optionalDefault(request.reportTitle(), "报警分析报告", "report_title", 200),
                reportFields(request.reportFields()), validationRules(request.validationRules()));
        UUID projectId = UUID.randomUUID();
        requireUnique(data.code(), data.name(), null);
        try {
            jdbcTemplate.update("""
                    INSERT INTO business_project (
                        project_id, code, name, client_name, site, unit_name, status,
                        report_title, report_fields, validation_rules
                    ) VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', ?, CAST(? AS jsonb), CAST(? AS jsonb))
                    """, projectId, data.code(), data.name(), data.clientName(), data.site(), data.unitName(),
                    data.reportTitle(), writeJson(data.reportFields()), writeJson(data.validationRules()));
        } catch (DuplicateKeyException exception) {
            throw duplicate(exception);
        }
        Actor actor = accessService.actor();
        if (actor.userId() != null) {
            jdbcTemplate.update("""
                    INSERT INTO project_membership (project_id, user_id, project_role)
                    VALUES (?, ?, 'MANAGER') ON CONFLICT (project_id, user_id) DO NOTHING
                    """, projectId, actor.userId());
        }
        auditService.record("PROJECT_CREATED", "PROJECT", projectId, projectId, "SUCCESS",
                Map.of("code", data.code(), "name", data.name()));
        return get(projectId);
    }

    public ProjectView get(UUID projectId) {
        accessService.requireRead(projectId);
        try {
            return jdbcTemplate.queryForObject(projectSelect() + " WHERE project_id = ?",
                    (resultSet, rowNumber) -> project(resultSet, true), projectId);
        } catch (EmptyResultDataAccessException exception) {
            throw notFound();
        }
    }

    @Transactional
    ProjectView update(UUID projectId, ProjectPatch patch) {
        accessService.requireManager(projectId);
        if (patch == null) {
            throw badRequest("请求体不能为空");
        }
        ProjectView current = get(projectId);
        if (!"ACTIVE".equals(current.status())) {
            throw new BusinessApiException(HttpStatus.CONFLICT, "PROJECT_ARCHIVED", "项目已归档，不能修改");
        }
        ProjectData data = new ProjectData(
                patch.code() == null ? current.code() : required(patch.code(), "code", 80),
                patch.name() == null ? current.name() : required(patch.name(), "name", 160),
                patch.clientName() == null ? current.clientName() : required(patch.clientName(), "client_name", 160),
                patch.site() == null ? current.site() : required(patch.site(), "site", 100),
                patch.unitName() == null ? current.unitName() : required(patch.unitName(), "unit_name", 100),
                patch.reportTitle() == null ? current.reportTitle()
                        : required(patch.reportTitle(), "report_title", 200),
                patch.reportFields() == null ? current.reportFields() : reportFields(patch.reportFields()),
                patch.validationRules() == null ? current.validationRules()
                        : validationRules(patch.validationRules()));
        requireUnique(data.code(), data.name(), projectId);
        try {
            jdbcTemplate.update("""
                    UPDATE business_project
                       SET code=?, name=?, client_name=?, site=?, unit_name=?, report_title=?,
                           report_fields=CAST(? AS jsonb), validation_rules=CAST(? AS jsonb),
                           updated_at=CURRENT_TIMESTAMP
                     WHERE project_id=?
                    """, data.code(), data.name(), data.clientName(), data.site(), data.unitName(),
                    data.reportTitle(), writeJson(data.reportFields()), writeJson(data.validationRules()), projectId);
        } catch (DuplicateKeyException exception) {
            throw duplicate(exception);
        }
        auditService.record("PROJECT_UPDATED", "PROJECT", projectId, projectId, "SUCCESS",
                Map.of("before", projectSnapshot(current), "after", projectSnapshot(data)));
        return get(projectId);
    }

    @Transactional
    ProjectView setArchived(UUID projectId, boolean archived) {
        accessService.requireManager(projectId);
        ProjectView current = get(projectId);
        String target = archived ? "ARCHIVED" : "ACTIVE";
        if (target.equals(current.status())) {
            throw new BusinessApiException(HttpStatus.CONFLICT, "PROJECT_STATUS_CONFLICT",
                    archived ? "项目已经归档" : "项目已经恢复");
        }
        jdbcTemplate.update("UPDATE business_project SET status=?, updated_at=CURRENT_TIMESTAMP WHERE project_id=?",
                target, projectId);
        auditService.record(archived ? "PROJECT_ARCHIVED" : "PROJECT_RESTORED",
                "PROJECT", projectId, projectId, "SUCCESS",
                Map.of("from_status", current.status(), "to_status", target));
        return get(projectId);
    }

    @Transactional
    void delete(UUID projectId) {
        accessService.requireSystemAdmin();
        ProjectView project = get(projectId);
        if (!"ARCHIVED".equals(project.status())) {
            throw new BusinessApiException(HttpStatus.CONFLICT, "PROJECT_DELETE_CONFLICT", "只有已归档项目可以删除");
        }
        long count = jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM import_batch WHERE project_id=?", Long.class, projectId);
        if (count != 0) {
            throw new BusinessApiException(HttpStatus.CONFLICT, "PROJECT_DELETE_CONFLICT",
                    "项目已经产生业务数据，不能删除");
        }
        auditService.record("PROJECT_DELETED", "PROJECT", projectId, projectId, "SUCCESS",
                Map.of("code", project.code(), "name", project.name()));
        jdbcTemplate.update("DELETE FROM business_project WHERE project_id=?", projectId);
    }

    ProjectOverview overview(UUID projectId) {
        accessService.requireRead(projectId);
        get(projectId);
        ProjectStatistics statistics = statistics(projectId);
        List<ProjectTask> tasks = jdbcTemplate.query("""
                SELECT 'IMPORT' AS task_type, batch_id AS task_id, status, created_at AS occurred_at
                  FROM import_batch WHERE project_id=?
                UNION ALL
                SELECT 'ANALYSIS', r.run_id, r.status, r.started_at
                  FROM analysis_run r JOIN import_batch b ON b.batch_id=r.batch_id
                 WHERE b.project_id=?
                 ORDER BY occurred_at DESC, task_id DESC LIMIT 10
                """, (resultSet, rowNumber) -> new ProjectTask(
                resultSet.getString("task_type"), resultSet.getObject("task_id", UUID.class),
                resultSet.getString("status"), resultSet.getObject("occurred_at", OffsetDateTime.class)),
                projectId, projectId);
        return new ProjectOverview(projectId, statistics, tasks);
    }

    ProjectManifest export(UUID projectId) {
        accessService.requireRead(projectId);
        ProjectView project = get(projectId);
        ProjectOverview overview = overview(projectId);
        Map<String, Object> manifest = new LinkedHashMap<>();
        manifest.put("manifest_type", "PROJECT_SUMMARY");
        manifest.put("exported_at", OffsetDateTime.now());
        manifest.put("project", project);
        manifest.put("statistics", overview.statistics());
        manifest.put("recent_tasks", overview.recentTasks());
        String safeCode = project.code().replaceAll("[^A-Za-z0-9._-]", "_");
        return new ProjectManifest((safeCode.isBlank() ? "project" : safeCode) + "-manifest.json",
                writeJson(manifest).getBytes(StandardCharsets.UTF_8));
    }

    public ProjectValidationRules requireActive(UUID projectId) {
        accessService.requireRead(projectId);
        ProjectView project = get(projectId);
        if (!"ACTIVE".equals(project.status())) {
            throw new BusinessApiException(HttpStatus.CONFLICT, "PROJECT_ARCHIVED", "项目已归档，不能新增业务数据");
        }
        return project.validationRules();
    }

    private ProjectStatistics statistics(UUID projectId) {
        return jdbcTemplate.queryForObject("""
                SELECT COUNT(DISTINCT b.batch_id) AS batch_count,
                       COUNT(a.record_id) AS alarm_count,
                       COUNT(a.record_id) FILTER (WHERE a.invalidated_at IS NULL) AS valid_count,
                       COUNT(a.record_id) FILTER (WHERE a.invalidated_at IS NOT NULL) AS invalid_count,
                       COUNT(r.record_id) FILTER (WHERE COALESCE(d.status, 'OPEN') <> 'CLOSED') AS pending_count
                  FROM business_project p
                  LEFT JOIN import_batch b ON b.project_id=p.project_id
                  LEFT JOIN alarm_record a ON a.batch_id=b.batch_id
                  LEFT JOIN analysis_result r ON r.record_id=a.record_id
                  LEFT JOIN alarm_disposition d ON d.run_id=r.run_id AND d.record_id=r.record_id
                 WHERE p.project_id=?
                """, (resultSet, rowNumber) -> new ProjectStatistics(
                resultSet.getLong("batch_count"), resultSet.getLong("alarm_count"),
                resultSet.getLong("valid_count"), resultSet.getLong("invalid_count"),
                resultSet.getLong("pending_count")), projectId);
    }

    private ProjectView project(ResultSet resultSet, boolean includeStatistics) throws SQLException {
        UUID projectId = resultSet.getObject("project_id", UUID.class);
        return new ProjectView(projectId, resultSet.getString("code"), resultSet.getString("name"),
                resultSet.getString("client_name"), resultSet.getString("site"), resultSet.getString("unit_name"),
                resultSet.getString("status"), resultSet.getString("report_title"),
                readJson(resultSet.getString("report_fields"), STRING_LIST),
                readJson(resultSet.getString("validation_rules"), ProjectValidationRules.class),
                resultSet.getObject("created_at", OffsetDateTime.class),
                resultSet.getObject("updated_at", OffsetDateTime.class),
                accessService.projectRole(projectId), includeStatistics ? statistics(projectId) : null);
    }

    private String projectSelect() {
        return """
                SELECT project_id, code, name, client_name, site, unit_name, status,
                       report_title, report_fields::text, validation_rules::text, created_at, updated_at
                  FROM business_project
                """;
    }

    private List<String> reportFields(List<String> fields) {
        List<String> actual = fields == null ? DEFAULT_REPORT_FIELDS : fields;
        if (actual.isEmpty() || actual.stream().anyMatch(field -> field == null || !REPORT_FIELDS.contains(field))
                || new java.util.HashSet<>(actual).size() != actual.size()) {
            throw badRequest("report_fields 必须是允许字段组成的非空无重复列表");
        }
        return List.copyOf(actual);
    }

    private ProjectValidationRules validationRules(ProjectValidationRules rules) {
        if (rules == null) {
            return new ProjectValidationRules(List.of(), null, null, null, null);
        }
        List<String> required = rules.requiredFields() == null ? List.of() : rules.requiredFields();
        if (required.stream().anyMatch(field -> field == null || !VALIDATION_FIELDS.contains(field))
                || new java.util.HashSet<>(required).size() != required.size()) {
            throw badRequest("validation_rules.required_fields 含非法或重复字段");
        }
        if (rules.valueMin() != null && rules.valueMax() != null
                && rules.valueMin().compareTo(rules.valueMax()) > 0) {
            throw badRequest("value_min 不能大于 value_max");
        }
        if (rules.thresholdMin() != null && rules.thresholdMax() != null
                && rules.thresholdMin().compareTo(rules.thresholdMax()) > 0) {
            throw badRequest("threshold_min 不能大于 threshold_max");
        }
        return new ProjectValidationRules(List.copyOf(required), rules.valueMin(), rules.valueMax(),
                rules.thresholdMin(), rules.thresholdMax());
    }

    private String required(String value, String field, int maximumLength) {
        if (value == null || value.isBlank()) {
            throw badRequest(field + " 不能为空");
        }
        String trimmed = value.trim();
        if (trimmed.length() > maximumLength) {
            throw badRequest(field + " 长度不能超过 " + maximumLength);
        }
        return trimmed;
    }

    private String optionalDefault(String value, String fallback, String field, int maximumLength) {
        return value == null ? fallback : required(value, field, maximumLength);
    }

    private Map<String, Object> projectSnapshot(ProjectView project) {
        return Map.of("code", project.code(), "name", project.name(), "client_name", project.clientName(),
                "site", project.site(), "unit_name", project.unitName(), "report_title", project.reportTitle(),
                "report_fields", project.reportFields(), "validation_rules", project.validationRules());
    }

    private Map<String, Object> projectSnapshot(ProjectData project) {
        return Map.of("code", project.code(), "name", project.name(), "client_name", project.clientName(),
                "site", project.site(), "unit_name", project.unitName(), "report_title", project.reportTitle(),
                "report_fields", project.reportFields(), "validation_rules", project.validationRules());
    }

    private String writeJson(Object value) {
        try {
            return objectMapper.writeValueAsString(value);
        } catch (JsonProcessingException exception) {
            throw new IllegalStateException("项目 JSON 序列化失败", exception);
        }
    }

    private <T> T readJson(String value, TypeReference<T> type) {
        try {
            return objectMapper.readValue(value, type);
        } catch (JsonProcessingException exception) {
            throw new IllegalStateException("项目 JSON 反序列化失败", exception);
        }
    }

    private <T> T readJson(String value, Class<T> type) {
        try {
            return objectMapper.readValue(value, type);
        } catch (JsonProcessingException exception) {
            throw new IllegalStateException("项目 JSON 反序列化失败", exception);
        }
    }

    private BusinessApiException badRequest(String message) {
        return new BusinessApiException(HttpStatus.BAD_REQUEST, "PROJECT_REQUEST_INVALID", message);
    }

    private BusinessApiException notFound() {
        return new BusinessApiException(HttpStatus.NOT_FOUND, "PROJECT_NOT_FOUND", "项目不存在");
    }

    private void requireUnique(String code, String name, UUID exceptProjectId) {
        String suffix = exceptProjectId == null ? "" : " AND project_id <> ?";
        Object[] arguments = exceptProjectId == null ? new Object[] {code} : new Object[] {code, exceptProjectId};
        Boolean codeExists = jdbcTemplate.queryForObject(
                "SELECT EXISTS (SELECT 1 FROM business_project WHERE code=?" + suffix + ")",
                Boolean.class, arguments);
        if (Boolean.TRUE.equals(codeExists)) {
            throw new BusinessApiException(HttpStatus.CONFLICT, "PROJECT_CODE_CONFLICT", "项目编号已存在");
        }
        arguments = exceptProjectId == null ? new Object[] {name} : new Object[] {name, exceptProjectId};
        Boolean nameExists = jdbcTemplate.queryForObject(
                "SELECT EXISTS (SELECT 1 FROM business_project WHERE name=?" + suffix + ")",
                Boolean.class, arguments);
        if (Boolean.TRUE.equals(nameExists)) {
            throw new BusinessApiException(HttpStatus.CONFLICT, "PROJECT_NAME_CONFLICT", "项目名称已存在");
        }
    }

    private BusinessApiException duplicate(DuplicateKeyException exception) {
        if (exception.getMessage() != null && exception.getMessage().contains("business_project_code_key")) {
            return new BusinessApiException(HttpStatus.CONFLICT, "PROJECT_CODE_CONFLICT", "项目编号已存在");
        }
        return new BusinessApiException(HttpStatus.CONFLICT, "PROJECT_NAME_CONFLICT", "项目名称已存在");
    }

    record ProjectManifest(String fileName, byte[] content) {
    }

    private record ProjectData(
            String code, String name, String clientName, String site, String unitName,
            String reportTitle, List<String> reportFields, ProjectValidationRules validationRules) {
    }
}
