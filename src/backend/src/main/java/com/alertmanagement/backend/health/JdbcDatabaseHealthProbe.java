package com.alertmanagement.backend.health;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

@Component
public class JdbcDatabaseHealthProbe implements DatabaseHealthProbe {

    private final JdbcTemplate jdbcTemplate;

    public JdbcDatabaseHealthProbe(JdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    @Override
    public ComponentHealth check() {
        try {
            Integer result = jdbcTemplate.queryForObject("SELECT 1", Integer.class);
            if (Integer.valueOf(1).equals(result)) {
                return ComponentHealth.up();
            }
            return ComponentHealth.down("数据库探测返回异常结果");
        } catch (RuntimeException exception) {
            return ComponentHealth.down("数据库不可用");
        }
    }
}
