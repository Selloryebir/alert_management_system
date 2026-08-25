package com.alertmanagement.backend.analysis;

import com.alertmanagement.backend.config.AppProperties;
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

    AnalysisView analyze(UUID batchId, AnalysisParameters requestedParameters) {
        Map<String, Object> parameters = (requestedParameters == null
                ? AnalysisParameters.defaults() : requestedParameters).validatedMap();
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

    AnalysisView getLatest(UUID batchId) {
        return persistence.findLatest(batchId);
    }

    Map<String, Object> defaultParameters() {
        return AnalysisParameters.defaults().validatedMap();
    }
}
