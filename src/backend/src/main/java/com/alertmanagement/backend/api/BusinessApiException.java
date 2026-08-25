package com.alertmanagement.backend.api;

import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;

public class BusinessApiException extends ResponseStatusException {

    private final String code;

    public BusinessApiException(HttpStatus status, String code, String message) {
        super(status, message);
        this.code = code;
    }

    public String code() {
        return code;
    }
}
