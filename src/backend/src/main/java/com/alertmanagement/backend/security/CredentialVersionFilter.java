package com.alertmanagement.backend.security;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.Map;
import org.springframework.http.MediaType;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.web.filter.OncePerRequestFilter;

class CredentialVersionFilter extends OncePerRequestFilter {

    private final AuthService authService;
    private final ObjectMapper objectMapper;

    CredentialVersionFilter(AuthService authService, ObjectMapper objectMapper) {
        this.authService = authService;
        this.objectMapper = objectMapper;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        Authentication authentication = SecurityContextHolder.getContext().getAuthentication();
        if (authentication != null && authentication.getPrincipal() instanceof AuthenticatedUser user) {
            AuthService.AccountVersion version = authService.version(user.userId());
            if (version == null || !"ACTIVE".equals(version.status())
                    || version.credentialVersion() != user.actor().credentialVersion()) {
                if (request.getSession(false) != null) {
                    request.getSession(false).invalidate();
                }
                SecurityContextHolder.clearContext();
                write(response, 401, "AUTH_SESSION_INVALID", "账号状态已改变，请重新登录");
                return;
            }
            if (version.mustChangePassword() && !allowedBeforePasswordChange(request.getRequestURI())) {
                write(response, 403, "PASSWORD_CHANGE_REQUIRED", "首次登录必须先修改密码");
                return;
            }
        }
        chain.doFilter(request, response);
    }

    private boolean allowedBeforePasswordChange(String uri) {
        return uri.equals("/api/v1/auth/csrf") || uri.equals("/api/v1/auth/me")
                || uri.equals("/api/v1/auth/password") || uri.equals("/api/v1/auth/logout")
                || uri.equals("/api/v1/health");
    }

    private void write(HttpServletResponse response, int status, String code, String message) throws IOException {
        response.setStatus(status);
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        response.setCharacterEncoding("UTF-8");
        objectMapper.writeValue(response.getWriter(), Map.of("code", code, "message", message,
                "trace_id", java.util.UUID.randomUUID().toString()));
    }
}
