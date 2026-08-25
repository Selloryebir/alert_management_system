package com.alertmanagement.backend.analysis;

import com.fasterxml.jackson.annotation.JsonProperty;
import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;

record AnalysisParameters(
        @JsonProperty("duplicate_window_seconds") Integer duplicateWindowSeconds,
        @JsonProperty("chatter_window_seconds") Integer chatterWindowSeconds,
        @JsonProperty("chatter_min_count") Integer chatterMinCount,
        @JsonProperty("chatter_min_transition_ratio") Double chatterMinTransitionRatio,
        @JsonProperty("short_lived_seconds") Integer shortLivedSeconds,
        @JsonProperty("persistent_requires_ack") Boolean persistentRequiresAck,
        @JsonProperty("episode_gap_seconds") Integer episodeGapSeconds,
        @JsonProperty("chain_window_seconds") Integer chainWindowSeconds,
        @JsonProperty("chain_min_steps") Integer chainMinSteps,
        @JsonProperty("min_episode_support") Integer minEpisodeSupport,
        @JsonProperty("min_transition_probability") Double minTransitionProbability,
        @JsonProperty("min_lift") Double minLift,
        @JsonProperty("expert_min_score") Double expertMinScore,
        @JsonProperty("expert_min_margin") Double expertMinMargin) {

    static AnalysisParameters defaults() {
        return new AnalysisParameters(30, 60, 4, 0.8, 10, true, 60, 60, 5, 3, 0.6, 2.0, 0.35, 0.10);
    }

    Map<String, Object> validatedMap() {
        positive(duplicateWindowSeconds, "重复报警窗口");
        positive(chatterWindowSeconds, "抖动检测窗口");
        minimum(chatterMinCount, 2, "抖动最少记录数");
        ratio(chatterMinTransitionRatio, "抖动最小转换比");
        positive(shortLivedSeconds, "短时恢复阈值");
        require(persistentRequiresAck != null, "持续报警确认要求不能为空");
        positive(episodeGapSeconds, "事件片段间隔");
        positive(chainWindowSeconds, "关联边延迟窗口");
        require(chainMinSteps != null && chainMinSteps >= 2 && chainMinSteps <= 5,
                "事件链最少成员数必须在 2 到 5 之间");
        minimum(minEpisodeSupport, 2, "最少事件片段支持数");
        require(minTransitionProbability != null && Double.isFinite(minTransitionProbability)
                && minTransitionProbability > 0 && minTransitionProbability <= 1,
                "最小转移概率必须大于 0 且不超过 1");
        require(minLift != null && Double.isFinite(minLift) && minLift >= 1,
                "最小提升度不得小于 1");
        ratio(expertMinScore, "专家分类最小分数");
        ratio(expertMinMargin, "专家分类最小差值");

        Map<String, Object> values = new LinkedHashMap<>();
        values.put("duplicate_window_seconds", duplicateWindowSeconds);
        values.put("chatter_window_seconds", chatterWindowSeconds);
        values.put("chatter_min_count", chatterMinCount);
        values.put("chatter_min_transition_ratio", chatterMinTransitionRatio);
        values.put("short_lived_seconds", shortLivedSeconds);
        values.put("persistent_requires_ack", persistentRequiresAck);
        values.put("episode_gap_seconds", episodeGapSeconds);
        values.put("chain_window_seconds", chainWindowSeconds);
        values.put("chain_min_steps", chainMinSteps);
        values.put("min_episode_support", minEpisodeSupport);
        values.put("min_transition_probability", minTransitionProbability);
        values.put("min_lift", minLift);
        values.put("expert_min_score", expertMinScore);
        values.put("expert_min_margin", expertMinMargin);
        return values;
    }

    private static void positive(Integer value, String label) {
        require(value != null && value > 0, label + "必须大于 0");
    }

    private static void minimum(Integer value, int lowerBound, String label) {
        require(value != null && value >= lowerBound, label + "不得小于 " + lowerBound);
    }

    private static void ratio(Double value, String label) {
        require(value != null && Double.isFinite(value) && value >= 0 && value <= 1,
                label + "必须在 0 到 1 之间");
    }

    private static void require(boolean condition, String message) {
        if (!condition) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, message);
        }
    }
}
