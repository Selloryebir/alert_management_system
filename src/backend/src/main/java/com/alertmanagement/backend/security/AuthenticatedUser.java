package com.alertmanagement.backend.security;

import java.io.Serial;
import java.util.Collection;
import java.util.List;
import java.util.UUID;
import org.springframework.security.core.GrantedAuthority;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.userdetails.UserDetails;

public final class AuthenticatedUser implements UserDetails {

    @Serial
    private static final long serialVersionUID = 1L;

    private final Actor actor;
    private final String passwordHash;
    private final boolean enabled;

    AuthenticatedUser(Actor actor, String passwordHash, boolean enabled) {
        this.actor = actor;
        this.passwordHash = passwordHash;
        this.enabled = enabled;
    }

    public Actor actor() {
        return actor;
    }

    public UUID userId() {
        return actor.userId();
    }

    @Override
    public Collection<? extends GrantedAuthority> getAuthorities() {
        return actor.systemAdmin()
                ? List.of(new SimpleGrantedAuthority("ROLE_SYSTEM_ADMIN")) : List.of();
    }

    @Override
    public String getPassword() {
        return passwordHash;
    }

    @Override
    public String getUsername() {
        return actor.username();
    }

    @Override
    public boolean isEnabled() {
        return enabled;
    }
}
