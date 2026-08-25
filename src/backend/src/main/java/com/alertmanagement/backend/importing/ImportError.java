package com.alertmanagement.backend.importing;

import com.fasterxml.jackson.annotation.JsonProperty;

public record ImportError(
        @JsonProperty("source_row") int sourceRow,
        String field,
        String code,
        String message) {
}
