package com.alertmanagement.backend.analysis;

import java.util.UUID;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1")
class AnalysisController {

    private final AnalysisService analysisService;

    AnalysisController(AnalysisService analysisService) {
        this.analysisService = analysisService;
    }

    @PostMapping("/imports/{batchId}/analyses")
    AnalysisView analyze(@PathVariable UUID batchId) {
        return analysisService.analyze(batchId);
    }

    @GetMapping("/analyses/{runId}")
    AnalysisView get(@PathVariable UUID runId) {
        return analysisService.get(runId);
    }

    @GetMapping("/imports/{batchId}/analyses/latest")
    AnalysisView latest(@PathVariable UUID batchId) {
        return analysisService.getLatest(batchId);
    }
}
