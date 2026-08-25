package com.alertmanagement.backend.importing;

import java.util.List;

public record ImportRecordPage(
        List<AlarmPreview> items,
        long total,
        int page,
        int size) {
}
