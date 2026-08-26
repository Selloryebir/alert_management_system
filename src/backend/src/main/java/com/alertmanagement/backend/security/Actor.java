package com.alertmanagement.backend.security;

import java.util.UUID;

public record Actor(
        UUID userId,
        String username,
        String displayName,
        String globalRole,
        boolean mustChangePassword,
        long credentialVersion) {

    public boolean systemAdmin() {
        return "SYSTEM_ADMIN".equals(globalRole);
    }
}
