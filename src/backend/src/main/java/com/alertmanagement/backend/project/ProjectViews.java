package com.alertmanagement.backend.project;

import com.fasterxml.jackson.annotation.JsonProperty;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.UUID;

record ProjectView(
        @JsonProperty("project_id") UUID projectId,
        String code,
        String name,
        @JsonProperty("client_name") String clientName,
        String site,
        @JsonProperty("unit_name") String unitName,
        String status,
        @JsonProperty("report_title") String reportTitle,
        @JsonProperty("report_fields") List<String> reportFields,
        @JsonProperty("validation_rules") ProjectValidationRules validationRules,
        @JsonProperty("created_at") OffsetDateTime createdAt,
        @JsonProperty("updated_at") OffsetDateTime updatedAt,
        @JsonProperty("project_role") String projectRole,
        ProjectStatistics statistics) {
}

record ProjectStatistics(
        @JsonProperty("batch_count") long batchCount,
        @JsonProperty("alarm_count") long alarmCount,
        @JsonProperty("valid_alarm_count") long validAlarmCount,
        @JsonProperty("invalid_alarm_count") long invalidAlarmCount,
        @JsonProperty("pending_disposition_count") long pendingDispositionCount) {
}

record ProjectOverview(
        @JsonProperty("project_id") UUID projectId,
        ProjectStatistics statistics,
        @JsonProperty("recent_tasks") List<ProjectTask> recentTasks) {
}

record ProjectTask(
        String type,
        UUID id,
        String status,
        @JsonProperty("occurred_at") OffsetDateTime occurredAt) {
}

record ProjectRequest(
        String code,
        String name,
        @JsonProperty("client_name") String clientName,
        String site,
        @JsonProperty("unit_name") String unitName,
        @JsonProperty("report_title") String reportTitle,
        @JsonProperty("report_fields") List<String> reportFields,
        @JsonProperty("validation_rules") ProjectValidationRules validationRules) {
}

record ProjectPatch(
        String code,
        String name,
        @JsonProperty("client_name") String clientName,
        String site,
        @JsonProperty("unit_name") String unitName,
        @JsonProperty("report_title") String reportTitle,
        @JsonProperty("report_fields") List<String> reportFields,
        @JsonProperty("validation_rules") ProjectValidationRules validationRules) {
}

final class ProjectViews {
    private ProjectViews() {
    }
}
