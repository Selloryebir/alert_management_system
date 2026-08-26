package com.alertmanagement.backend.security;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.mock;

import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.mock.env.MockEnvironment;
import org.springframework.security.crypto.password.PasswordEncoder;

class SecurityBootstrapTest {

    @Test
    void passwordRequestsRedactSecretsInToString() {
        assertThat(new LoginRequest("admin", "login-secret").toString())
                .contains("password=[REDACTED]")
                .doesNotContain("login-secret");
        assertThat(new PasswordChangeRequest("current-secret", "new-secret").toString())
                .contains("currentPassword=[REDACTED]", "newPassword=[REDACTED]")
                .doesNotContain("current-secret", "new-secret");
        assertThat(new UserCreateRequest("analyst", "分析人员", "create-secret", "USER").toString())
                .contains("password=[REDACTED]")
                .doesNotContain("create-secret");
        assertThat(new PasswordResetRequest("reset-secret").toString())
                .contains("newPassword=[REDACTED]")
                .doesNotContain("reset-secret");
    }

    @Test
    void networkModeWithoutTlsRejectsStartup() {
        SecurityBootstrap bootstrap = bootstrap(new MockEnvironment());
        assertThatThrownBy(() -> bootstrap.run(null))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("TLS");
    }

    @Test
    void networkModeWithoutDatabaseSecretRejectsStartup() {
        MockEnvironment environment = new MockEnvironment()
                .withProperty("server.ssl.enabled", "true")
                .withProperty("server.ssl.certificate", "server.crt")
                .withProperty("server.ssl.certificate-private-key", "server.key")
                .withProperty("server.servlet.session.cookie.secure", "true");
        SecurityBootstrap bootstrap = bootstrap(environment);
        assertThatThrownBy(() -> bootstrap.run(null))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("DB_PASSWORD");
    }

    private SecurityBootstrap bootstrap(MockEnvironment environment) {
        return new SecurityBootstrap(mock(JdbcTemplate.class), mock(PasswordEncoder.class),
                mock(PasswordPolicy.class), new SecurityProperties("NETWORK", "admin", "secret.txt"),
                environment);
    }
}
