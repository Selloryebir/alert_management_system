package com.alertmanagement.backend.health;

import com.alertmanagement.backend.config.AppProperties;
import com.alertmanagement.backend.health.HealthResponse.AggregateStatus;
import com.alertmanagement.backend.health.HealthResponse.Components;
import org.springframework.stereotype.Service;

@Service
public class HealthService {

    private final DatabaseHealthProbe databaseHealthProbe;
    private final AlgorithmHealthProbe algorithmHealthProbe;
    private final AppProperties properties;

    public HealthService(
            DatabaseHealthProbe databaseHealthProbe,
            AlgorithmHealthProbe algorithmHealthProbe,
            AppProperties properties) {
        this.databaseHealthProbe = databaseHealthProbe;
        this.algorithmHealthProbe = algorithmHealthProbe;
        this.properties = properties;
    }

    public HealthResponse getHealth() {
        ComponentHealth system = ComponentHealth.up();
        ComponentHealth database = databaseHealthProbe.check();
        ComponentHealth algorithm = algorithmHealthProbe.check();
        AggregateStatus aggregate = database.status() == ComponentStatus.UP
                        && algorithm.status() == ComponentStatus.UP
                ? AggregateStatus.UP
                : AggregateStatus.DEGRADED;

        return new HealthResponse(
                aggregate,
                properties.service(),
                properties.version(),
                properties.identity(),
                new Components(system, database, algorithm));
    }
}
