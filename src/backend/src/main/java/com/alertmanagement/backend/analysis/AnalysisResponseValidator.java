package com.alertmanagement.backend.analysis;

import com.alertmanagement.backend.config.AppProperties;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import org.springframework.stereotype.Component;

@Component
class AnalysisResponseValidator {

    static final String RULE_VERSION = "rules-v1.0.0";
    private static final Set<String> NOISE_TYPES = Set.of(
            "NORMAL", "DUPLICATE", "CHATTER", "SHORT_LIVED", "PERSISTENT");
    private static final Set<String> ALARM_CLASSES = Set.of("NUISANCE", "ACTIONABLE", "STANDARD");
    private static final Set<String> CAUSE_CATEGORIES = Set.of(
            "PROCESS_DISTURBANCE", "EQUIPMENT_FAULT", "INSTRUMENT_ISSUE", "MAINTENANCE_TEST", "UNKNOWN");

    private final AppProperties properties;
    private final ObjectMapper objectMapper;

    AnalysisResponseValidator(AppProperties properties, ObjectMapper objectMapper) {
        this.properties = properties;
        this.objectMapper = objectMapper;
    }

    ValidatedAnalysis validate(AnalysisRequest request, AlgorithmResponse response) {
        require(response != null, "算法响应为空");
        require(request.analysisRunId().equals(response.analysisRunId()), "算法响应运行 ID 不匹配");
        require(properties.algorithm().contractVersion().equals(response.contractVersion()), "算法响应契约版本不匹配");
        require(properties.algorithm().version().equals(response.algorithmVersion()), "算法响应版本不匹配");
        require(RULE_VERSION.equals(response.ruleVersion()), "算法规则版本不匹配");
        require(response.parameters() != null
                && objectMapper.valueToTree(request.parameters()).equals(response.parameters()), "算法响应规则参数不匹配");
        require(response.errors() != null && response.errors().isEmpty(), "算法响应包含记录错误");
        require(response.recordResults() != null, "算法响应缺少逐记录结果");
        require(response.eventChains() != null, "算法响应缺少事件链");
        require(response.summary() != null, "算法响应缺少摘要");

        Map<UUID, AlarmRecordRequest> records = new LinkedHashMap<>();
        request.records().forEach(record -> records.put(record.recordId(), record));
        Set<UUID> seen = new HashSet<>();
        Map<String, Integer> noiseCounts = zeroCounts(NOISE_TYPES);
        Map<String, Integer> causeCounts = zeroCounts(CAUSE_CATEGORIES);
        for (AlgorithmRecordResult result : response.recordResults()) {
            require(result != null && result.recordId() != null && records.containsKey(result.recordId()),
                    "逐记录结果包含不属于本批次的记录");
            require(seen.add(result.recordId()), "逐记录结果包含重复记录");
            require(NOISE_TYPES.contains(result.noiseType()), "逐记录结果噪声类型非法");
            require(CAUSE_CATEGORIES.contains(result.causeCategory()), "逐记录结果原因类别非法");
            require(ALARM_CLASSES.contains(result.alarmClass()), "逐记录结果报警类别非法");
            require(result.score() != null && result.score().signum() >= 0
                    && result.score().compareTo(java.math.BigDecimal.ONE) <= 0, "逐记录结果分数非法");
            require(result.evidence() != null && result.evidence().stream().allMatch(item -> item != null && !item.isBlank()),
                    "逐记录结果依据非法");
            noiseCounts.merge(result.noiseType(), 1, Integer::sum);
            causeCounts.merge(result.causeCategory(), 1, Integer::sum);
        }
        require(seen.equals(records.keySet()), "逐记录结果未唯一覆盖全部输入记录");

        Set<String> chainIds = new HashSet<>();
        for (AlgorithmEventChain chain : response.eventChains()) {
            require(chain != null && chain.chainId() != null && !chain.chainId().isBlank()
                    && chainIds.add(chain.chainId()), "事件链 ID 非法或重复");
            int minimumSteps = (Integer) request.parameters().get("chain_min_steps");
            require(chain.memberRecordIds() != null && chain.memberRecordIds().size() >= minimumSteps,
                    "事件链成员数量不足");
            require(chain.startRecordId() != null && chain.startRecordId().equals(chain.memberRecordIds().getFirst()),
                    "事件链起点与首成员不一致");
            require(chain.startTime() != null && chain.endTime() != null
                    && !chain.endTime().isBefore(chain.startTime()), "事件链时间顺序非法");
            require(chain.associationRule() != null && !chain.associationRule().isBlank(), "事件链关联规则为空");
            require(chain.explanation() != null && !chain.explanation().isBlank(), "事件链说明为空");
            Set<UUID> memberIds = new HashSet<>();
            AlarmRecordRequest previous = null;
            for (UUID memberId : chain.memberRecordIds()) {
                AlarmRecordRequest member = records.get(memberId);
                require(member != null, "事件链包含不属于本批次的记录");
                require(memberIds.add(memberId), "事件链包含重复成员");
                require(previous == null || member.eventTime().isAfter(previous.eventTime())
                        || member.eventTime().isEqual(previous.eventTime())
                        && member.sourceRow() > previous.sourceRow(), "事件链成员顺序非法");
                previous = member;
            }
            AlarmRecordRequest first = records.get(chain.memberRecordIds().getFirst());
            AlarmRecordRequest last = records.get(chain.memberRecordIds().getLast());
            require(chain.startTime().isEqual(first.eventTime()) && chain.endTime().isEqual(last.eventTime()),
                    "事件链时间与成员不一致");
        }

        AlgorithmSummary summary = response.summary();
        int inputCount = request.records().size();
        require(Integer.valueOf(inputCount).equals(summary.inputCount()), "摘要输入数量不匹配");
        require(Integer.valueOf(inputCount).equals(summary.successCount()), "摘要成功数量不匹配");
        require(Integer.valueOf(0).equals(summary.failureCount()), "摘要失败数量不为零");
        require(noiseCounts.equals(summary.noiseTypeCounts()), "摘要噪声类型计数不匹配");
        require(causeCounts.equals(summary.causeCategoryCounts()), "摘要原因类别计数不匹配");
        require(Integer.valueOf(response.eventChains().size()).equals(summary.eventChainCount()), "摘要事件链数量不匹配");

        return new ValidatedAnalysis(response.ruleVersion(), List.copyOf(response.recordResults()),
                List.copyOf(response.eventChains()), new AnalysisView.Summary(
                inputCount, inputCount, 0, Map.copyOf(noiseCounts), Map.copyOf(causeCounts),
                response.eventChains().size()));
    }

    private void require(boolean condition, String message) {
        if (!condition) {
            throw new AnalysisCallException(message + "，可重试");
        }
    }

    private Map<String, Integer> zeroCounts(Set<String> values) {
        Map<String, Integer> counts = new HashMap<>();
        values.forEach(value -> counts.put(value, 0));
        return counts;
    }
}
