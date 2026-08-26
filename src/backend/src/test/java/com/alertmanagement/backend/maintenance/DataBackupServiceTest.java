package com.alertmanagement.backend.maintenance;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import com.alertmanagement.backend.api.BusinessApiException;
import com.alertmanagement.backend.security.Actor;
import com.alertmanagement.backend.security.CurrentActor;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.mock.env.MockEnvironment;
import org.springframework.jdbc.core.JdbcTemplate;

class DataBackupServiceTest {
    @TempDir Path temporary;

    @Test
    void reportsDatabaseAndRecoveryPointWithoutClaimingHashVerification() throws Exception {
        Path dump = temporary.resolve("point.dump");
        Files.writeString(dump, "backup-content");
        Files.writeString(temporary.resolve("point.dump.meta.json"), """
                {
                  "product": "alert-management-system-recovery-point",
                  "backup_file": "point.dump",
                  "created_at": "2026-08-26T08:00:00Z",
                  "origin_instance_id": "0123456789abcdef0123456789abcdef",
                  "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                  "size_bytes": %d
                }
                """.formatted(Files.size(dump)));
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        when(jdbc.queryForObject("SELECT pg_database_size(current_database())", Long.class))
                .thenReturn(4096L);
        CurrentActor actor = mock(CurrentActor.class);
        when(actor.require()).thenReturn(new Actor(UUID.randomUUID(), "admin", "管理员",
                "SYSTEM_ADMIN", false, 1));
        MockEnvironment environment = new MockEnvironment()
                .withProperty("APP_DEPLOYMENT_MODE", "LOCAL_NATIVE")
                .withProperty("APP_BACKUP_DIRECTORY", temporary.toString());

        DataBackupStatusView status = new DataBackupService(
                jdbc, actor, environment, new ObjectMapper()).status();

        assertThat(status.databaseSizeBytes()).isEqualTo(4096);
        assertThat(status.recoveryPointCount()).isEqualTo(1);
        assertThat(status.totalBackupBytes()).isEqualTo(Files.size(dump));
        assertThat(status.allHashesValid()).isNull();
        assertThat(status.recoveryPoints()).singleElement()
                .extracting(RecoveryPointView::status).isEqualTo("METADATA_OK");
        assertThat(status.operatorInstructions()).hasSize(4);
    }

    @Test
    void rejectsNonAdministrator() {
        CurrentActor actor = mock(CurrentActor.class);
        when(actor.require()).thenReturn(new Actor(UUID.randomUUID(), "analyst", "分析员",
                "NONE", false, 1));
        DataBackupService service = new DataBackupService(
                mock(JdbcTemplate.class), actor, new MockEnvironment(), new ObjectMapper());

        assertThatThrownBy(service::status)
                .isInstanceOf(BusinessApiException.class)
                .hasMessageContaining("仅系统管理员");
    }
}
