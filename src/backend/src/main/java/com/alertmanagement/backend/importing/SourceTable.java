package com.alertmanagement.backend.importing;

import java.util.List;
import java.util.Map;

record SourceTable(
        ImportFormat format,
        List<String> headers,
        List<SourceRow> rows,
        List<ImportError> errors) {

    record SourceRow(int sourceRow, Map<String, String> values) {
    }
}
