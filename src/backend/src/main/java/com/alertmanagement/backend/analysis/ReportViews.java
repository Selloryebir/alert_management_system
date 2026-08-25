package com.alertmanagement.backend.analysis;

record ReportRequest(String operator) {
}

record ReportFile(byte[] content, String fileName, String contentType) {
}

final class ReportViews {
    private ReportViews() {
    }
}
