package com.alertmanagement.backend.analysis;

import com.alertmanagement.backend.config.AppProperties;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import org.springframework.stereotype.Component;

@Component
class AlgorithmAnalysisClient {

    private final HttpClient httpClient;
    private final ObjectMapper objectMapper;
    private final AppProperties properties;

    AlgorithmAnalysisClient(HttpClient httpClient, ObjectMapper objectMapper, AppProperties properties) {
        this.httpClient = httpClient;
        this.objectMapper = objectMapper;
        this.properties = properties;
    }

    AlgorithmResponse analyze(AnalysisRequest request) {
        try {
            HttpRequest httpRequest = HttpRequest.newBuilder(properties.algorithm().analysisUrl())
                    .timeout(properties.algorithm().analysisTimeout())
                    .header("Content-Type", "application/json")
                    .POST(HttpRequest.BodyPublishers.ofString(objectMapper.writeValueAsString(request)))
                    .build();
            HttpResponse<String> response = httpClient.send(
                    httpRequest, HttpResponse.BodyHandlers.ofString());
            if (response.statusCode() < 200 || response.statusCode() >= 300) {
                throw new AnalysisCallException("算法服务返回 HTTP " + response.statusCode() + "，可重试");
            }
            try {
                return objectMapper.readValue(response.body(), AlgorithmResponse.class);
            } catch (JsonProcessingException exception) {
                throw new AnalysisCallException("算法响应不是合法 JSON，可重试", exception);
            }
        } catch (java.net.http.HttpTimeoutException exception) {
            throw new AnalysisCallException("算法分析超时，可重试", exception);
        } catch (JsonProcessingException exception) {
            throw new IllegalStateException("分析请求 JSON 序列化失败", exception);
        } catch (IOException exception) {
            throw new AnalysisCallException("算法服务不可用，可重试", exception);
        } catch (InterruptedException exception) {
            Thread.currentThread().interrupt();
            throw new AnalysisCallException("算法分析被中断，可重试", exception);
        }
    }
}

class AnalysisCallException extends RuntimeException {
    AnalysisCallException(String message) {
        super(message);
    }

    AnalysisCallException(String message, Throwable cause) {
        super(message, cause);
    }
}
