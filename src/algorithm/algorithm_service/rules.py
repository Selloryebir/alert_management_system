"""确定性的 hybrid-v2 报警分析模型，不访问外部状态。"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import heapq
from math import exp, sqrt
import re
from statistics import median
from uuid import NAMESPACE_URL, UUID, uuid5

from algorithm_service.models import (
    AlarmClass,
    AlarmRecord,
    AnalysisRequest,
    AnalysisResponse,
    AnalysisSummary,
    CauseCategory,
    EventChain,
    NoiseType,
    RecordResult,
)


RULE_VERSION = "hybrid-v2.0.0"
ASSOCIATION_RULE = "MARKOV_TRANSITION_HYBRID_V2"
NOISE_TYPES: tuple[NoiseType, ...] = (
    "NORMAL",
    "DUPLICATE",
    "CHATTER",
    "SHORT_LIVED",
    "PERSISTENT",
)
CAUSE_CATEGORIES: tuple[CauseCategory, ...] = (
    "PROCESS_DISTURBANCE",
    "EQUIPMENT_FAULT",
    "INSTRUMENT_ISSUE",
    "MAINTENANCE_TEST",
    "UNKNOWN",
)
NOISE_PRIORITY: tuple[NoiseType, ...] = (
    "DUPLICATE",
    "CHATTER",
    "SHORT_LIVED",
    "PERSISTENT",
    "NORMAL",
)
ALARM_CLASSES: dict[NoiseType, AlarmClass] = {
    "DUPLICATE": "NUISANCE",
    "CHATTER": "NUISANCE",
    "SHORT_LIVED": "NUISANCE",
    "PERSISTENT": "ACTIONABLE",
    "NORMAL": "STANDARD",
}

# 经模型卡冻结的非负专家特征。英文特征按完整 token 匹配，中文特征按短语匹配。
EXPERT_WEIGHTS: dict[CauseCategory, dict[str, float]] = {
    "PROCESS_DISTURBANCE": {
        "工艺": 1.0,
        "扰动": 0.9,
        "反应器": 0.6,
        "压力": 0.4,
        "温度": 0.4,
        "流量": 0.4,
        "液位": 0.4,
        "process": 1.0,
        "upset": 0.9,
        "reactor": 0.6,
    },
    "EQUIPMENT_FAULT": {
        "设备": 0.7,
        "故障": 0.7,
        "跳停": 1.0,
        "泵": 0.8,
        "风机": 0.8,
        "压缩机": 1.0,
        "阀门": 0.7,
        "equipment": 0.7,
        "fault": 0.7,
        "trip": 1.0,
        "pump": 0.8,
        "compressor": 1.0,
        "valve": 0.7,
    },
    "INSTRUMENT_ISSUE": {
        "仪表": 1.0,
        "传感器": 1.0,
        "变送器": 1.0,
        "测点": 0.7,
        "漂移": 0.9,
        "失准": 0.9,
        "instrument": 1.0,
        "sensor": 1.0,
        "transmitter": 1.0,
        "drift": 0.9,
    },
    "MAINTENANCE_TEST": {
        "维护": 1.0,
        "检修": 1.0,
        "校验": 0.9,
        "标定": 0.9,
        "maintenance": 1.0,
        "calibration": 0.9,
        "overhaul": 0.9,
    },
}
MAINTENANCE_TERMS = frozenset(EXPERT_WEIGHTS["MAINTENANCE_TEST"])


@dataclass(frozen=True, slots=True)
class EdgeStatistic:
    count: int
    episode_support: int
    transition_probability: float
    baseline_probability: float
    lift: float
    median_lag_seconds: float


def analyze(request: AnalysisRequest) -> AnalysisResponse:
    """对已通过 v2 契约校验的单批记录执行纯计算模型。"""

    strengths: dict[UUID, dict[NoiseType, float]] = {
        record.record_id: {} for record in request.records
    }
    rule_evidence: dict[UUID, dict[NoiseType, str]] = {
        record.record_id: {} for record in request.records
    }
    _mark_duplicates(request, strengths, rule_evidence)
    _mark_chatter(request, strengths, rule_evidence)
    _mark_short_lived(request, strengths, rule_evidence)
    _mark_persistent(request, strengths, rule_evidence)

    chain_evidence: dict[UUID, list[str]] = defaultdict(list)
    chains = _event_chains(request, chain_evidence)
    results: list[RecordResult] = []
    for record in _ordered(request.records):
        record_strengths = strengths[record.record_id]
        noise_type = next(
            candidate
            for candidate in NOISE_PRIORITY
            if candidate == "NORMAL" or candidate in record_strengths
        )
        evidence = [
            rule_evidence[record.record_id][candidate]
            for candidate in NOISE_PRIORITY
            if candidate != "NORMAL" and candidate in rule_evidence[record.record_id]
        ]
        if not record_strengths:
            evidence.append(
                "EXPERT_NORMAL_V2：未命中重复、抖动、短时恢复或持续报警规则；"
                "规则强度 1.000000 表示通过当前规则集，不是正常概率。"
            )
        evidence.extend(chain_evidence[record.record_id])
        cause, cause_evidence = _cause_category(record, request)
        evidence.append(cause_evidence)
        results.append(
            RecordResult(
                record_id=record.record_id,
                noise_type=noise_type,
                alarm_class=ALARM_CLASSES[noise_type],
                cause_category=cause,
                score=_rounded(record_strengths.get(noise_type, 1.0)),
                evidence=evidence,
            )
        )

    noise_counts = Counter(result.noise_type for result in results)
    cause_counts = Counter(result.cause_category for result in results)
    return AnalysisResponse(
        analysis_run_id=request.analysis_run_id,
        contract_version=request.contract_version,
        algorithm_version=request.algorithm_version,
        rule_version=RULE_VERSION,
        parameters=request.parameters,
        record_results=results,
        event_chains=chains,
        summary=AnalysisSummary(
            input_count=len(request.records),
            success_count=len(results),
            failure_count=0,
            noise_type_counts={kind: noise_counts[kind] for kind in NOISE_TYPES},
            cause_category_counts={kind: cause_counts[kind] for kind in CAUSE_CATEGORIES},
            event_chain_count=len(chains),
        ),
        errors=[],
    )


def _ordered(records: list[AlarmRecord]) -> list[AlarmRecord]:
    return sorted(records, key=lambda item: (item.event_time, item.source_row, str(item.record_id)))


def _seconds(start: datetime, end: datetime) -> float:
    return (end - start).total_seconds()


def _rounded(value: float) -> float:
    return round(value, 6)


def _relation(record: AlarmRecord) -> tuple[str, str, str | None]:
    return record.site, record.area, record.unit


def _alarm_group(record: AlarmRecord) -> tuple[str, str, str | None, str]:
    return record.site, record.area, record.unit, record.tag


def _core(record: AlarmRecord) -> tuple[str, str, str, Decimal | None, Decimal | None]:
    return record.description, record.priority, record.state, record.value, record.threshold


def _set_match(
    strengths: dict[UUID, dict[NoiseType, float]],
    evidence: dict[UUID, dict[NoiseType, str]],
    record_id: UUID,
    noise_type: NoiseType,
    strength: float,
    explanation: str,
) -> None:
    current = strengths[record_id].get(noise_type)
    if current is None or strength > current:
        strengths[record_id][noise_type] = strength
        evidence[record_id][noise_type] = explanation


def _mark_duplicates(
    request: AnalysisRequest,
    strengths: dict[UUID, dict[NoiseType, float]],
    evidence: dict[UUID, dict[NoiseType, str]],
) -> None:
    grouped: dict[
        tuple[str, str, str | None, str, tuple[object, ...]], list[AlarmRecord]
    ] = defaultdict(list)
    for record in request.records:
        grouped[(*_alarm_group(record), _core(record))].append(record)

    window = request.parameters.duplicate_window_seconds
    for records in grouped.values():
        timestamp_groups: list[list[AlarmRecord]] = []
        for record in _ordered(records):
            if not timestamp_groups or timestamp_groups[-1][0].event_time != record.event_time:
                timestamp_groups.append([record])
            else:
                timestamp_groups[-1].append(record)
        for previous_group, current_group in zip(timestamp_groups, timestamp_groups[1:], strict=False):
            delta = _seconds(previous_group[0].event_time, current_group[0].event_time)
            if delta > window:
                continue
            strength = exp(-delta / window)
            for record in previous_group:
                _set_duplicate(record, current_group[0], delta, window, strength, strengths, evidence)
            for record in current_group:
                _set_duplicate(record, previous_group[-1], delta, window, strength, strengths, evidence)


def _set_duplicate(
    record: AlarmRecord,
    peer: AlarmRecord,
    delta: float,
    window: int,
    strength: float,
    strengths: dict[UUID, dict[NoiseType, float]],
    evidence: dict[UUID, dict[NoiseType, str]],
) -> None:
    _set_match(
        strengths,
        evidence,
        record.record_id,
        "DUPLICATE",
        strength,
        "EXPERT_DUPLICATE_V2：同关系范围、位号及核心字段完全相同；"
        f"匹配源行 {peer.source_row}，时间差 {delta:.6f} 秒，窗口 {window} 秒，"
        f"mu=exp(-{delta:.6f}/{window})={strength:.6f}。",
    )


def _binary_state(record: AlarmRecord) -> int:
    return 0 if record.state == "RETURNED" else 1


def _mark_chatter(
    request: AnalysisRequest,
    strengths: dict[UUID, dict[NoiseType, float]],
    evidence: dict[UUID, dict[NoiseType, str]],
) -> None:
    grouped: dict[tuple[str, str, str | None, str], list[AlarmRecord]] = defaultdict(list)
    for record in request.records:
        grouped[_alarm_group(record)].append(record)

    window_seconds = request.parameters.chatter_window_seconds
    minimum = request.parameters.chatter_min_count
    minimum_ratio = request.parameters.chatter_min_transition_ratio
    for records in grouped.values():
        ordered = _ordered(records)
        if len(ordered) < minimum:
            continue
        prefix_transitions = [0] * len(ordered)
        for index in range(1, len(ordered)):
            prefix_transitions[index] = prefix_transitions[index - 1] + int(
                _binary_state(ordered[index - 1]) != _binary_state(ordered[index])
            )

        transformed = [
            prefix_transitions[index] - minimum_ratio * index
            for index in range(len(ordered))
        ]
        tree_size = 1
        while tree_size < len(transformed):
            tree_size *= 2
        minimum_tree = [float("inf")] * (tree_size * 2)
        minimum_tree[tree_size : tree_size + len(transformed)] = transformed
        for index in range(tree_size - 1, 0, -1):
            minimum_tree[index] = min(
                minimum_tree[index * 2], minimum_tree[index * 2 + 1]
            )

        def first_qualifying_start(
            node: int,
            node_left: int,
            node_right: int,
            query_left: int,
            query_right: int,
            maximum_value: float,
        ) -> int | None:
            if (
                node_right < query_left
                or node_left > query_right
                or minimum_tree[node] > maximum_value + 1e-12
            ):
                return None
            if node_left == node_right:
                return node_left
            midpoint = (node_left + node_right) // 2
            found = first_qualifying_start(
                node * 2,
                node_left,
                midpoint,
                query_left,
                query_right,
                maximum_value,
            )
            if found is not None:
                return found
            return first_qualifying_start(
                node * 2 + 1,
                midpoint + 1,
                node_right,
                query_left,
                query_right,
                maximum_value,
            )

        intervals: list[tuple[int, int, float, int, int]] = []
        left = 0
        for right, current in enumerate(ordered):
            while _seconds(ordered[left].event_time, current.event_time) > window_seconds:
                left += 1
            latest_start = right - minimum + 1
            if latest_start < left:
                continue
            candidate_left = first_qualifying_start(
                1,
                0,
                tree_size - 1,
                left,
                latest_start,
                transformed[right],
            )
            if candidate_left is None:
                continue
            count = right - candidate_left + 1
            transitions = prefix_transitions[right] - prefix_transitions[candidate_left]
            ratio = transitions / (count - 1)
            intervals.append((candidate_left, right, ratio, transitions, count))

        starts: dict[int, list[tuple[int, float, int, int]]] = defaultdict(list)
        for start, end, ratio, transitions, count in intervals:
            starts[start].append((end, ratio, transitions, count))
        active: list[tuple[float, int, int, int, int]] = []
        for index, record in enumerate(ordered):
            for end, ratio, transitions, count in starts[index]:
                heapq.heappush(active, (-ratio, end, index, transitions, count))
            while active and active[0][1] < index:
                heapq.heappop(active)
            if not active:
                continue
            negative_ratio, end, start, transitions, count = active[0]
            ratio = -negative_ratio
            _set_match(
                strengths,
                evidence,
                record.record_id,
                "CHATTER",
                ratio,
                "EXPERT_CHATTER_V2：同关系范围和位号的滑动窗口命中；"
                f"窗口 {ordered[start].event_time.isoformat()} 至 {ordered[end].event_time.isoformat()}，"
                f"N={count}，首尾二值状态={_binary_state(ordered[start])}->"
                f"{_binary_state(ordered[end])}，T={transitions}，"
                f"A=T/(N-1)={ratio:.6f}，门槛 N>={minimum}、A>={minimum_ratio:.6f}。",
            )


def _mark_short_lived(
    request: AnalysisRequest,
    strengths: dict[UUID, dict[NoiseType, float]],
    evidence: dict[UUID, dict[NoiseType, str]],
) -> None:
    threshold = request.parameters.short_lived_seconds
    for record in request.records:
        if record.return_time is None:
            continue
        duration = _seconds(record.event_time, record.return_time)
        if duration <= threshold:
            strength = exp(-duration / threshold)
            _set_match(
                strengths,
                evidence,
                record.record_id,
                "SHORT_LIVED",
                strength,
                "EXPERT_SHORT_LIVED_V2：存在真实恢复时间；"
                f"持续 {duration:.6f} 秒，阈值 {threshold} 秒，"
                f"mu=exp(-{duration:.6f}/{threshold})={strength:.6f}。",
            )


def _mark_persistent(
    request: AnalysisRequest,
    strengths: dict[UUID, dict[NoiseType, float]],
    evidence: dict[UUID, dict[NoiseType, str]],
) -> None:
    requires_ack = request.parameters.persistent_requires_ack
    for record in request.records:
        conditions = (
            record.state == "ACTIVE",
            record.priority == "P1",
            record.return_time is None,
            record.ack_time is not None or not requires_ack,
        )
        if all(conditions):
            _set_match(
                strengths,
                evidence,
                record.record_id,
                "PERSISTENT",
                1.0,
                "EXPERT_PERSISTENT_V2："
                f"state=ACTIVE({conditions[0]})，priority=P1({conditions[1]})，"
                f"return_time为空({conditions[2]})，确认条件满足({conditions[3]})，mu=1.000000。",
            )


def _episodes(
    request: AnalysisRequest,
) -> dict[tuple[str, str, str | None], list[list[AlarmRecord]]]:
    grouped: dict[tuple[str, str, str | None], list[AlarmRecord]] = defaultdict(list)
    for record in request.records:
        grouped[_relation(record)].append(record)

    episodes: dict[tuple[str, str, str | None], list[list[AlarmRecord]]] = {}
    gap = request.parameters.episode_gap_seconds
    for relation in sorted(grouped, key=lambda item: tuple(value or "" for value in item)):
        relation_episodes: list[list[AlarmRecord]] = []
        current: list[AlarmRecord] = []
        for record in _ordered(grouped[relation]):
            if current and _seconds(current[-1].event_time, record.event_time) > gap:
                relation_episodes.append(_collapse_consecutive_tags(current))
                current = []
            current.append(record)
        if current:
            relation_episodes.append(_collapse_consecutive_tags(current))
        episodes[relation] = relation_episodes
    return episodes


def _collapse_consecutive_tags(records: list[AlarmRecord]) -> list[AlarmRecord]:
    collapsed: list[AlarmRecord] = []
    for record in records:
        if not collapsed or collapsed[-1].tag != record.tag:
            collapsed.append(record)
    return collapsed


def _markov_edges(
    request: AnalysisRequest,
    episodes: list[list[AlarmRecord]],
) -> dict[tuple[str, str], EdgeStatistic]:
    edge_counts: Counter[tuple[str, str]] = Counter()
    source_counts: Counter[str] = Counter()
    target_counts: Counter[str] = Counter()
    episode_counts: Counter[tuple[str, str]] = Counter()
    lags: dict[tuple[str, str], list[float]] = defaultdict(list)
    total = 0
    for episode in episodes:
        seen: set[tuple[str, str]] = set()
        for first, second in zip(episode, episode[1:], strict=False):
            edge = first.tag, second.tag
            lag = _seconds(first.event_time, second.event_time)
            if lag < 0 or first.tag == second.tag:
                continue
            edge_counts[edge] += 1
            source_counts[first.tag] += 1
            target_counts[second.tag] += 1
            lags[edge].append(lag)
            total += 1
            seen.add(edge)
        for edge in seen:
            episode_counts[edge] += 1

    if total == 0:
        return {}
    result: dict[tuple[str, str], EdgeStatistic] = {}
    for edge in sorted(edge_counts):
        source, target = edge
        transition_probability = edge_counts[edge] / source_counts[source]
        baseline_probability = target_counts[target] / total
        lift = transition_probability / baseline_probability
        statistic = EdgeStatistic(
            count=edge_counts[edge],
            episode_support=episode_counts[edge],
            transition_probability=transition_probability,
            baseline_probability=baseline_probability,
            lift=lift,
            median_lag_seconds=float(median(lags[edge])),
        )
        if (
            statistic.episode_support >= request.parameters.min_episode_support
            and statistic.transition_probability >= request.parameters.min_transition_probability
            and statistic.lift >= request.parameters.min_lift
            and statistic.median_lag_seconds <= request.parameters.chain_window_seconds
        ):
            result[edge] = statistic
    return result


def _event_chains(
    request: AnalysisRequest,
    evidence: dict[UUID, list[str]],
) -> list[EventChain]:
    chains: list[EventChain] = []
    for relation_episodes in _episodes(request).values():
        valid_edges = _markov_edges(request, relation_episodes)
        for episode in relation_episodes:
            path: list[AlarmRecord] = []
            for first, second in zip(episode, episode[1:], strict=False):
                edge = first.tag, second.tag
                actual_lag = _seconds(first.event_time, second.event_time)
                can_extend = edge in valid_edges and actual_lag <= request.parameters.chain_window_seconds
                if can_extend and path:
                    can_extend = path[-1].record_id == first.record_id
                if can_extend:
                    if not path:
                        path = [first]
                    path.append(second)
                    continue
                _append_chain(request, path, valid_edges, chains, evidence)
                path = (
                    [first, second]
                    if edge in valid_edges and actual_lag <= request.parameters.chain_window_seconds
                    else []
                )
            _append_chain(request, path, valid_edges, chains, evidence)

    chains.sort(key=lambda chain: (chain.start_time, str(chain.chain_id)))
    return chains


def _append_chain(
    request: AnalysisRequest,
    members: list[AlarmRecord],
    valid_edges: dict[tuple[str, str], EdgeStatistic],
    chains: list[EventChain],
    evidence: dict[UUID, list[str]],
) -> None:
    if len(members) < request.parameters.chain_min_steps:
        return
    edges = [(first.tag, second.tag) for first, second in zip(members, members[1:], strict=False)]
    details = []
    for edge in edges:
        statistic = valid_edges[edge]
        details.append(
            f"{edge[0]}->{edge[1]}:C={statistic.count},E={statistic.episode_support},"
            f"P(v|u)={statistic.transition_probability:.6f},P(v)={statistic.baseline_probability:.6f},"
            f"lift={statistic.lift:.6f},median_lag={statistic.median_lag_seconds:.6f}s"
        )
    member_ids = [member.record_id for member in members]
    chain_id = uuid5(
        NAMESPACE_URL,
        f"{RULE_VERSION}:{ASSOCIATION_RULE}:" + ":".join(str(member_id) for member_id in member_ids),
    )
    explanation = (
        f"{ASSOCIATION_RULE}；关系键为 {members[0].site}/{members[0].area}/"
        f"{members[0].unit or '未指定单元'}；"
        + "；".join(details)
        + f"；门槛 E>={request.parameters.min_episode_support}、"
        f"P>={request.parameters.min_transition_probability:.6f}、"
        f"lift>={request.parameters.min_lift:.6f}、"
        f"median_lag<={request.parameters.chain_window_seconds}s。"
        "这是基于重复报警片段的统计关联建议，不代表已确认根因。"
    )
    chains.append(
        EventChain(
            chain_id=chain_id,
            member_record_ids=member_ids,
            start_time=members[0].event_time,
            end_time=members[-1].event_time,
            start_record_id=members[0].record_id,
            association_rule=ASSOCIATION_RULE,
            explanation=explanation,
        )
    )
    member_rows = ",".join(str(member.source_row) for member in members)
    chain_note = (
        f"{ASSOCIATION_RULE}：属于成员源行[{member_rows}]的关联链；"
        "时间最早只表示候选起始报警，统计关联不代表已确认根因。"
    )
    for member in members:
        evidence[member.record_id].append(chain_note)


def _term_present(term: str, text: str, english_tokens: set[str]) -> bool:
    if term.isascii():
        return term in english_tokens
    return term in text


def _cause_category(record: AlarmRecord, request: AnalysisRequest) -> tuple[CauseCategory, str]:
    text = f"{record.tag} {record.description}".lower()
    english_tokens = set(re.findall(r"[a-z0-9]+", text))
    feature_terms = sorted({term for weights in EXPERT_WEIGHTS.values() for term in weights})
    features = {
        term: 1.0 if _term_present(term, text, english_tokens) else 0.0
        for term in feature_terms
    }
    feature_norm = sqrt(sum(value * value for value in features.values()))
    maintenance_present = any(features[term] > 0 for term in MAINTENANCE_TERMS)
    scores: dict[CauseCategory, float] = {}
    contributions: dict[CauseCategory, list[str]] = {}
    for category, weights in EXPERT_WEIGHTS.items():
        weight_norm = sqrt(sum(weight * weight for weight in weights.values()))
        numerator = sum(weights[term] * features[term] for term in weights)
        score = numerator / (weight_norm * feature_norm) if weight_norm and feature_norm else 0.0
        if maintenance_present and category != "MAINTENANCE_TEST":
            score = 0.0
        scores[category] = score
        contributions[category] = [
            f"{term}={weight:.2f}" for term, weight in weights.items() if features[term] > 0
        ]

    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    best_category, best_score = ranked[0]
    second_score = ranked[1][1]
    margin = best_score - second_score
    score_text = ",".join(f"{category}={scores[category]:.6f}" for category in sorted(scores))
    qualifies = (
        best_score >= request.parameters.expert_min_score
        and margin >= request.parameters.expert_min_margin
    )
    if not qualifies:
        return (
            "UNKNOWN",
            "EXPERT_CAUSE_V2："
            f"类别分数[{score_text}]，最高分 {best_score:.6f}，次高分 {second_score:.6f}，"
            f"margin={margin:.6f}；门槛 score>={request.parameters.expert_min_score:.6f}、"
            f"margin>={request.parameters.expert_min_margin:.6f}，证据不足或冲突，保留 UNKNOWN。",
        )
    contribution_text = ",".join(contributions[best_category]) or "无"
    veto_text = "；维护特征否决其他原因类别" if maintenance_present else ""
    return (
        best_category,
        "EXPERT_CAUSE_V2："
        f"类别分数[{score_text}]，选择 {best_category}，贡献特征[{contribution_text}]，"
        f"最高分 {best_score:.6f}，次高分 {second_score:.6f}，margin={margin:.6f}"
        f"{veto_text}；这是可弃权专家建议，不代表已确认根因。",
    )
