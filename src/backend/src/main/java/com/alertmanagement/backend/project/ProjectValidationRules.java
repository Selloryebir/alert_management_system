package com.alertmanagement.backend.project;

import com.fasterxml.jackson.annotation.JsonProperty;
import java.math.BigDecimal;
import java.util.List;

public record ProjectValidationRules(
        @JsonProperty("required_fields") List<String> requiredFields,
        @JsonProperty("value_min") BigDecimal valueMin,
        @JsonProperty("value_max") BigDecimal valueMax,
        @JsonProperty("threshold_min") BigDecimal thresholdMin,
        @JsonProperty("threshold_max") BigDecimal thresholdMax) {
}
