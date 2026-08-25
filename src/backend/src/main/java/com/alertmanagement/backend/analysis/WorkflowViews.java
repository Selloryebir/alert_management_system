package com.alertmanagement.backend.analysis;

import com.fasterxml.jackson.annotation.JsonProperty;
import java.math.BigDecimal;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import java.util.UUID;

record DashboardView(
        @JsonProperty("run_id") UUID runId,
        @JsonProperty("batch_id") UUID batchId,
        long total,
        @JsonProperty("disposition_counts") Map<String, Long> dispositionCounts,
        List<TrendPoint> trend,
        @JsonProperty("priority_counts") Map<String, Long> priorityCounts,
        @JsonProperty("area_counts") Map<String, Long> areaCounts,
        @JsonProperty("unit_counts") Map<String, Long> unitCounts,
        @JsonProperty("noise_type_counts") Map<String, Long> noiseTypeCounts,
        @JsonProperty("cause_category_counts") Map<String, Long> causeCategoryCounts) {
}

record TrendPoint(OffsetDateTime bucket, long count) {
}

record AlarmPage(
        @JsonProperty("run_id") UUID runId,
        int page,
        int size,
        long total,
        List<AlarmItem> items) {
}

record AlarmItem(
        @JsonProperty("record_id") UUID recordId,
        @JsonProperty("source_row") int sourceRow,
        @JsonProperty("event_time") OffsetDateTime eventTime,
        String site,
        String area,
        String unit,
        String tag,
        String description,
        String priority,
        @JsonProperty("alarm_state") String alarmState,
        @JsonProperty("noise_type") String noiseType,
        @JsonProperty("alarm_class") String alarmClass,
        @JsonProperty("cause_category") String causeCategory,
        BigDecimal score,
        @JsonProperty("disposition_status") String dispositionStatus) {
}

record AlarmDetail(
        @JsonProperty("record_id") UUID recordId,
        @JsonProperty("source_row") int sourceRow,
        @JsonProperty("event_time") OffsetDateTime eventTime,
        String site,
        String area,
        String unit,
        String tag,
        String description,
        String priority,
        @JsonProperty("alarm_state") String alarmState,
        @JsonProperty("noise_type") String noiseType,
        @JsonProperty("alarm_class") String alarmClass,
        @JsonProperty("cause_category") String causeCategory,
        BigDecimal score,
        @JsonProperty("disposition_status") String dispositionStatus,
        @JsonProperty("return_time") OffsetDateTime returnTime,
        @JsonProperty("ack_time") OffsetDateTime ackTime,
        BigDecimal value,
        BigDecimal threshold,
        @JsonProperty("engineering_unit") String engineeringUnit,
        @JsonProperty("source_system") String sourceSystem,
        String operator,
        @JsonProperty("raw_payload") Map<String, String> rawPayload,
        List<String> evidence,
        @JsonProperty("algorithm_classification") ClassificationValues algorithmClassification,
        @JsonProperty("classification_override") ClassificationOverrideView classificationOverride,
        DispositionView disposition,
        @JsonProperty("disposition_history") List<DispositionHistoryView> dispositionHistory,
        @JsonProperty("event_chains") List<AnalysisView.EventChain> eventChains) {
}

record DispositionView(
        String status,
        String operator,
        String note,
        @JsonProperty("updated_at") OffsetDateTime updatedAt,
        @JsonProperty("closed_at") OffsetDateTime closedAt) {
}

record DispositionHistoryView(
        @JsonProperty("from_status") String fromStatus,
        @JsonProperty("to_status") String toStatus,
        String operator,
        String note,
        @JsonProperty("occurred_at") OffsetDateTime occurredAt) {
}

record DispositionRequest(String status, String operator, String note) {
}

record ClassificationValues(
        @JsonProperty("noise_type") String noiseType,
        @JsonProperty("alarm_class") String alarmClass,
        @JsonProperty("cause_category") String causeCategory) {
}

record ClassificationOverrideView(
        String operator,
        String reason,
        @JsonProperty("updated_at") OffsetDateTime updatedAt) {
}

record ClassificationRequest(
        @JsonProperty("noise_type") String noiseType,
        @JsonProperty("alarm_class") String alarmClass,
        @JsonProperty("cause_category") String causeCategory,
        String operator,
        String reason) {
}
final class WorkflowViews {
    private WorkflowViews() {
    }
}
