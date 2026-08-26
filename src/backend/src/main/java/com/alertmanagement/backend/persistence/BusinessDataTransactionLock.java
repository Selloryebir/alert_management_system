package com.alertmanagement.backend.persistence;

import org.springframework.jdbc.core.JdbcTemplate;

public final class BusinessDataTransactionLock {

    private static final int LOCK_NAMESPACE = 1095517522;
    private static final int LOCK_ID = 1297040468;

    private BusinessDataTransactionLock() {
    }

    public static void acquire(JdbcTemplate jdbcTemplate) {
        jdbcTemplate.execute("SELECT pg_advisory_xact_lock(" + LOCK_NAMESPACE + ", " + LOCK_ID + ")");
    }
}
