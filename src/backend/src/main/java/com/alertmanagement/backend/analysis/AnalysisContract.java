package com.alertmanagement.backend.analysis;

import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.databind.JsonNode;
import java.math.BigDecimal;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import java.util.UUID;

record AnalysisRequest(
        @JsonProperty("analysis_run_id") UUID analysisRunId,
        @JsonProperty("contract_version") String contractVersion,
        @JsonProperty("algorithm_version") String algorithmVersion,
        Map<String, Object> parameters,
        List<AlarmRecordRequest> records) {
}

record AlarmRecordRequest(
        @JsonProperty("record_id") UUID recordId,
        @JsonProperty("batch_id") UUID batchId,
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

record AlgorithmResponse(
        @JsonProperty("analysis_run_id") UUID analysisRunId,
        @JsonProperty("contract_version") String contractVersion,
        @JsonProperty("algorithm_version") String algorithmVersion,
        @JsonProperty("rule_version") String ruleVersion,
        JsonNode parameters,
        @JsonProperty("record_results") List<AlgorithmRecordResult> recordResults,
        @JsonProperty("event_chains") List<AlgorithmEventChain> eventChains,
        AlgorithmSummary summary,
        List<AlgorithmError> errors) {
}

record AlgorithmRecordResult(
        @JsonProperty("record_id") UUID recordId,
        @JsonProperty("noise_type") String noiseType,
        @JsonProperty("alarm_class") String alarmClass,
        @JsonProperty("cause_category") String causeCategory,
        BigDecimal score,
        List<String> evidence) {
}

record AlgorithmEventChain(
        @JsonProperty("chain_id") String chainId,
        @JsonProperty("member_record_ids") List<UUID> memberRecordIds,
        @JsonProperty("start_time") OffsetDateTime startTime,
        @JsonProperty("end_time") OffsetDateTime endTime,
        @JsonProperty("start_record_id") UUID startRecordId,
        @JsonProperty("association_rule") String associationRule,
        String explanation) {
}

record AlgorithmSummary(
        @JsonProperty("input_count") Integer inputCount,
        @JsonProperty("success_count") Integer successCount,
        @JsonProperty("failure_count") Integer failureCount,
        @JsonProperty("noise_type_counts") Map<String, Integer> noiseTypeCounts,
        @JsonProperty("cause_category_counts") Map<String, Integer> causeCategoryCounts,
        @JsonProperty("event_chain_count") Integer eventChainCount) {
}

record AlgorithmError(@JsonProperty("record_id") UUID recordId, String code, String message) {
}
record ValidatedAnalysis(
        String ruleVersion,
        List<AlgorithmRecordResult> results,
        List<AlgorithmEventChain> chains,
        AnalysisView.Summary summary) {
}

record StartedAnalysis(AnalysisRequest request, int attempt) {
}
final class AnalysisContract {
    private AnalysisContract() {
    }
}
