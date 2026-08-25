package com.alertmanagement.backend.analysis;

import com.fasterxml.jackson.annotation.JsonProperty;
import java.math.BigDecimal;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import java.util.UUID;

public record AnalysisView(
        @JsonProperty("run_id") UUID runId,
        @JsonProperty("batch_id") UUID batchId,
        int attempt,
        AnalysisStatus status,
        String failure,
        @JsonProperty("contract_version") String contractVersion,
        @JsonProperty("algorithm_version") String algorithmVersion,
        @JsonProperty("rule_version") String ruleVersion,
        Map<String, Object> parameters,
        List<Result> results,
        @JsonProperty("event_chains") List<EventChain> eventChains,
        Summary summary,
        @JsonProperty("started_at") OffsetDateTime startedAt,
        @JsonProperty("completed_at") OffsetDateTime completedAt) {

    public record Result(
            @JsonProperty("record_id") UUID recordId,
            @JsonProperty("source_row") int sourceRow,
            @JsonProperty("noise_type") String noiseType,
            @JsonProperty("alarm_class") String alarmClass,
            @JsonProperty("cause_category") String causeCategory,
            BigDecimal score,
            List<String> evidence) {
    }

    public record EventChain(
            @JsonProperty("chain_id") String chainId,
            @JsonProperty("start_record_id") UUID startRecordId,
            @JsonProperty("start_time") OffsetDateTime startTime,
            @JsonProperty("end_time") OffsetDateTime endTime,
            @JsonProperty("association_rule") String associationRule,
            String explanation,
            List<Member> members) {
    }

    public record Member(
            @JsonProperty("record_id") UUID recordId,
            @JsonProperty("source_row") int sourceRow,
            int order) {
    }

    public record Summary(
            @JsonProperty("input_count") int inputCount,
            @JsonProperty("success_count") int successCount,
            @JsonProperty("failure_count") int failureCount,
            @JsonProperty("noise_type_counts") Map<String, Integer> noiseTypeCounts,
            @JsonProperty("cause_category_counts") Map<String, Integer> causeCategoryCounts,
            @JsonProperty("event_chain_count") int eventChainCount) {
    }
}
