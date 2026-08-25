package com.alertmanagement.backend.config;

import java.net.http.HttpClient;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class HttpClientConfiguration {

    @Bean
    HttpClient algorithmHttpClient(AppProperties properties) {
        return HttpClient.newBuilder()
                .connectTimeout(properties.algorithm().connectTimeout())
                .build();
    }
}
