package com.alertmanagement.backend.api;

import com.fasterxml.jackson.annotation.JsonProperty;

public record ApiError(
        String code,
        String message,
        @JsonProperty("trace_id") String traceId) {
}
