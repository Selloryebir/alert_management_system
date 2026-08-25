package com.alertmanagement.backend.health;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import org.junit.jupiter.api.Test;
import org.springframework.dao.DataAccessResourceFailureException;
import org.springframework.jdbc.core.JdbcTemplate;

class JdbcDatabaseHealthProbeTest {

    @Test
    void returnsUpWhenSelectOneSucceeds() {
        JdbcTemplate jdbcTemplate = mock(JdbcTemplate.class);
        when(jdbcTemplate.queryForObject("SELECT 1", Integer.class)).thenReturn(1);

        ComponentHealth result = new JdbcDatabaseHealthProbe(jdbcTemplate).check();

        assertThat(result).isEqualTo(ComponentHealth.up());
    }

    @Test
    void returnsDownInsteadOfThrowingWhenDatabaseIsUnavailable() {
        JdbcTemplate jdbcTemplate = mock(JdbcTemplate.class);
        when(jdbcTemplate.queryForObject("SELECT 1", Integer.class))
                .thenThrow(new DataAccessResourceFailureException("connection refused"));

        ComponentHealth result = new JdbcDatabaseHealthProbe(jdbcTemplate).check();

        assertThat(result.status()).isEqualTo(ComponentStatus.DOWN);
        assertThat(result.detail()).isEqualTo("数据库不可用");
    }
}
