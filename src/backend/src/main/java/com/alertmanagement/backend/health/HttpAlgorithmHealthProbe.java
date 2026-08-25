package com.alertmanagement.backend.health;

import com.alertmanagement.backend.config.AppProperties;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import org.springframework.stereotype.Component;

@Component
public class HttpAlgorithmHealthProbe implements AlgorithmHealthProbe {

    private final HttpClient httpClient;
    private final ObjectMapper objectMapper;
    private final AppProperties properties;

    public HttpAlgorithmHealthProbe(
            HttpClient httpClient,
            ObjectMapper objectMapper,
            AppProperties properties) {
        this.httpClient = httpClient;
        this.objectMapper = objectMapper;
        this.properties = properties;
    }

    @Override
    public ComponentHealth check() {
        HttpRequest request = HttpRequest.newBuilder(properties.algorithm().healthUrl())
                .timeout(properties.algorithm().requestTimeout())
                .GET()
                .build();
        try {
            HttpResponse<String> response = httpClient.send(
                    request, HttpResponse.BodyHandlers.ofString());
            if (response.statusCode() < 200 || response.statusCode() >= 300) {
                return ComponentHealth.down("算法服务 HTTP " + response.statusCode());
            }
            JsonNode body = objectMapper.readTree(response.body());
            AppProperties.Algorithm expected = properties.algorithm();
            if ("UP".equalsIgnoreCase(body.path("status").asText())
                    && expected.service().equals(body.path("service").asText())
                    && expected.version().equals(body.path("version").asText())
                    && expected.contractVersion().equals(body.path("contract_version").asText())) {
                return ComponentHealth.up();
            }
            return ComponentHealth.down("算法服务健康契约不匹配");
        } catch (InterruptedException exception) {
            Thread.currentThread().interrupt();
            return ComponentHealth.down("算法服务健康检查被中断");
        } catch (IOException | RuntimeException exception) {
            return ComponentHealth.down("算法服务不可用");
        }
    }
}
