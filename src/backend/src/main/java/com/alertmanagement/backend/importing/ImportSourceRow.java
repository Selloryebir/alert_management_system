package com.alertmanagement.backend.importing;

import com.fasterxml.jackson.annotation.JsonProperty;
import java.util.Map;

public record ImportSourceRow(
        @JsonProperty("source_row") int sourceRow,
        Map<String, String> values) {
}
