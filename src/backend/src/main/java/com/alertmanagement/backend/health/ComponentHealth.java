package com.alertmanagement.backend.health;

public record ComponentHealth(ComponentStatus status, String detail) {

    public static ComponentHealth up() {
        return new ComponentHealth(ComponentStatus.UP, null);
    }

    public static ComponentHealth down(String detail) {
        return new ComponentHealth(ComponentStatus.DOWN, detail);
    }
}
