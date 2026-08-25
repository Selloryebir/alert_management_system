package com.alertmanagement.backend.analysis;

import java.nio.charset.StandardCharsets;
import java.util.UUID;
import org.springframework.http.ContentDisposition;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/analyses/{runId}/reports")
class ReportController {

    private final ReportService reportService;

    ReportController(ReportService reportService) {
        this.reportService = reportService;
    }

    @PostMapping("/pdf")
    ResponseEntity<byte[]> pdf(@PathVariable UUID runId, @RequestBody(required = false) ReportRequest request) {
        return response(reportService.generate(runId, "PDF", request));
    }

    @PostMapping("/xlsx")
    ResponseEntity<byte[]> xlsx(@PathVariable UUID runId, @RequestBody(required = false) ReportRequest request) {
        return response(reportService.generate(runId, "XLSX", request));
    }

    private ResponseEntity<byte[]> response(ReportFile report) {
        return ResponseEntity.ok()
                .contentType(MediaType.parseMediaType(report.contentType()))
                .header(HttpHeaders.CONTENT_DISPOSITION, ContentDisposition.attachment()
                        .filename(report.fileName(), StandardCharsets.UTF_8).build().toString())
                .contentLength(report.content().length)
                .body(report.content());
    }
}
