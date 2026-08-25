package com.alertmanagement.backend.analysis;

import java.util.UUID;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/analyses/{runId}")
class AnalysisWorkflowController {

    private final AnalysisWorkflowService workflowService;

    AnalysisWorkflowController(AnalysisWorkflowService workflowService) {
        this.workflowService = workflowService;
    }

    @GetMapping("/dashboard")
    DashboardView dashboard(@PathVariable UUID runId) {
        return workflowService.dashboard(runId);
    }

    @GetMapping("/alarms")
    AlarmPage alarms(
            @PathVariable UUID runId,
            @RequestParam(defaultValue = "0") int page,
            @RequestParam(defaultValue = "50") int size,
            @RequestParam(required = false) String priority,
            @RequestParam(required = false) String area,
            @RequestParam(required = false) String unit,
            @RequestParam(name = "noise_type", required = false) String noiseType,
            @RequestParam(name = "cause_category", required = false) String causeCategory,
            @RequestParam(name = "disposition_status", required = false) String dispositionStatus) {
        return workflowService.alarms(
                runId, page, size, priority, area, unit, noiseType, causeCategory, dispositionStatus);
    }

    @GetMapping("/alarms/{recordId}")
    AlarmDetail alarm(@PathVariable UUID runId, @PathVariable UUID recordId) {
        return workflowService.alarm(runId, recordId);
    }

    @PatchMapping("/alarms/{recordId}/disposition")
    DispositionView updateDisposition(
            @PathVariable UUID runId,
            @PathVariable UUID recordId,
            @RequestBody(required = false) DispositionRequest request) {
        return workflowService.updateDisposition(runId, recordId, request);
    }
}
