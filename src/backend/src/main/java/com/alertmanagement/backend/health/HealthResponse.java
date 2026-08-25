package com.alertmanagement.backend.health;

public record HealthResponse(
        AggregateStatus status,
        String service,
        String version,
        String identity,
        Components components) {

    public enum AggregateStatus {
        UP,
        DEGRADED
    }

    public record Components(
            ComponentHealth system,
            ComponentHealth database,
            ComponentHealth algorithm) {
    }
}
