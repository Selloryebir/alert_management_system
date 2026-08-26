package com.alertmanagement.backend.importing;

import com.alertmanagement.backend.api.BusinessApiException;
import jakarta.annotation.PreDestroy;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.Future;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;
import org.springframework.web.multipart.MultipartFile;

@Component
class ImportParsingExecutor {
    private final ThreadPoolExecutor executor = new ThreadPoolExecutor(
            1, 1, 0, TimeUnit.MILLISECONDS, new ArrayBlockingQueue<>(1), runnable -> {
                Thread thread = new Thread(runnable, "bounded-import-parser");
                thread.setDaemon(true);
                return thread;
            }, new ThreadPoolExecutor.AbortPolicy());

    SourceTable parse(ImportFileParser parser, MultipartFile file) {
        final Future<SourceTable> future;
        try {
            future = executor.submit(() -> parser.parse(file));
        } catch (java.util.concurrent.RejectedExecutionException exception) {
            throw new BusinessApiException(HttpStatus.TOO_MANY_REQUESTS,
                    "IMPORT_PARSER_BUSY", "已有文件正在解析，请稍后重试");
        }
        try {
            return future.get(30, TimeUnit.SECONDS);
        } catch (TimeoutException exception) {
            future.cancel(true);
            throw new BusinessApiException(HttpStatus.REQUEST_TIMEOUT,
                    "IMPORT_PARSE_TIMEOUT", "文件解析超过 30 秒，已终止本次导入");
        } catch (InterruptedException exception) {
            future.cancel(true);
            Thread.currentThread().interrupt();
            throw new BusinessApiException(HttpStatus.BAD_REQUEST,
                    "IMPORT_PARSE_INTERRUPTED", "文件解析已中断，请重试");
        } catch (ExecutionException exception) {
            Throwable cause = exception.getCause();
            if (cause instanceof RuntimeException runtimeException) {
                throw runtimeException;
            }
            throw new BusinessApiException(HttpStatus.BAD_REQUEST,
                    "IMPORT_PARSE_FAILED", "文件解析失败");
        }
    }

    @PreDestroy
    void close() {
        executor.shutdownNow();
    }
}
