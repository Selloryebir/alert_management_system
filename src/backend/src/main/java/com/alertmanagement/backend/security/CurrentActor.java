package com.alertmanagement.backend.security;

import com.alertmanagement.backend.api.BusinessApiException;
import org.springframework.http.HttpStatus;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Component;

@Component
public class CurrentActor {

    public Actor require() {
        Authentication authentication = SecurityContextHolder.getContext().getAuthentication();
        if (authentication == null || !authentication.isAuthenticated()) {
            throw new BusinessApiException(HttpStatus.UNAUTHORIZED, "AUTH_REQUIRED", "请先登录");
        }
        if (authentication.getPrincipal() instanceof AuthenticatedUser user) {
            return user.actor();
        }
        boolean admin = authentication.getAuthorities().stream()
                .anyMatch(authority -> "ROLE_SYSTEM_ADMIN".equals(authority.getAuthority()));
        if (admin) {
            return new Actor(null, authentication.getName(), authentication.getName(),
                    "SYSTEM_ADMIN", false, 1);
        }
        throw new BusinessApiException(HttpStatus.UNAUTHORIZED, "AUTH_SESSION_INVALID", "登录会话无效，请重新登录");
    }
}
