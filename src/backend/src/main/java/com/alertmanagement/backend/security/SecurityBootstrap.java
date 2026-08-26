package com.alertmanagement.backend.security;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Locale;
import java.util.UUID;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.core.env.Environment;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Component;

@Component
class SecurityBootstrap implements ApplicationRunner {

    private final JdbcTemplate jdbcTemplate;
    private final PasswordEncoder encoder;
    private final PasswordPolicy passwordPolicy;
    private final SecurityProperties properties;
    private final Environment environment;

    SecurityBootstrap(JdbcTemplate jdbcTemplate, PasswordEncoder encoder, PasswordPolicy passwordPolicy,
            SecurityProperties properties, Environment environment) {
        this.jdbcTemplate = jdbcTemplate;
        this.encoder = encoder;
        this.passwordPolicy = passwordPolicy;
        this.properties = properties;
        this.environment = environment;
    }

    @Override
    public void run(ApplicationArguments arguments) throws Exception {
        validateDeployment();
        Long accounts = jdbcTemplate.queryForObject("SELECT COUNT(*) FROM user_account", Long.class);
        if (accounts != null && accounts > 0) {
            return;
        }
        String username = AuthService.normalizeUsername(properties.bootstrapAdminUsername());
        String file = properties.bootstrapAdminPasswordFile();
        if (file == null || file.isBlank()) {
            throw new IllegalStateException("数据库尚无账号，必须提供 APP_BOOTSTRAP_ADMIN_PASSWORD_FILE");
        }
        Path passwordFile = Path.of(file).toAbsolutePath().normalize();
        if (!Files.isRegularFile(passwordFile)) {
            throw new IllegalStateException("首个管理员密码文件不存在或不是普通文件");
        }
        String password = Files.readString(passwordFile, StandardCharsets.UTF_8).strip();
        passwordPolicy.validate(username, password, null, encoder);
        UUID userId = UUID.randomUUID();
        jdbcTemplate.update("""
                INSERT INTO user_account (
                    user_id, username, display_name, password_hash, global_role, status, must_change_password)
                VALUES (?, ?, ?, ?, 'SYSTEM_ADMIN', 'ACTIVE', TRUE)
                """, userId, username, username, encoder.encode(password));
        jdbcTemplate.update("""
                INSERT INTO project_membership (project_id, user_id, project_role)
                SELECT project_id, ?, 'MANAGER' FROM business_project
                ON CONFLICT (project_id, user_id) DO NOTHING
                """, userId);
    }

    private void validateDeployment() {
        String mode = properties.deploymentMode() == null
                ? "" : properties.deploymentMode().trim().toUpperCase(Locale.ROOT);
        if (!java.util.Set.of("LOCAL_NATIVE", "LOCAL_CONTAINER", "NETWORK").contains(mode)) {
            throw new IllegalStateException("APP_DEPLOYMENT_MODE 必须是 LOCAL_NATIVE、LOCAL_CONTAINER 或 NETWORK");
        }
        String address = environment.getProperty("server.address", "");
        if ("LOCAL_NATIVE".equals(mode) && !java.util.Set.of("127.0.0.1", "localhost", "::1").contains(address)) {
            throw new IllegalStateException("LOCAL_NATIVE 模式只允许主系统绑定回环地址");
        }
        if ("NETWORK".equals(mode)) {
            if (!environment.getProperty("server.ssl.enabled", Boolean.class, false)
                    || environment.getProperty("server.ssl.certificate") == null
                            && environment.getProperty("server.ssl.key-store") == null
                    || environment.getProperty("server.ssl.certificate-private-key") == null
                            && environment.getProperty("server.ssl.key-store-password") == null
                    || !environment.getProperty("server.servlet.session.cookie.secure", Boolean.class, false)) {
                throw new IllegalStateException("NETWORK 模式必须配置 TLS 证书、私钥并启用 Secure 会话 Cookie");
            }
            String dbPassword = environment.getProperty("DB_PASSWORD");
            if (dbPassword == null || dbPassword.isBlank()) {
                throw new IllegalStateException("NETWORK 模式必须通过 DB_PASSWORD 提供数据库秘密");
            }
            if (properties.bootstrapAdminPasswordFile() == null || properties.bootstrapAdminPasswordFile().isBlank()) {
                Long accounts = jdbcTemplate.queryForObject("SELECT COUNT(*) FROM user_account", Long.class);
                if (accounts == null || accounts == 0) {
                    throw new IllegalStateException("NETWORK 首次启动必须提供首个管理员秘密文件");
                }
            }
        }
    }
}
