package com.alertmanagement.backend.security;

import com.alertmanagement.backend.api.BusinessApiException;
import java.nio.charset.StandardCharsets;
import org.springframework.http.HttpStatus;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Component;

@Component
class PasswordPolicy {

    void validate(String username, String newPassword, String currentHash, PasswordEncoder encoder) {
        if (newPassword == null || newPassword.length() < 12 || newPassword.length() > 64
                || newPassword.getBytes(StandardCharsets.UTF_8).length > 72) {
            throw invalid("密码长度必须为 12 到 64 个字符，且 UTF-8 编码不能超过 72 字节");
        }
        if (newPassword.equalsIgnoreCase(username)) {
            throw invalid("密码不能与账号名相同");
        }
        if (currentHash != null && encoder.matches(newPassword, currentHash)) {
            throw invalid("新密码不能与当前密码相同");
        }
    }

    private BusinessApiException invalid(String message) {
        return new BusinessApiException(HttpStatus.BAD_REQUEST, "PASSWORD_INVALID", message);
    }
}
