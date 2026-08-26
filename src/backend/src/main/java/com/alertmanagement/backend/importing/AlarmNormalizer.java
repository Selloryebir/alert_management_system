package com.alertmanagement.backend.importing;

import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import org.springframework.stereotype.Component;
import com.alertmanagement.backend.project.ProjectValidationRules;
import com.alertmanagement.backend.api.BusinessApiException;
import org.springframework.http.HttpStatus;

@Component
class AlarmNormalizer {

    private static final ZoneId DEFAULT_ZONE = ZoneId.of("Asia/Shanghai");
    private static final Set<String> REQUIRED_FIELDS = Set.of(
            "event_time", "site", "area", "tag", "description", "priority", "state", "source_system");
    private static final Map<String, List<String>> ALIASES = aliases();
    private static final List<DateTimeFormatter> LOCAL_TIME_FORMATS = List.of(
            DateTimeFormatter.ISO_LOCAL_DATE_TIME,
            DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss"),
            DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm"),
            DateTimeFormatter.ofPattern("yyyy/M/d H:mm:ss"),
            DateTimeFormatter.ofPattern("yyyy/M/d H:mm"));

    ValidatedImport normalize(SourceTable table, Map<String, String> requestedMapping) {
        return normalize(table, requestedMapping, Map.of());
    }

    ValidatedImport normalize(SourceTable table, Map<String, String> requestedMapping,
            Map<Integer, Map<String, String>> corrections) {
        List<ImportError> errors = new ArrayList<>(table.errors());
        Map<String, String> mapping = resolveMapping(table.headers(), requestedMapping, errors);
        List<NormalizedAlarm> normalized = new ArrayList<>();
        int validRows = 0;
        Set<Integer> sourceRows = new LinkedHashSet<>();

        if (table.rows().isEmpty()) {
            errors.add(new ImportError(2, "_row", "REQUIRED_VALUE_MISSING", "文件没有数据行"));
        }

        for (SourceTable.SourceRow row : table.rows()) {
            int errorStart = errors.size();
            Map<String, String> rowCorrections = corrections.getOrDefault(row.sourceRow(), Map.of());
            if (!sourceRows.add(row.sourceRow())) {
                errors.add(new ImportError(row.sourceRow(), "source_row", "DUPLICATE_SOURCE_ROW",
                        "源数据行号重复"));
            }

            OffsetDateTime eventTime = time(row, mapping, rowCorrections, "event_time", true, errors);
            OffsetDateTime returnTime = time(row, mapping, rowCorrections, "return_time", false, errors);
            OffsetDateTime ackTime = time(row, mapping, rowCorrections, "ack_time", false, errors);
            String site = text(row, mapping, rowCorrections, "site", true, 100, errors);
            String area = text(row, mapping, rowCorrections, "area", true, 100, errors);
            String unit = text(row, mapping, rowCorrections, "unit", false, 100, errors);
            String tag = text(row, mapping, rowCorrections, "tag", true, 120, errors);
            String description = text(row, mapping, rowCorrections, "description", true, 500, errors);
            String priority = enumeration(row, mapping, rowCorrections, "priority", true,
                    Set.of("P1", "P2", "P3", "P4"), errors);
            String state = enumeration(row, mapping, rowCorrections, "state", true,
                    Set.of("ACTIVE", "RETURNED", "ACKNOWLEDGED"), errors);
            BigDecimal value = number(row, mapping, rowCorrections, "value", errors);
            BigDecimal threshold = number(row, mapping, rowCorrections, "threshold", errors);
            String engineeringUnit = text(row, mapping, rowCorrections, "engineering_unit", false, 40, errors);
            String sourceSystem = text(row, mapping, rowCorrections, "source_system", true, 100, errors);
            String operator = text(row, mapping, rowCorrections, "operator", false, 100, errors);

            validateTimeOrder(row.sourceRow(), eventTime, returnTime, "return_time", errors);
            validateTimeOrder(row.sourceRow(), eventTime, ackTime, "ack_time", errors);
            ensureErrorLimit(errors);

            if (errors.size() == errorStart) {
                validRows++;
                normalized.add(new NormalizedAlarm(
                        UUID.randomUUID(), row.sourceRow(), eventTime, returnTime, ackTime,
                        site, area, unit, tag, description, priority, state, value, threshold,
                        engineeringUnit, sourceSystem, operator, Map.copyOf(row.values())));
            }
        }

        if (hasGlobalError(errors)) {
            validRows = 0;
            normalized.clear();
        }

        return new ValidatedImport(
                table.format(), table.headers(), Map.copyOf(mapping), table.rows().size(), validRows,
                List.copyOf(errors), List.copyOf(normalized));
    }

    boolean supportsField(String field) {
        return ALIASES.containsKey(field);
    }

    ValidatedImport applyProjectRules(ValidatedImport validated, ProjectValidationRules rules) {
        if (rules == null) {
            return validated;
        }
        List<ImportError> errors = new ArrayList<>(validated.errors());
        List<NormalizedAlarm> records = new ArrayList<>();
        for (NormalizedAlarm record : validated.records()) {
            int before = errors.size();
            for (String field : rules.requiredFields()) {
                if (fieldValue(record, field) == null) {
                    errors.add(new ImportError(record.sourceRow(), field, "PROJECT_RULE_REQUIRED",
                            "项目校验规则“附加必填字段”未通过：" + field));
                }
            }
            range(record.sourceRow(), "value", record.value(), rules.valueMin(), rules.valueMax(), errors);
            range(record.sourceRow(), "threshold", record.threshold(),
                    rules.thresholdMin(), rules.thresholdMax(), errors);
            ensureErrorLimit(errors);
            if (errors.size() == before) {
                records.add(record);
            }
        }
        return new ValidatedImport(validated.format(), validated.headers(), validated.mapping(),
                validated.totalRows(), records.size(), List.copyOf(errors), List.copyOf(records));
    }

    private Object fieldValue(NormalizedAlarm record, String field) {
        return switch (field) {
            case "event_time" -> record.eventTime();
            case "return_time" -> record.returnTime();
            case "ack_time" -> record.ackTime();
            case "site" -> record.site();
            case "area" -> record.area();
            case "unit" -> record.unit();
            case "tag" -> record.tag();
            case "description" -> record.description();
            case "priority" -> record.priority();
            case "state" -> record.state();
            case "value" -> record.value();
            case "threshold" -> record.threshold();
            case "engineering_unit" -> record.engineeringUnit();
            case "source_system" -> record.sourceSystem();
            case "operator" -> record.operator();
            default -> null;
        };
    }

    private void range(int sourceRow, String field, BigDecimal value, BigDecimal minimum,
            BigDecimal maximum, List<ImportError> errors) {
        if (value == null) {
            return;
        }
        if ((minimum != null && value.compareTo(minimum) < 0)
                || (maximum != null && value.compareTo(maximum) > 0)) {
            errors.add(new ImportError(sourceRow, field, "PROJECT_RULE_RANGE",
                    "项目校验规则“数值范围”未通过：" + field));
        }
    }

    private Map<String, String> resolveMapping(
            List<String> headers,
            Map<String, String> requested,
            List<ImportError> errors) {
        Map<String, String> resolved = new LinkedHashMap<>();
        for (Map.Entry<String, List<String>> entry : ALIASES.entrySet()) {
            findHeader(headers, entry.getValue()).ifPresent(header -> resolved.put(entry.getKey(), header));
        }

        if (requested != null) {
            for (Map.Entry<String, String> entry : requested.entrySet()) {
                String target = entry.getKey() == null ? "" : entry.getKey().trim();
                if (!ALIASES.containsKey(target)) {
                    errors.add(new ImportError(1, target, "INVALID_MAPPING", "未知目标字段：" + target));
                    continue;
                }
                String source = entry.getValue() == null ? "" : entry.getValue().trim();
                java.util.Optional<String> actual = findHeader(headers, List.of(source));
                if (source.isEmpty() || actual.isEmpty()) {
                    resolved.remove(target);
                    errors.add(new ImportError(1, target, "MISSING_HEADER", "映射的源表头不存在：" + source));
                } else {
                    resolved.put(target, actual.get());
                }
            }
        }

        for (String field : REQUIRED_FIELDS) {
            if (!resolved.containsKey(field)) {
                errors.add(new ImportError(1, field, "MISSING_HEADER", "缺少必填字段表头：" + field));
            }
        }
        return resolved;
    }

    private java.util.Optional<String> findHeader(List<String> headers, List<String> candidates) {
        for (String candidate : candidates) {
            for (String header : headers) {
                if (header.trim().equalsIgnoreCase(candidate.trim())) {
                    return java.util.Optional.of(header.trim());
                }
            }
        }
        return java.util.Optional.empty();
    }

    private String raw(SourceTable.SourceRow row, Map<String, String> mapping,
            Map<String, String> corrections, String field) {
        if (corrections.containsKey(field)) {
            String corrected = corrections.get(field);
            return corrected.trim().isEmpty() ? null : corrected.trim();
        }
        String header = mapping.get(field);
        if (header == null) {
            return null;
        }
        String value = row.values().get(header);
        return value == null || value.trim().isEmpty() ? null : value.trim();
    }

    private String text(
            SourceTable.SourceRow row,
            Map<String, String> mapping,
            Map<String, String> corrections,
            String field,
            boolean required,
            int maximumLength,
            List<ImportError> errors) {
        String value = raw(row, mapping, corrections, field);
        if (value == null) {
            if (required && (mapping.containsKey(field) || corrections.containsKey(field))) {
                errors.add(new ImportError(row.sourceRow(), field, "REQUIRED_VALUE_MISSING",
                        "必填字段不能为空：" + field));
            }
            return null;
        }
        if (value.length() > maximumLength) {
            errors.add(new ImportError(row.sourceRow(), field, "VALUE_TOO_LONG",
                    "字段长度不能超过 " + maximumLength + " 个字符：" + field));
        }
        return value;
    }

    private OffsetDateTime time(
            SourceTable.SourceRow row,
            Map<String, String> mapping,
            Map<String, String> corrections,
            String field,
            boolean required,
            List<ImportError> errors) {
        String value = raw(row, mapping, corrections, field);
        if (value == null) {
            if (required && (mapping.containsKey(field) || corrections.containsKey(field))) {
                errors.add(new ImportError(row.sourceRow(), field, "REQUIRED_VALUE_MISSING",
                        "必填时间不能为空：" + field));
            }
            return null;
        }
        try {
            return parseTime(value);
        } catch (DateTimeParseException exception) {
            errors.add(new ImportError(row.sourceRow(), field, "INVALID_TIME", "时间格式无效：" + value));
            return null;
        }
    }

    private OffsetDateTime parseTime(String value) {
        try {
            return OffsetDateTime.parse(value, DateTimeFormatter.ISO_OFFSET_DATE_TIME)
                    .withOffsetSameInstant(ZoneOffset.UTC);
        } catch (DateTimeParseException ignored) {
            try {
                return ZonedDateTime.parse(value, DateTimeFormatter.ISO_ZONED_DATE_TIME)
                        .withZoneSameInstant(ZoneOffset.UTC)
                        .toOffsetDateTime();
            } catch (DateTimeParseException ignoredAgain) {
                try {
                    return Instant.parse(value).atOffset(ZoneOffset.UTC);
                } catch (DateTimeParseException ignoredInstant) {
                    for (DateTimeFormatter formatter : LOCAL_TIME_FORMATS) {
                        try {
                            return LocalDateTime.parse(value, formatter)
                                    .atZone(DEFAULT_ZONE)
                                    .withZoneSameInstant(ZoneOffset.UTC)
                                    .toOffsetDateTime();
                        } catch (DateTimeParseException ignoredLocal) {
                            // Try the next supported local-time format.
                        }
                    }
                    throw new DateTimeParseException("unsupported time", value, 0);
                }
            }
        }
    }

    private String enumeration(
            SourceTable.SourceRow row,
            Map<String, String> mapping,
            Map<String, String> corrections,
            String field,
            boolean required,
            Set<String> accepted,
            List<ImportError> errors) {
        String value = raw(row, mapping, corrections, field);
        if (value == null) {
            if (required && (mapping.containsKey(field) || corrections.containsKey(field))) {
                errors.add(new ImportError(row.sourceRow(), field, "REQUIRED_VALUE_MISSING",
                        "必填枚举不能为空：" + field));
            }
            return null;
        }
        if (!accepted.contains(value)) {
            errors.add(new ImportError(row.sourceRow(), field, "INVALID_ENUM", "枚举值无效：" + value));
            return null;
        }
        return value;
    }

    private BigDecimal number(
            SourceTable.SourceRow row,
            Map<String, String> mapping,
            Map<String, String> corrections,
            String field,
            List<ImportError> errors) {
        String value = raw(row, mapping, corrections, field);
        if (value == null) {
            return null;
        }
        try {
            return new BigDecimal(value);
        } catch (NumberFormatException exception) {
            errors.add(new ImportError(row.sourceRow(), field, "INVALID_NUMBER", "数字格式无效：" + value));
            return null;
        }
    }

    private void validateTimeOrder(
            int sourceRow,
            OffsetDateTime eventTime,
            OffsetDateTime laterTime,
            String field,
            List<ImportError> errors) {
        if (eventTime != null && laterTime != null && laterTime.isBefore(eventTime)) {
            errors.add(new ImportError(sourceRow, field, "TIME_ORDER_INVALID",
                    field + " 不得早于 event_time"));
        }
    }

    private boolean hasGlobalError(List<ImportError> errors) {
        return errors.stream().anyMatch(error -> error.sourceRow() == 1
                && Set.of("MISSING_HEADER", "DUPLICATE_HEADER", "INVALID_MAPPING").contains(error.code()));
    }

    private void ensureErrorLimit(List<ImportError> errors) {
        if (errors.size() > ImportLimits.MAX_ERRORS) {
            throw new BusinessApiException(HttpStatus.PAYLOAD_TOO_LARGE,
                    "IMPORT_ERROR_LIMIT", "校验错误不能超过 1,000 个，请离线修正后重试");
        }
    }

    private static Map<String, List<String>> aliases() {
        Map<String, List<String>> aliases = new LinkedHashMap<>();
        aliases.put("event_time", List.of("event_time", "event time", "报警时间", "发生时间"));
        aliases.put("return_time", List.of("return_time", "return time", "恢复时间"));
        aliases.put("ack_time", List.of("ack_time", "ack time", "确认时间"));
        aliases.put("site", List.of("site", "厂区", "地点"));
        aliases.put("area", List.of("area", "区域", "装置", "装置/区域"));
        aliases.put("unit", List.of("unit", "工艺单元", "单元"));
        aliases.put("tag", List.of("tag", "tag_name", "报警位号", "位号"));
        aliases.put("description", List.of("description", "alarm_description", "报警描述", "描述"));
        aliases.put("priority", List.of("priority", "priority_level", "报警级别", "优先级"));
        aliases.put("state", List.of("state", "alarm_state", "报警状态", "状态"));
        aliases.put("value", List.of("value", "alarm_value", "报警值", "当前值"));
        aliases.put("threshold", List.of("threshold", "报警阈值", "阈值"));
        aliases.put("engineering_unit", List.of("engineering_unit", "engineering unit", "工程单位", "单位"));
        aliases.put("source_system", List.of("source_system", "source system", "来源系统", "源系统"));
        aliases.put("operator", List.of("operator", "operator_name", "操作员"));
        return Map.copyOf(aliases);
    }
}
