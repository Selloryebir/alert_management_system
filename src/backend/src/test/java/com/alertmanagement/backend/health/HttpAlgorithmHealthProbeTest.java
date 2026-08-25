package com.alertmanagement.backend.health;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import com.alertmanagement.backend.config.AppProperties;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpResponse;
import java.time.Duration;
import org.junit.jupiter.api.Test;

class HttpAlgorithmHealthProbeTest {

    private static final AppProperties PROPERTIES = new AppProperties(
            "alert-management-backend",
            "0.1.0",
            "2026 年灾后重建 Demo",
            new AppProperties.Algorithm(
                    URI.create("http://127.0.0.1:8001/health"),
                    URI.create("http://127.0.0.1:8001/api/v1/analyze"),
                    Duration.ofMillis(500),
                    Duration.ofSeconds(1),
                    Duration.ofSeconds(60),
                    "algorithm-service",
                    "0.1.0",
                    "v1"));

    @Test
    void returnsUpForValidAlgorithmHealthResponse() throws Exception {
        HttpClient httpClient = mock(HttpClient.class);
        @SuppressWarnings("unchecked")
        HttpResponse<String> response = mock(HttpResponse.class);
        when(response.statusCode()).thenReturn(200);
        when(response.body()).thenReturn("""
                {"status":"UP","service":"algorithm-service","version":"0.1.0","contract_version":"v1"}
                """);
        when(httpClient.send(any(), anyResponseHandler())).thenReturn(response);

        ComponentHealth result = probe(httpClient).check();

        assertThat(result).isEqualTo(ComponentHealth.up());
    }

    @Test
    void returnsDownForWrongAlgorithmIdentity() throws Exception {
        ComponentHealth result = checkResponse("""
                {"status":"UP","service":"another-service","version":"0.1.0","contract_version":"v1"}
                """);

        assertThat(result.status()).isEqualTo(ComponentStatus.DOWN);
        assertThat(result.detail()).isEqualTo("算法服务健康契约不匹配");
    }

    @Test
    void returnsDownForWrongContractVersion() throws Exception {
        ComponentHealth result = checkResponse("""
                {"status":"UP","service":"algorithm-service","version":"0.1.0","contract_version":"v2"}
                """);

        assertThat(result.status()).isEqualTo(ComponentStatus.DOWN);
        assertThat(result.detail()).isEqualTo("算法服务健康契约不匹配");
    }

    @Test
    void returnsDownForWrongAlgorithmVersion() throws Exception {
        ComponentHealth result = checkResponse("""
                {"status":"UP","service":"algorithm-service","version":"0.2.0","contract_version":"v1"}
                """);

        assertThat(result.status()).isEqualTo(ComponentStatus.DOWN);
        assertThat(result.detail()).isEqualTo("算法服务健康契约不匹配");
    }

    @Test
    void returnsDownForMissingContractFields() throws Exception {
        ComponentHealth result = checkResponse("{\"status\":\"UP\"}");

        assertThat(result.status()).isEqualTo(ComponentStatus.DOWN);
        assertThat(result.detail()).isEqualTo("算法服务健康契约不匹配");
    }

    @Test
    void returnsDownForNonSuccessHttpResponse() throws Exception {
        ComponentHealth result = checkResponse(503, "unavailable");

        assertThat(result.status()).isEqualTo(ComponentStatus.DOWN);
        assertThat(result.detail()).isEqualTo("算法服务 HTTP 503");
    }

    @Test
    void returnsDownForInvalidJson() throws Exception {
        ComponentHealth result = checkResponse("not-json");

        assertThat(result.status()).isEqualTo(ComponentStatus.DOWN);
        assertThat(result.detail()).isEqualTo("算法服务不可用");
    }

    @Test
    void returnsDownInsteadOfThrowingWhenAlgorithmIsUnavailable() throws Exception {
        HttpClient httpClient = mock(HttpClient.class);
        when(httpClient.send(any(), anyResponseHandler()))
                .thenThrow(new IOException("connection refused"));

        ComponentHealth result = probe(httpClient).check();

        assertThat(result.status()).isEqualTo(ComponentStatus.DOWN);
        assertThat(result.detail()).isEqualTo("算法服务不可用");
    }

    private HttpAlgorithmHealthProbe probe(HttpClient httpClient) {
        return new HttpAlgorithmHealthProbe(httpClient, new ObjectMapper(), PROPERTIES);
    }

    private ComponentHealth checkResponse(String body) throws Exception {
        return checkResponse(200, body);
    }

    private ComponentHealth checkResponse(int statusCode, String body) throws Exception {
        HttpClient httpClient = mock(HttpClient.class);
        @SuppressWarnings("unchecked")
        HttpResponse<String> response = mock(HttpResponse.class);
        when(response.statusCode()).thenReturn(statusCode);
        when(response.body()).thenReturn(body);
        when(httpClient.send(any(), anyResponseHandler())).thenReturn(response);
        return probe(httpClient).check();
    }

    @SuppressWarnings("unchecked")
    private static HttpResponse.BodyHandler<String> anyResponseHandler() {
        return any(HttpResponse.BodyHandler.class);
    }
}
