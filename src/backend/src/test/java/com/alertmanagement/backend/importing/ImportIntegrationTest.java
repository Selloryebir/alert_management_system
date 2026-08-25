package com.alertmanagement.backend.importing;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.multipart;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.ObjectMapper;
import io.zonky.test.db.postgres.embedded.EmbeddedPostgres;
import java.io.IOException;
import java.nio.charset.Charset;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.stream.Collectors;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestInstance;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.web.server.ResponseStatusException;

@SpringBootTest
@AutoConfigureMockMvc
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class ImportIntegrationTest {

    private static final String[] HEADERS = {
        "event_time", "return_time", "ack_time", "site", "area", "unit", "tag", "description",
        "priority", "state", "value", "threshold", "engineering_unit", "source_system", "operator"
    };
    private static final String[][] ROWS = {
        {"2026-08-25 08:00:00", "2026-08-25 08:05:00", "2026-08-25 08:01:00", "合成厂区", "一号区",
            "反应单元", "TAG-001", "压力高", "P1", "RETURNED", "12.5", "10", "MPa", "SYNTHETIC_DCS", "演示员"},
        {"2026-08-25T09:00:00+08:00", "", "", "合成厂区", "二号区", "", "Tag-002", "温度高",
            "p2", "active", "88.2", "80", "℃", "SYNTHETIC_DCS", ""}
    };
    private static final EmbeddedPostgres POSTGRES = startPostgres();

    @Autowired
    private ImportService importService;

    @Autowired
    private JdbcTemplate jdbcTemplate;

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private ObjectMapper objectMapper;

    @DynamicPropertySource
    static void postgresProperties(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", () -> POSTGRES.getJdbcUrl("postgres", "postgres"));
        registry.add("spring.datasource.username", () -> "postgres");
        registry.add("spring.datasource.password", () -> "");
    }

    @BeforeEach
    void cleanDatabase() {
        jdbcTemplate.execute("DROP TRIGGER IF EXISTS reject_import_status ON import_batch");
        jdbcTemplate.execute("DROP FUNCTION IF EXISTS reject_import_status()");
        jdbcTemplate.execute("TRUNCATE alarm_record, import_staging, import_batch CASCADE");
    }

    @AfterAll
    void stopPostgres() throws IOException {
        POSTGRES.close();
    }

    @Test
    void csvTxtAndFirstVisibleXlsxProduceEquivalentRecords() throws Exception {
        ImportBatchSummary csv = importService.preview(sample("synthetic_smoke_utf8.csv"), null);
        ImportBatchSummary txt = importService.preview(sample("synthetic_smoke_utf8.txt"), null);
        ImportBatchSummary xlsx = importService.preview(sample("synthetic_smoke.xlsx"), null);

        assertThat(List.of(csv.status(), txt.status(), xlsx.status()))
                .containsOnly(ImportBatchStatus.READY);
        assertThat(List.of(csv.totalRows(), txt.totalRows(), xlsx.totalRows())).containsOnly(300);
        assertThat(List.of(csv.validRows(), txt.validRows(), xlsx.validRows())).containsOnly(300);
        assertThat(normalizedPreview(csv)).isEqualTo(normalizedPreview(txt)).isEqualTo(normalizedPreview(xlsx));

        importService.confirm(csv.batchId());
        importService.confirm(txt.batchId());
        importService.confirm(xlsx.batchId());
        assertThat(List.of(countAlarms(csv.batchId()), countAlarms(txt.batchId()), countAlarms(xlsx.batchId())))
                .containsOnly(300);
    }

    @Test
    void gb18030CsvAndExplicitMappingAreAccepted() throws Exception {
        String headers = "时间,厂区列,区域列,位号列,描述列,级别列,状态列,来源列\r\n";
        String row = "2026-08-25 10:00:00,合成厂区,三号区,TAG-003,流量低,P3,ACTIVE,SYNTHETIC_DCS\r\n";
        String mapping = objectMapper.writeValueAsString(Map.of(
                "event_time", "时间", "site", "厂区列", "area", "区域列", "tag", "位号列",
                "description", "描述列", "priority", "级别列", "state", "状态列", "source_system", "来源列"));

        ImportBatchSummary summary = importService.preview(
                file("gb18030.csv", (headers + row).getBytes(Charset.forName("GB18030"))), mapping);

        assertThat(summary.status()).isEqualTo(ImportBatchStatus.READY);
        assertThat(summary.previewRows()).singleElement().satisfies(preview -> {
            assertThat(preview.site()).isEqualTo("合成厂区");
            assertThat(preview.rawPayload()).containsEntry("描述列", "流量低");
        });
        importService.confirm(summary.batchId());
        assertThat(countAlarms(summary.batchId())).isOne();
    }

    @Test
    void invalidBatchReportsAllRequiredFailureKindsAndCreatesNoBusinessRows() {
        String invalid = String.join(",", HEADERS) + "\n"
                + "错误时间,,, ,区域,单元,TAG-X,描述,P9,UNKNOWN,非数字,1,MPa,SYNTHETIC_DCS,\n"
                + "2026-08-25 10:00:00,2026-08-25 09:59:59,,厂区,区域,单元,TAG-Y,描述,P1,RETURNED,1,1,MPa,SYNTHETIC_DCS,\n"
                + "2026-08-25 10:00:00,,,厂区,区域,单元,TAG-Z,,P2,ACTIVE,1,1,MPa,SYNTHETIC_DCS,\n";

        ImportBatchSummary summary = importService.preview(
                file("invalid.csv", invalid.getBytes(StandardCharsets.UTF_8)), null);
        Set<String> codes = summary.errors().stream().map(ImportError::code).collect(Collectors.toSet());

        assertThat(summary.status()).isEqualTo(ImportBatchStatus.REJECTED);
        assertThat(codes).contains("INVALID_TIME", "INVALID_ENUM", "INVALID_NUMBER",
                "TIME_ORDER_INVALID", "REQUIRED_VALUE_MISSING");
        assertThat(summary.errors()).allSatisfy(error -> {
            assertThat(error.sourceRow()).isPositive();
            assertThat(error.field()).isNotBlank();
            assertThat(error.message()).isNotBlank();
        });
        assertThat(countStaging(summary.batchId())).isZero();
        assertThat(countAlarms(summary.batchId())).isZero();
        assertThatThrownBy(() -> importService.confirm(summary.batchId()))
                .isInstanceOf(ResponseStatusException.class)
                .hasMessageContaining("409 CONFLICT");
        assertThat(countAlarms(summary.batchId())).isZero();
    }

    @Test
    void apiSupportsPreviewSummaryAndRejectsRepeatedConfirmation() throws Exception {
        MockMultipartFile file = file("api.csv", delimited(',').getBytes(StandardCharsets.UTF_8));
        String response = mockMvc.perform(multipart("/api/v1/imports/preview").file(file))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("READY"))
                .andExpect(jsonPath("$.total_rows").value(2))
                .andReturn().getResponse().getContentAsString();
        UUID batchId = UUID.fromString(objectMapper.readTree(response).get("batch_id").asText());

        mockMvc.perform(get("/api/v1/imports/{batchId}", batchId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.preview_rows.length()").value(2));
        mockMvc.perform(post("/api/v1/imports/{batchId}/confirm", batchId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("IMPORTED"));
        mockMvc.perform(post("/api/v1/imports/{batchId}/confirm", batchId))
                .andExpect(status().isConflict());
        assertThat(countAlarms(batchId)).isEqualTo(2);
    }

    @Test
    void confirmationFailureRollsBackAllAlarmRowsAndKeepsReadyState() {
        ImportBatchSummary summary = importService.preview(
                file("rollback.csv", delimited(',').getBytes(StandardCharsets.UTF_8)), null);
        jdbcTemplate.execute("""
                CREATE FUNCTION reject_import_status() RETURNS trigger AS $$
                BEGIN
                    IF NEW.status = 'IMPORTED' THEN
                        RAISE EXCEPTION 'forced confirmation failure';
                    END IF;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql
                """);
        jdbcTemplate.execute("""
                CREATE TRIGGER reject_import_status
                BEFORE UPDATE ON import_batch
                FOR EACH ROW EXECUTE FUNCTION reject_import_status()
                """);

        assertThatThrownBy(() -> importService.confirm(summary.batchId()))
                .hasMessageContaining("forced confirmation failure");
        assertThat(countAlarms(summary.batchId())).isZero();
        assertThat(jdbcTemplate.queryForObject(
                "SELECT status FROM import_batch WHERE batch_id = ?", String.class, summary.batchId()))
                .isEqualTo("READY");
    }

    private MockMultipartFile file(String name, byte[] content) {
        return new MockMultipartFile("file", name, "application/octet-stream", content);
    }

    private String delimited(char delimiter) {
        String separator = String.valueOf(delimiter);
        StringBuilder content = new StringBuilder(String.join(separator, HEADERS)).append("\r\n");
        for (String[] row : ROWS) {
            content.append(String.join(separator, row)).append("\r\n");
        }
        return content.toString();
    }

    private MockMultipartFile sample(String name) throws IOException {
        Path rootCandidate = Path.of("samples", "smoke", name);
        Path path = Files.exists(rootCandidate)
                ? rootCandidate
                : Path.of("..", "..", "samples", "smoke", name);
        return file(name, Files.readAllBytes(path));
    }

    private List<List<Object>> normalizedPreview(ImportBatchSummary summary) {
        return summary.previewRows().stream().map(row -> List.of(
                row.sourceRow(), row.eventTime(), nullable(row.returnTime()), nullable(row.ackTime()),
                row.site(), row.area(), nullable(row.unit()), row.tag(), row.description(), row.priority(), row.state(),
                decimal(row.value()), decimal(row.threshold()), nullable(row.engineeringUnit()),
                row.sourceSystem(), nullable(row.operator()))).toList();
    }

    private Object nullable(Object value) {
        return value == null ? "<null>" : value;
    }

    private String decimal(java.math.BigDecimal value) {
        return value == null ? "<null>" : value.stripTrailingZeros().toPlainString();
    }

    private int countAlarms(UUID batchId) {
        return jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM alarm_record WHERE batch_id = ?", Integer.class, batchId);
    }

    private int countStaging(UUID batchId) {
        return jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM import_staging WHERE batch_id = ?", Integer.class, batchId);
    }

    private static EmbeddedPostgres startPostgres() {
        try {
            return EmbeddedPostgres.start();
        } catch (IOException exception) {
            throw new ExceptionInInitializerError(exception);
        }
    }
}
