package com.alertmanagement.backend.importing;

import java.math.BigDecimal;
import java.time.OffsetDateTime;
import java.util.Map;
import java.util.UUID;

record NormalizedAlarm(
        UUID recordId,
        int sourceRow,
        OffsetDateTime eventTime,
        OffsetDateTime returnTime,
        OffsetDateTime ackTime,
        String site,
        String area,
        String unit,
        String tag,
        String description,
        String priority,
        String state,
        BigDecimal value,
        BigDecimal threshold,
        String engineeringUnit,
        String sourceSystem,
        String operator,
        Map<String, String> rawPayload) {
}
