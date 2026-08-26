package com.alertmanagement.backend.project;

import com.fasterxml.jackson.annotation.JsonProperty;
import java.math.BigDecimal;
import java.time.OffsetDateTime;
import java.util.Map;
import java.util.UUID;

record ManualAlarmView(
        @JsonProperty("project_id") UUID projectId,
        @JsonProperty("batch_id") UUID batchId,
        @JsonProperty("record_id") UUID recordId,
        @JsonProperty("event_time") OffsetDateTime eventTime,
        @JsonProperty("return_time") OffsetDateTime returnTime,
        @JsonProperty("ack_time") OffsetDateTime ackTime,
        String site,
        String area,
        String unit,
        String tag,
        String description,
        String priority,
        String state,
        BigDecimal value,
        BigDecimal threshold,
        @JsonProperty("engineering_unit") String engineeringUnit,
        @JsonProperty("source_system") String sourceSystem,
        String operator,
        @JsonProperty("raw_payload") Map<String, Object> rawPayload,
        @JsonProperty("invalidated_at") OffsetDateTime invalidatedAt,
        @JsonProperty("invalidated_by") String invalidatedBy,
        @JsonProperty("invalidation_reason") String invalidationReason) {
}

record ManualAlarmRequest(
        @JsonProperty("event_time") OffsetDateTime eventTime,
        @JsonProperty("return_time") OffsetDateTime returnTime,
        @JsonProperty("ack_time") OffsetDateTime ackTime,
        String site,
        String area,
        String unit,
        String tag,
        String description,
        String priority,
        String state,
        BigDecimal value,
        BigDecimal threshold,
        @JsonProperty("engineering_unit") String engineeringUnit,
        @JsonProperty("source_system") String sourceSystem,
        String operator) {
}

record ManualAlarmPatch(
        @JsonProperty("event_time") OffsetDateTime eventTime,
        @JsonProperty("return_time") OffsetDateTime returnTime,
        @JsonProperty("ack_time") OffsetDateTime ackTime,
        String site,
        String area,
        String unit,
        String tag,
        String description,
        String priority,
        String state,
        BigDecimal value,
        BigDecimal threshold,
        @JsonProperty("engineering_unit") String engineeringUnit,
        @JsonProperty("source_system") String sourceSystem,
        String operator,
        @JsonProperty("edited_by") String editedBy,
        String reason) {
}

record ManualAlarmInvalidation(String operator, String reason) {
}

final class ManualAlarmViews {
    private ManualAlarmViews() {
    }
}
