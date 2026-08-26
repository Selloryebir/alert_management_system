package com.alertmanagement.backend.security;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "app")
public record SecurityProperties(
        String deploymentMode,
        String bootstrapAdminUsername,
        String bootstrapAdminPasswordFile) {

    public boolean networkMode() {
        return "NETWORK".equals(deploymentMode);
    }
}
