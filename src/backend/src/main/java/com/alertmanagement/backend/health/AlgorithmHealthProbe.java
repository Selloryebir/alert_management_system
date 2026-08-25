package com.alertmanagement.backend.health;

@FunctionalInterface
public interface AlgorithmHealthProbe {

    ComponentHealth check();
}
