package com.alertmanagement.backend.importing;

import java.util.List;
import java.util.Map;

record ValidatedImport(
        ImportFormat format,
        List<String> headers,
        Map<String, String> mapping,
        int totalRows,
        int validRows,
        List<ImportError> errors,
        List<NormalizedAlarm> records) {

    ImportBatchStatus status() {
        return errors.isEmpty() ? ImportBatchStatus.READY : ImportBatchStatus.REJECTED;
    }
}
