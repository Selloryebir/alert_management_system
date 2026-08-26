package com.alertmanagement.backend.security;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.ServletInputStream;
import jakarta.servlet.ReadListener;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletRequestWrapper;
import jakarta.servlet.http.HttpServletResponse;
import java.io.BufferedReader;
import java.io.FilterInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

@Component
@Order(Ordered.HIGHEST_PRECEDENCE)
class RequestBoundaryFilter extends OncePerRequestFilter {

    private static final int MAX_QUERY_LENGTH = 2 * 1024;
    private static final int MAX_JSON_BYTES = 1024 * 1024;
    private final ObjectMapper objectMapper;

    RequestBoundaryFilter(ObjectMapper objectMapper) {
        this.objectMapper = objectMapper;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        String query = request.getQueryString();
        if (query != null && query.getBytes(StandardCharsets.UTF_8).length > MAX_QUERY_LENGTH) {
            reject(response, 414, "QUERY_TOO_LARGE", "查询字符串不能超过 2 KiB");
            return;
        }
        String contentType = request.getContentType();
        if (contentType != null && contentType.toLowerCase(java.util.Locale.ROOT)
                .startsWith(MediaType.APPLICATION_JSON_VALUE)) {
            if (request.getContentLengthLong() > MAX_JSON_BYTES) {
                reject(response, 413, "REQUEST_BODY_TOO_LARGE", "JSON 请求体不能超过 1 MiB");
                return;
            }
            try {
                chain.doFilter(new LimitedRequest(request), response);
            } catch (PayloadLimitException exception) {
                if (!response.isCommitted()) {
                    response.reset();
                    reject(response, 413, "REQUEST_BODY_TOO_LARGE", "JSON 请求体不能超过 1 MiB");
                }
            }
            return;
        }
        chain.doFilter(request, response);
    }

    private void reject(HttpServletResponse response, int status, String code, String message) throws IOException {
        response.setStatus(status);
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        response.setCharacterEncoding("UTF-8");
        objectMapper.writeValue(response.getWriter(), Map.of("code", code, "message", message,
                "trace_id", java.util.UUID.randomUUID().toString()));
    }

    private static final class LimitedRequest extends HttpServletRequestWrapper {
        LimitedRequest(HttpServletRequest request) {
            super(request);
        }

        @Override
        public ServletInputStream getInputStream() throws IOException {
            ServletInputStream delegate = super.getInputStream();
            InputStream limited = new FilterInputStream(delegate) {
                private int count;
                @Override public int read() throws IOException {
                    int value = super.read();
                    if (value >= 0 && ++count > MAX_JSON_BYTES) throw new PayloadLimitException();
                    return value;
                }
                @Override public int read(byte[] bytes, int offset, int length) throws IOException {
                    int read = super.read(bytes, offset, length);
                    if (read > 0 && (count += read) > MAX_JSON_BYTES) throw new PayloadLimitException();
                    return read;
                }
            };
            return new ServletInputStream() {
                @Override public boolean isFinished() { return delegate.isFinished(); }
                @Override public boolean isReady() { return delegate.isReady(); }
                @Override public void setReadListener(ReadListener listener) { delegate.setReadListener(listener); }
                @Override public int read() throws IOException { return limited.read(); }
                @Override public int read(byte[] bytes, int offset, int length) throws IOException {
                    return limited.read(bytes, offset, length);
                }
            };
        }

        @Override
        public BufferedReader getReader() throws IOException {
            return new BufferedReader(new InputStreamReader(getInputStream(), StandardCharsets.UTF_8));
        }
    }

    private static final class PayloadLimitException extends IOException {
    }
}
