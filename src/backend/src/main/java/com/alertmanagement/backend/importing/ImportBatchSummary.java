package com.alertmanagement.backend.importing;

import com.fasterxml.jackson.annotation.JsonProperty;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import java.util.UUID;

public record ImportBatchSummary(
        @JsonProperty("batch_id") UUID batchId,
        @JsonProperty("file_name") String fileName,
        ImportFormat format,
        ImportBatchStatus status,
        @JsonProperty("total_rows") int totalRows,
        @JsonProperty("valid_rows") int validRows,
        @JsonProperty("error_count") int errorCount,
        List<String> headers,
        Map<String, String> mapping,
        List<ImportError> errors,
        @JsonProperty("preview_rows") List<AlarmPreview> previewRows,
        @JsonProperty("created_at") OffsetDateTime createdAt,
        @JsonProperty("imported_at") OffsetDateTime importedAt) {
}
