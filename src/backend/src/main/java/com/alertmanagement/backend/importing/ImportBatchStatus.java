package com.alertmanagement.backend.importing;

public enum ImportBatchStatus {
    READY,
    REJECTED,
    IMPORTED,
    ANALYZING,
    COMPLETED,
    FAILED
}
