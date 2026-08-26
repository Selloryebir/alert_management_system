package com.alertmanagement.backend.maintenance;

import com.fasterxml.jackson.annotation.JsonProperty;
import java.time.OffsetDateTime;
import java.util.List;

record RecoveryPointView(
        @JsonProperty("backup_file") String backupFile,
        @JsonProperty("created_at") OffsetDateTime createdAt,
        @JsonProperty("size_bytes") long sizeBytes,
        @JsonProperty("origin_instance_id") String originInstanceId,
        String status,
        String message) {
}

record DataBackupStatusView(
        @JsonProperty("database_size_bytes") long databaseSizeBytes,
        @JsonProperty("deployment_mode") String deploymentMode,
        @JsonProperty("backup_management") String backupManagement,
        @JsonProperty("recovery_point_count") int recoveryPointCount,
        @JsonProperty("latest_success_at") OffsetDateTime latestSuccessAt,
        @JsonProperty("total_backup_bytes") long totalBackupBytes,
        @JsonProperty("all_hashes_valid") Boolean allHashesValid,
        @JsonProperty("recovery_points") List<RecoveryPointView> recoveryPoints,
        @JsonProperty("operator_instructions") List<String> operatorInstructions) {
}
