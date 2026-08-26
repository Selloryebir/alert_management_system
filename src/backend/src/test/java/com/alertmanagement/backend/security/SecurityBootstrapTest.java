package com.alertmanagement.backend.security;

import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.mock;

import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.mock.env.MockEnvironment;
import org.springframework.security.crypto.password.PasswordEncoder;

class SecurityBootstrapTest {

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
