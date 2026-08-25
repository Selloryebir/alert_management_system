package com.alertmanagement.backend.demo;

import com.alertmanagement.backend.api.BusinessApiException;
import com.fasterxml.jackson.annotation.JsonProperty;
import java.time.OffsetDateTime;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
class DemoResetService {

    private static final String CONFIRMATION = "RESET_DEMO";
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

    DemoResetService(JdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    @Transactional
    public DemoResetView reset(DemoResetRequest request) {
        if (request == null || request.operator() == null || request.operator().isBlank()) {
            throw badRequest("operator 不能为空");
        }
        if (!CONFIRMATION.equals(request.confirmation())) {
            throw badRequest("confirmation 必须是 RESET_DEMO");
        }
        jdbcTemplate.execute("LOCK TABLE " + String.join(", ", BUSINESS_TABLES)
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
        jdbcTemplate.execute("TRUNCATE TABLE " + String.join(", ", BUSINESS_TABLES) + " RESTART IDENTITY");
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
