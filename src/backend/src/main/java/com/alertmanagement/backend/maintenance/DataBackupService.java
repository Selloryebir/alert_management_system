package com.alertmanagement.backend.maintenance;

import com.alertmanagement.backend.api.BusinessApiException;
import com.alertmanagement.backend.security.Actor;
import com.alertmanagement.backend.security.CurrentActor;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.time.OffsetDateTime;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import org.springframework.core.env.Environment;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

@Service
class DataBackupService {
    private static final List<String> OPERATOR_INSTRUCTIONS = List.of(
            "运行 scripts\\backup-status.ps1 查看容量并执行完整 SHA-256 校验",
            "运行 scripts\\backup.ps1 创建手动恢复点",
            "运行 scripts\\backup-schedule.ps1 -Action Configure 配置每日备份",
            "运行 scripts\\restore-verify.ps1 -BackupPath <恢复点> 执行隔离恢复验证");

    private final JdbcTemplate jdbcTemplate;
    private final CurrentActor currentActor;
    private final Environment environment;
    private final ObjectMapper objectMapper;

    DataBackupService(JdbcTemplate jdbcTemplate, CurrentActor currentActor,
            Environment environment, ObjectMapper objectMapper) {
        this.jdbcTemplate = jdbcTemplate;
        this.currentActor = currentActor;
        this.environment = environment;
        this.objectMapper = objectMapper;
    }

    DataBackupStatusView status() {
        Actor actor = currentActor.require();
        if (!actor.systemAdmin()) {
            throw new BusinessApiException(HttpStatus.FORBIDDEN, "SYSTEM_ADMIN_REQUIRED", "仅系统管理员可查看数据与备份状态");
        }
        Long databaseSize = jdbcTemplate.queryForObject(
                "SELECT pg_database_size(current_database())", Long.class);
        String deploymentMode = environment.getProperty("APP_DEPLOYMENT_MODE", "LOCAL_NATIVE");
        String backupDirectory = environment.getProperty("APP_BACKUP_DIRECTORY");
        List<RecoveryPointView> points = readRecoveryPoints(backupDirectory);
        long totalBytes = points.stream().filter(point -> "METADATA_OK".equals(point.status()))
                .mapToLong(RecoveryPointView::sizeBytes).sum();
        OffsetDateTime latest = points.stream().filter(point -> "METADATA_OK".equals(point.status()))
                .map(RecoveryPointView::createdAt).max(Comparator.naturalOrder()).orElse(null);
        int validCount = (int) points.stream().filter(point -> "METADATA_OK".equals(point.status())).count();
        String management = backupDirectory == null || backupDirectory.isBlank()
                ? "DEPLOYMENT_MANAGED" : "WINDOWS_NATIVE_SCRIPTS";
        return new DataBackupStatusView(
                databaseSize == null ? 0 : databaseSize,
                deploymentMode,
                management,
                validCount,
                latest,
                totalBytes,
                null,
                points,
                OPERATOR_INSTRUCTIONS);
    }

    private List<RecoveryPointView> readRecoveryPoints(String directory) {
        if (directory == null || directory.isBlank()) {
            return List.of();
        }
        Path root = Path.of(directory).toAbsolutePath().normalize();
        if (!Files.isDirectory(root, LinkOption.NOFOLLOW_LINKS) || Files.isSymbolicLink(root)) {
            return List.of(new RecoveryPointView("-", null, 0, null, "UNAVAILABLE", "备份目录不可用或不是普通目录"));
        }
        List<RecoveryPointView> points = new ArrayList<>();
        try (var files = Files.list(root)) {
            files.filter(path -> path.getFileName().toString().endsWith(".dump.meta.json"))
                    .sorted()
                    .forEach(path -> points.add(readRecoveryPoint(root, path)));
        } catch (IOException exception) {
            return List.of(new RecoveryPointView("-", null, 0, null, "UNAVAILABLE", "无法读取备份目录"));
        }
        points.sort(Comparator.comparing(RecoveryPointView::createdAt,
                Comparator.nullsLast(Comparator.reverseOrder())));
        return List.copyOf(points);
    }

    private RecoveryPointView readRecoveryPoint(Path root, Path metadataPath) {
        String metadataName = metadataPath.getFileName().toString();
        try {
            if (!Files.isRegularFile(metadataPath, LinkOption.NOFOLLOW_LINKS)
                    || Files.isSymbolicLink(metadataPath)) {
                throw new IOException("元数据不是普通文件");
            }
            JsonNode metadata = objectMapper.readTree(metadataPath.toFile());
            String backupFile = metadata.path("backup_file").asText();
            String originInstanceId = metadata.path("origin_instance_id").asText();
            String expectedMetadata = backupFile + ".meta.json";
            if (!"alert-management-system-recovery-point".equals(metadata.path("product").asText())
                    || backupFile.isBlank()
                    || !metadataName.equals(expectedMetadata)
                    || !Path.of(backupFile).getFileName().toString().equals(backupFile)
                    || !originInstanceId.matches("[0-9a-f]{32}")
                    || !metadata.path("sha256").asText().matches("[0-9a-f]{64}")) {
                throw new IOException("恢复点元数据身份无效");
            }
            Path dump = root.resolve(backupFile).normalize();
            if (!dump.getParent().equals(root)
                    || !Files.isRegularFile(dump, LinkOption.NOFOLLOW_LINKS)
                    || Files.isSymbolicLink(dump)) {
                throw new IOException("恢复点文件缺失或不是普通文件");
            }
            long declaredSize = metadata.path("size_bytes").asLong(-1);
            long actualSize = Files.size(dump);
            if (declaredSize <= 0 || declaredSize != actualSize) {
                throw new IOException("恢复点大小与元数据不一致");
            }
            OffsetDateTime createdAt = OffsetDateTime.parse(metadata.path("created_at").asText());
            return new RecoveryPointView(
                    backupFile,
                    createdAt,
                    actualSize,
                    originInstanceId,
                    "METADATA_OK",
                    "元数据和大小一致；SHA-256 请运行 backup-status.ps1 校验");
        } catch (IOException | DateTimeParseException | IllegalArgumentException exception) {
            return new RecoveryPointView(metadataName, null, 0, null, "INVALID", exception.getMessage());
        }
    }
}
