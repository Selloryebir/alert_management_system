package com.alertmanagement.backend.config;

import java.net.URI;
import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "app")
public record AppProperties(
        String service,
        String version,
        String identity,
        Algorithm algorithm) {

    public record Algorithm(
            URI healthUrl,
            URI analysisUrl,
            Duration connectTimeout,
            Duration requestTimeout,
            Duration analysisTimeout,
            String service,
            String version,
            String contractVersion) {
    }
}
