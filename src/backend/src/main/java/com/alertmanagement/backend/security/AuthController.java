package com.alertmanagement.backend.security;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.context.SecurityContext;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.web.context.SecurityContextRepository;
import org.springframework.security.web.csrf.CsrfToken;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/auth")
class AuthController {

    private final AuthService authService;
    private final CurrentActor currentActor;
    private final SecurityContextRepository contextRepository;

    AuthController(AuthService authService, CurrentActor currentActor, SecurityContextRepository contextRepository) {
        this.authService = authService;
        this.currentActor = currentActor;
        this.contextRepository = contextRepository;
    }

    @GetMapping("/csrf")
    CsrfView csrf(CsrfToken token) {
        return new CsrfView(token.getToken(), token.getHeaderName(), token.getParameterName());
    }

    @PostMapping("/login")
    CurrentUserView login(@RequestBody(required = false) LoginRequest request,
            HttpServletRequest servletRequest, HttpServletResponse servletResponse) {
        AuthenticatedUser user = authService.authenticate(request);
        servletRequest.getSession(true);
        servletRequest.changeSessionId();
        SecurityContext context = SecurityContextHolder.createEmptyContext();
        context.setAuthentication(UsernamePasswordAuthenticationToken.authenticated(
                user, null, user.getAuthorities()));
        SecurityContextHolder.setContext(context);
        contextRepository.saveContext(context, servletRequest, servletResponse);
        authService.recordLoginSuccess(user.actor());
        return CurrentUserView.from(user.actor());
    }

    @GetMapping("/me")
    CurrentUserView me() {
        return CurrentUserView.from(currentActor.require());
    }

    @PostMapping("/password")
    CurrentUserView changePassword(@RequestBody(required = false) PasswordChangeRequest request,
            HttpServletRequest servletRequest) {
        Actor actor = currentActor.require();
        authService.changePassword(request);
        if (servletRequest.getSession(false) != null) {
            servletRequest.getSession(false).invalidate();
        }
        SecurityContextHolder.clearContext();
        return new CurrentUserView(actor.userId(), actor.username(), actor.displayName(), actor.globalRole(), false);
    }

    @PostMapping("/logout")
    void logout(HttpServletRequest request) {
        authService.recordLogout();
        if (request.getSession(false) != null) {
            request.getSession(false).invalidate();
        }
        SecurityContextHolder.clearContext();
    }
}
