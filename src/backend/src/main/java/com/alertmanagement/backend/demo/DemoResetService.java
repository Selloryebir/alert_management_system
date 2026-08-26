package com.alertmanagement.backend.demo;

import com.alertmanagement.backend.api.BusinessApiException;
import com.alertmanagement.backend.audit.AuditService;
import com.alertmanagement.backend.security.ProjectAccessService;
import com.alertmanagement.backend.security.SecurityProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import java.time.OffsetDateTime;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
class DemoResetService {

    private static final String CONFIRMATION = "RESET_DEMO";
    private static final UUID DEFAULT_PROJECT_ID =
            UUID.fromString("00000000-0000-0000-0000-000000000001");
    private static final List<String> BUSINESS_TABLES = List.of(
            "disposition_history",
            "alarm_disposition",
            "event_chain_member",
            "event_chain",
            "analysis_result_override",
            "analysis_result",
            "analysis_run",
            "alarm_record",
            "import_staging",
            "import_batch",
            "audit_event");

    private final JdbcTemplate jdbcTemplate;
    private final ProjectAccessService accessService;
    private final SecurityProperties securityProperties;
    private final AuditService auditService;

    DemoResetService(JdbcTemplate jdbcTemplate, ProjectAccessService accessService,
            SecurityProperties securityProperties, AuditService auditService) {
        this.jdbcTemplate = jdbcTemplate;
        this.accessService = accessService;
        this.securityProperties = securityProperties;
        this.auditService = auditService;
    }

    @Transactional
    public DemoResetView reset(DemoResetRequest request) {
        accessService.requireSystemAdmin();
        if (securityProperties.networkMode()) {
            throw new BusinessApiException(HttpStatus.FORBIDDEN, "DEMO_RESET_LOCAL_ONLY", "演示复位只允许在本机模式执行");
        }
        if (request == null || !CONFIRMATION.equals(request.confirmation())) {
            throw badRequest("confirmation 必须是 RESET_DEMO");
        }
        jdbcTemplate.execute("LOCK TABLE " + String.join(", ", BUSINESS_TABLES) + ", business_project"
                + " IN ACCESS EXCLUSIVE MODE");
        int analyzing = jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM analysis_run WHERE status = 'ANALYZING'", Integer.class);
        if (analyzing > 0) {
            throw new BusinessApiException(HttpStatus.CONFLICT, "DEMO_RESET_ANALYSIS_ACTIVE",
                    "存在正在分析的运行，暂不能复位");
        }
        Map<String, Long> deleted = new LinkedHashMap<>();
        for (String table : BUSINESS_TABLES) {
            deleted.put(table, jdbcTemplate.queryForObject("SELECT COUNT(*) FROM " + table, Long.class));
        }
        deleted.put("business_project", jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM business_project WHERE project_id <> ?", Long.class, DEFAULT_PROJECT_ID));
        jdbcTemplate.execute("TRUNCATE TABLE " + String.join(", ", BUSINESS_TABLES) + " RESTART IDENTITY");
        jdbcTemplate.update("DELETE FROM business_project WHERE project_id <> ?", DEFAULT_PROJECT_ID);
        jdbcTemplate.update("""
                INSERT INTO business_project (
                    project_id, code, name, client_name, site, unit_name, status,
                    report_title, report_fields, validation_rules
                ) VALUES (
                    ?, 'DEFAULT-DEMO', '默认演示项目', '演示客户', '合成厂区', '演示装置', 'ACTIVE',
                    '报警分析报告',
                    '["summary","priority","area","unit","noise","cause","disposition","chains"]'::jsonb,
                    '{"required_fields":[]}'::jsonb
                )
                ON CONFLICT (project_id) DO UPDATE SET
                    code=EXCLUDED.code,
                    name=EXCLUDED.name,
                    client_name=EXCLUDED.client_name,
                    site=EXCLUDED.site,
                    unit_name=EXCLUDED.unit_name,
                    status=EXCLUDED.status,
                    report_title=EXCLUDED.report_title,
                    report_fields=EXCLUDED.report_fields,
                    validation_rules=EXCLUDED.validation_rules,
                    updated_at=business_project.created_at
                """, DEFAULT_PROJECT_ID);
        auditService.record("DEMO_RESET", "SYSTEM", null, DEFAULT_PROJECT_ID, "SUCCESS",
                Map.of("deleted_counts", deleted));
        return new DemoResetView(OffsetDateTime.now(), "EMPTY", deleted);
    }

    private BusinessApiException badRequest(String message) {
        return new BusinessApiException(HttpStatus.BAD_REQUEST, "DEMO_RESET_REQUEST_INVALID", message);
    }
}

record DemoResetRequest(String operator, String confirmation) {
}

record DemoResetView(
        @JsonProperty("completed_at") OffsetDateTime completedAt,
        @JsonProperty("business_state") String businessState,
        @JsonProperty("deleted_counts") Map<String, Long> deletedCounts) {
}
