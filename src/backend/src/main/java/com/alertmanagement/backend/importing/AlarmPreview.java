package com.alertmanagement.backend.importing;

import com.fasterxml.jackson.annotation.JsonProperty;
import java.math.BigDecimal;
import java.time.OffsetDateTime;
import java.util.Map;

public record AlarmPreview(
        @JsonProperty("source_row") int sourceRow,
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
        @JsonProperty("raw_payload") Map<String, String> rawPayload) {
}
