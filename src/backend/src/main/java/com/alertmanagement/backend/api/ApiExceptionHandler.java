package com.alertmanagement.backend.api;

import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.web.multipart.MaxUploadSizeExceededException;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.MissingServletRequestParameterException;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;

@RestControllerAdvice
public class ApiExceptionHandler {

    @ExceptionHandler(BusinessApiException.class)
    ResponseEntity<ApiError> handleBusiness(BusinessApiException exception) {
        return ResponseEntity.status(exception.getStatusCode())
                .body(new ApiError(exception.code(), exception.getReason(), UUID.randomUUID().toString()));
    }

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

    @ExceptionHandler(MaxUploadSizeExceededException.class)
    ResponseEntity<ApiError> handleUploadLimit(MaxUploadSizeExceededException exception) {
        return error(HttpStatus.PAYLOAD_TOO_LARGE, "IMPORT_REQUEST_TOO_LARGE", "上传请求不能超过 52 MiB");
    }

    @ExceptionHandler({HttpMessageNotReadableException.class, MissingServletRequestParameterException.class,
            MethodArgumentTypeMismatchException.class})
    ResponseEntity<ApiError> handleMalformedRequest(Exception exception) {
        return error(HttpStatus.BAD_REQUEST, "REQUEST_INVALID", "请求结构或参数格式无效");
    }

    private ResponseEntity<ApiError> error(HttpStatus status, String code, String message) {
        return ResponseEntity.status(status).body(new ApiError(code, message, UUID.randomUUID().toString()));
    }
}
