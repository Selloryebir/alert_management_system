package com.alertmanagement.backend.audit;

import com.fasterxml.jackson.annotation.JsonProperty;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import java.util.UUID;

record AuditPage(int page, int size, long total, List<AuditEventView> items) {
}

record AuditEventView(
        @JsonProperty("event_id") UUID eventId,
        @JsonProperty("event_type") String eventType,
        @JsonProperty("occurred_at") OffsetDateTime occurredAt,
        String operator,
        @JsonProperty("target_type") String targetType,
        @JsonProperty("target_id") UUID targetId,
        String result,
        @JsonProperty("trace_id") UUID traceId,
        @JsonProperty("actor_user_id") UUID actorUserId,
        @JsonProperty("project_id") UUID projectId,
        Map<String, Object> details) {
}

final class AuditViews {
    private AuditViews() {
    }
}
