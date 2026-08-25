package com.alertmanagement.backend.health;

@FunctionalInterface
public interface DatabaseHealthProbe {

    ComponentHealth check();
}
