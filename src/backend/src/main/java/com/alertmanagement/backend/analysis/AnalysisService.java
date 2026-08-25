package com.alertmanagement.backend.analysis;

import com.alertmanagement.backend.config.AppProperties;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
import org.springframework.stereotype.Service;

@Service
class AnalysisService {

    private final AnalysisPersistenceService persistence;
    private final AlgorithmAnalysisClient client;
    private final AnalysisResponseValidator validator;
    private final AppProperties properties;

    AnalysisService(AnalysisPersistenceService persistence, AlgorithmAnalysisClient client,
            AnalysisResponseValidator validator, AppProperties properties) {
        this.persistence = persistence;
        this.client = client;
        this.validator = validator;
        this.properties = properties;
    }

    AnalysisView analyze(UUID batchId) {
        Map<String, Object> parameters = defaultParameters();
        StartedAnalysis started = persistence.begin(batchId, properties.algorithm().contractVersion(),
                properties.algorithm().version(), parameters);
        UUID runId = started.request().analysisRunId();
        try {
            AlgorithmResponse response = client.analyze(started.request());
            ValidatedAnalysis analysis = validator.validate(started.request(), response);
            try {
                persistence.complete(started, analysis);
            } catch (RuntimeException exception) {
                persistence.fail(runId, batchId, "数据库保存分析结果失败，可重试");
            }
        } catch (AnalysisCallException exception) {
            persistence.fail(runId, batchId, exception.getMessage());
        }
        return persistence.find(runId);
    }

    AnalysisView get(UUID runId) {
        return persistence.find(runId);
    }

    private Map<String, Object> defaultParameters() {
        Map<String, Object> parameters = new LinkedHashMap<>();
        parameters.put("duplicate_window_seconds", 30);
        parameters.put("chatter_window_seconds", 60);
        parameters.put("chatter_min_count", 4);
        parameters.put("short_lived_seconds", 10);
        parameters.put("persistent_requires_ack", true);
        parameters.put("chain_window_seconds", 60);
        parameters.put("chain_min_steps", 5);
        return parameters;
    }
}
