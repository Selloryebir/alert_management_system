package com.alertmanagement.backend.security;

import com.alertmanagement.backend.api.BusinessApiException;
import com.alertmanagement.backend.audit.AuditService;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;
import java.util.regex.Pattern;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
class AuthService {

    private static final Pattern USERNAME = Pattern.compile("[a-z0-9._-]{3,50}");
    private static final String DUMMY_HASH = "$2a$12$EXRkfkdmXn2gzds2SSitu.9B9yCk/ztEUlOmoBZXl/Oi6BgEWpET6";

    private final JdbcTemplate jdbcTemplate;
    private final PasswordEncoder encoder;
    private final PasswordPolicy passwordPolicy;
    private final AuditService auditService;
    private final CurrentActor currentActor;

    AuthService(JdbcTemplate jdbcTemplate, PasswordEncoder encoder, PasswordPolicy passwordPolicy,
            AuditService auditService, CurrentActor currentActor) {
        this.jdbcTemplate = jdbcTemplate;
        this.encoder = encoder;
        this.passwordPolicy = passwordPolicy;
        this.auditService = auditService;
        this.currentActor = currentActor;
    }

    AuthenticatedUser authenticate(LoginRequest request) {
        String username = normalizeUsername(request == null ? null : request.username());
        String password = request == null ? null : request.password();
        if (password == null) {
            password = "";
        }
        List<AccountRow> accounts = jdbcTemplate.query("""
                SELECT user_id, username, display_name, password_hash, global_role, status,
                       must_change_password, failed_login_attempts, locked_until, credential_version
                  FROM user_account WHERE username=?
                """, (resultSet, rowNumber) -> account(resultSet), username);
        AccountRow account = accounts.isEmpty() ? null : accounts.getFirst();
        boolean passwordMatches = encoder.matches(password, account == null ? DUMMY_HASH : account.passwordHash());
        OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
        boolean unavailable = account == null || !"ACTIVE".equals(account.status())
                || account.lockedUntil() != null && account.lockedUntil().isAfter(now);
        if (!passwordMatches || unavailable) {
            if (account != null && "ACTIVE".equals(account.status())
                    && (account.lockedUntil() == null || !account.lockedUntil().isAfter(now))) {
                jdbcTemplate.update("""
                        UPDATE user_account
                           SET failed_login_attempts=CASE WHEN locked_until <= CURRENT_TIMESTAMP
                                   THEN 1 ELSE failed_login_attempts+1 END,
                               locked_until=CASE WHEN CASE WHEN locked_until <= CURRENT_TIMESTAMP
                                   THEN 1 ELSE failed_login_attempts+1 END >= 5
                                   THEN CURRENT_TIMESTAMP + INTERVAL '15 minutes' ELSE NULL END,
                               updated_at=CURRENT_TIMESTAMP
                         WHERE user_id=?
                        """, account.userId());
            }
            auditService.recordAnonymous("LOGIN_FAILED", username, "USER",
                    account == null ? null : account.userId(), "FAILURE", Map.of("username", username));
            throw new BusinessApiException(HttpStatus.UNAUTHORIZED, "LOGIN_FAILED", "账号或密码错误，或账号暂不可用");
        }
        jdbcTemplate.update("""
                UPDATE user_account SET failed_login_attempts=0, locked_until=NULL, updated_at=CURRENT_TIMESTAMP
                 WHERE user_id=?
                """, account.userId());
        Actor actor = account.actor();
        AuthenticatedUser user = new AuthenticatedUser(actor, account.passwordHash(), true);
        return user;
    }

    void recordLoginSuccess(Actor actor) {
        auditService.record("LOGIN_SUCCEEDED", "USER", actor.userId(), null, "SUCCESS",
                Map.of("username", actor.username()));
    }

    void recordLogout() {
        Actor actor = currentActor.require();
        auditService.record("LOGOUT", "USER", actor.userId(), null, "SUCCESS", Map.of());
    }

    @Transactional
    public void changePassword(PasswordChangeRequest request) {
        Actor actor = currentActor.require();
        if (request == null || request.currentPassword() == null) {
            throw invalid("当前密码不能为空");
        }
        String currentHash = jdbcTemplate.queryForObject(
                "SELECT password_hash FROM user_account WHERE user_id=? FOR UPDATE", String.class, actor.userId());
        if (!encoder.matches(request.currentPassword(), currentHash)) {
            throw new BusinessApiException(HttpStatus.UNAUTHORIZED, "PASSWORD_CURRENT_INVALID", "当前密码错误");
        }
        passwordPolicy.validate(actor.username(), request.newPassword(), currentHash, encoder);
        jdbcTemplate.update("""
                UPDATE user_account SET password_hash=?, must_change_password=FALSE,
                       credential_version=credential_version+1, failed_login_attempts=0, locked_until=NULL,
                       updated_at=CURRENT_TIMESTAMP WHERE user_id=?
                """, encoder.encode(request.newPassword()), actor.userId());
        auditService.record("PASSWORD_CHANGED", "USER", actor.userId(), null, "SUCCESS", Map.of());
    }

    AccountVersion version(UUID userId) {
        List<AccountVersion> rows = jdbcTemplate.query("""
                SELECT status, credential_version, must_change_password FROM user_account WHERE user_id=?
                """, (resultSet, rowNumber) -> new AccountVersion(resultSet.getString("status"),
                        resultSet.getLong("credential_version"), resultSet.getBoolean("must_change_password")), userId);
        return rows.isEmpty() ? null : rows.getFirst();
    }

    static String normalizeUsername(String value) {
        String normalized = value == null ? "" : value.trim().toLowerCase(Locale.ROOT);
        if (!USERNAME.matcher(normalized).matches()) {
            throw new BusinessApiException(HttpStatus.BAD_REQUEST, "USERNAME_INVALID",
                    "账号名必须为 3 到 50 位小写字母、数字、点、下划线或连字符");
        }
        return normalized;
    }

    private AccountRow account(java.sql.ResultSet resultSet) throws java.sql.SQLException {
        return new AccountRow(resultSet.getObject("user_id", UUID.class), resultSet.getString("username"),
                resultSet.getString("display_name"), resultSet.getString("password_hash"),
                resultSet.getString("global_role"), resultSet.getString("status"),
                resultSet.getBoolean("must_change_password"), resultSet.getInt("failed_login_attempts"),
                resultSet.getObject("locked_until", OffsetDateTime.class), resultSet.getLong("credential_version"));
    }

    private BusinessApiException invalid(String message) {
        return new BusinessApiException(HttpStatus.BAD_REQUEST, "PASSWORD_REQUEST_INVALID", message);
    }

    record AccountVersion(String status, long credentialVersion, boolean mustChangePassword) {
    }

    private record AccountRow(UUID userId, String username, String displayName, String passwordHash,
            String globalRole, String status, boolean mustChangePassword, int failedAttempts,
            OffsetDateTime lockedUntil, long credentialVersion) {
        Actor actor() {
            return new Actor(userId, username, displayName, globalRole, mustChangePassword, credentialVersion);
        }
    }
}
