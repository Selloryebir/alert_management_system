package com.alertmanagement.backend.api;

import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.server.ResponseStatusException;

@RestControllerAdvice
public class ApiExceptionHandler {

    @ExceptionHandler(ResponseStatusException.class)
    ResponseEntity<ApiError> handleResponseStatus(ResponseStatusException exception) {
        HttpStatus status = HttpStatus.resolve(exception.getStatusCode().value());
        String code = switch (status == null ? HttpStatus.INTERNAL_SERVER_ERROR : status) {
            case BAD_REQUEST -> "IMPORT_REQUEST_INVALID";
            case NOT_FOUND -> "IMPORT_BATCH_NOT_FOUND";
            case CONFLICT -> "IMPORT_STATUS_CONFLICT";
            default -> "REQUEST_FAILED";
        };
        String message = exception.getReason() == null ? "请求处理失败" : exception.getReason();
        return ResponseEntity.status(exception.getStatusCode())
                .body(new ApiError(code, message, UUID.randomUUID().toString()));
    }
}
