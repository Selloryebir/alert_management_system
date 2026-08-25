package com.alertmanagement.backend.health;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.alertmanagement.backend.config.AppProperties;
import java.net.URI;
import java.time.Duration;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

class HealthControllerTest {

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
    void returnsUpWhenAllComponentsAreUp() throws Exception {
        MockMvc mockMvc = mockMvc(ComponentHealth.up(), ComponentHealth.up());

        mockMvc.perform(get("/api/v1/health"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("UP"))
                .andExpect(jsonPath("$.service").value("alert-management-backend"))
                .andExpect(jsonPath("$.version").value("0.1.0"))
                .andExpect(jsonPath("$.identity").value("2026 年灾后重建 Demo"))
                .andExpect(jsonPath("$.components.system.status").value("UP"))
                .andExpect(jsonPath("$.components.database.status").value("UP"))
                .andExpect(jsonPath("$.components.algorithm.status").value("UP"));
    }

    @Test
    void returnsDegradedWithComponentDetailsWhenDependenciesAreDown() throws Exception {
        MockMvc mockMvc = mockMvc(
                ComponentHealth.down("数据库不可用"),
                ComponentHealth.down("算法服务不可用"));

        mockMvc.perform(get("/api/v1/health"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("DEGRADED"))
                .andExpect(jsonPath("$.components.system.status").value("UP"))
                .andExpect(jsonPath("$.components.system.detail").doesNotExist())
                .andExpect(jsonPath("$.components.database.status").value("DOWN"))
                .andExpect(jsonPath("$.components.database.detail").value("数据库不可用"))
                .andExpect(jsonPath("$.components.algorithm.status").value("DOWN"))
                .andExpect(jsonPath("$.components.algorithm.detail").value("算法服务不可用"));
    }

    private MockMvc mockMvc(ComponentHealth database, ComponentHealth algorithm) {
        HealthService healthService = new HealthService(() -> database, () -> algorithm, PROPERTIES);
        return MockMvcBuilders.standaloneSetup(new HealthController(healthService)).build();
    }
}
