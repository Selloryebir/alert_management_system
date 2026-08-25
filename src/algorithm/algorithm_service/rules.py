"""确定性的 v1 报警规则计算，不访问外部状态。"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
import re
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


RULE_VERSION = "rules-v1.0.0"
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
NOISE_SCORES: dict[NoiseType, float] = {
    "DUPLICATE": 0.95,
    "CHATTER": 0.90,
    "SHORT_LIVED": 0.85,
    "PERSISTENT": 0.80,
    "NORMAL": 0.50,
}
ALARM_CLASSES: dict[NoiseType, AlarmClass] = {
    "DUPLICATE": "NUISANCE",
    "CHATTER": "NUISANCE",
    "SHORT_LIVED": "NUISANCE",
    "PERSISTENT": "ACTIONABLE",
    "NORMAL": "STANDARD",
}
CHAIN_PATTERNS: tuple[tuple[str, re.Pattern[str], CauseCategory, str, str], ...] = (
    (
        "EQUIPMENT_TRIP",
        re.compile(r"(?:EQUIPMENT[_ -]TRIP|设备跳停).*?(?:步骤|[-_ ])(\d+)(?:\D|$)", re.IGNORECASE),
        "EQUIPMENT_FAULT",
        "EQUIPMENT_TRIP_SEQUENCE",
        "设备跳停",
    ),
    (
        "PROCESS_CASCADE",
        re.compile(r"(?:PROCESS[_ -]CASCADE|工艺扰动级联).*?(?:步骤|[-_ ])(\d+)(?:\D|$)", re.IGNORECASE),
        "PROCESS_DISTURBANCE",
        "PROCESS_CASCADE_SEQUENCE",
        "工艺扰动级联",
    ),
)


def analyze(request: AnalysisRequest) -> AnalysisResponse:
    """对已通过 v1 契约校验的单批记录执行纯计算规则。"""

    matches: dict[UUID, set[NoiseType]] = {record.record_id: set() for record in request.records}
    evidence: dict[UUID, list[str]] = {record.record_id: [] for record in request.records}

    _mark_duplicates(request, matches, evidence)
    _mark_chatter(request, matches, evidence)
    _mark_short_lived(request, matches, evidence)
    _mark_persistent(request, matches, evidence)

    chains, chain_causes = _event_chains(request, evidence)
    results: list[RecordResult] = []
    for record in request.records:
        record_matches = matches[record.record_id]
        noise_type = next(
            candidate for candidate in NOISE_PRIORITY if candidate == "NORMAL" or candidate in record_matches
        )
        if not record_matches:
            evidence[record.record_id].append("未命中重复、抖动、短时恢复或持续报警规则。")
        cause, cause_evidence = _cause_category(record, chain_causes.get(record.record_id))
        evidence[record.record_id].append(cause_evidence)
        results.append(
            RecordResult(
                record_id=record.record_id,
                noise_type=noise_type,
                alarm_class=ALARM_CLASSES[noise_type],
                cause_category=cause,
                score=NOISE_SCORES[noise_type],
                evidence=evidence[record.record_id],
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


def _core(record: AlarmRecord) -> tuple[str, str, str, Decimal | None, Decimal | None]:
    return (record.description, record.priority, record.state, record.value, record.threshold)


def _mark_duplicates(
    request: AnalysisRequest,
    matches: dict[UUID, set[NoiseType]],
    evidence: dict[UUID, list[str]],
) -> None:
    previous: dict[tuple[str, tuple[object, ...]], list[AlarmRecord]] = defaultdict(list)
    window = request.parameters.duplicate_window_seconds
    for record in _ordered(request.records):
        key = (record.tag, _core(record))
        candidates = previous[key]
        candidates[:] = [item for item in candidates if _seconds(item.event_time, record.event_time) <= window]
        if candidates:
            original = candidates[-1]
            matches[record.record_id].add("DUPLICATE")
            evidence[record.record_id].append(
                f"同位号且核心值相同，在 {window} 秒窗口内重复；前序记录 {original.record_id}。"
            )
            for candidate in candidates:
                matches[candidate.record_id].add("DUPLICATE")
                evidence[candidate.record_id].append(
                    f"同位号且核心值相同，在 {window} 秒窗口内重复；后序记录 {record.record_id}。"
                )
        candidates.append(record)


def _mark_chatter(
    request: AnalysisRequest,
    matches: dict[UUID, set[NoiseType]],
    evidence: dict[UUID, list[str]],
) -> None:
    grouped: dict[str, list[AlarmRecord]] = defaultdict(list)
    for record in request.records:
        grouped[record.tag].append(record)
    window_seconds = request.parameters.chatter_window_seconds
    minimum = request.parameters.chatter_min_count
    for records in grouped.values():
        ordered = _ordered(records)
        left = 0
        qualifying: set[UUID] = set()
        for right, current in enumerate(ordered):
            while _seconds(ordered[left].event_time, current.event_time) > window_seconds:
                left += 1
            window = ordered[left : right + 1]
            states_alternate = all(
                first.state != second.state for first, second in zip(window, window[1:], strict=False)
            )
            if len(window) >= minimum and states_alternate:
                qualifying.update(record.record_id for record in window)
        for record in ordered:
            if record.record_id in qualifying:
                matches[record.record_id].add("CHATTER")
                evidence[record.record_id].append(
                    f"同位号在 {window_seconds} 秒窗口内达到至少 {minimum} 次状态交替或高频报警。"
                )


def _mark_short_lived(
    request: AnalysisRequest,
    matches: dict[UUID, set[NoiseType]],
    evidence: dict[UUID, list[str]],
) -> None:
    threshold = request.parameters.short_lived_seconds
    for record in request.records:
        if record.return_time is None:
            continue
        duration = _seconds(record.event_time, record.return_time)
        if duration <= threshold:
            matches[record.record_id].add("SHORT_LIVED")
            evidence[record.record_id].append(
                f"报警在 {duration:g} 秒内恢复，不超过 {threshold} 秒阈值。"
            )


def _mark_persistent(
    request: AnalysisRequest,
    matches: dict[UUID, set[NoiseType]],
    evidence: dict[UUID, list[str]],
) -> None:
    requires_ack = request.parameters.persistent_requires_ack
    for record in request.records:
        persistent = (
            record.state == "ACTIVE"
            and record.priority == "P1"
            and record.return_time is None
            and (record.ack_time is not None or not requires_ack)
        )
        if persistent:
            matches[record.record_id].add("PERSISTENT")
            ack_text = "已确认且" if requires_ack else ""
            evidence[record.record_id].append(f"P1 活跃报警{ack_text}尚未恢复，符合持续报警规则。")


def _chain_step(record: AlarmRecord) -> tuple[str, int, CauseCategory, str, str] | None:
    searchable = f"{record.tag} {record.description}"
    for kind, pattern, cause, rule, label in CHAIN_PATTERNS:
        match = pattern.search(searchable)
        if match is not None:
            return kind, int(match.group(1)), cause, rule, label
    return None


def _event_chains(
    request: AnalysisRequest,
    evidence: dict[UUID, list[str]],
) -> tuple[list[EventChain], dict[UUID, CauseCategory]]:
    grouped: dict[str, list[tuple[AlarmRecord, int, CauseCategory, str, str]]] = defaultdict(list)
    for record in request.records:
        parsed = _chain_step(record)
        if parsed is not None:
            kind, step, cause, rule, label = parsed
            grouped[kind].append((record, step, cause, rule, label))

    chains: list[EventChain] = []
    causes: dict[UUID, CauseCategory] = {}
    expected_steps = list(range(1, request.parameters.chain_min_steps + 1))
    for kind in sorted(grouped):
        candidates = sorted(grouped[kind], key=lambda item: (item[0].event_time, item[0].source_row))
        index = 0
        while index < len(candidates):
            if candidates[index][1] != 1:
                index += 1
                continue
            selected = [candidates[index]]
            cursor = index + 1
            for expected in expected_steps[1:]:
                while cursor < len(candidates) and candidates[cursor][1] != expected:
                    if candidates[cursor][1] == 1:
                        break
                    cursor += 1
                if cursor >= len(candidates) or candidates[cursor][1] != expected:
                    break
                if _seconds(selected[0][0].event_time, candidates[cursor][0].event_time) > request.parameters.chain_window_seconds:
                    break
                selected.append(candidates[cursor])
                cursor += 1
            if [item[1] for item in selected] != expected_steps:
                index += 1
                continue

            members = [item[0] for item in selected]
            _, _, cause, rule, label = selected[0]
            chain_id = uuid5(
                NAMESPACE_URL,
                f"{RULE_VERSION}:{kind}:" + ":".join(str(member.record_id) for member in members),
            )
            explanation = (
                f"{label}步骤 1..{request.parameters.chain_min_steps} 在 "
                f"{request.parameters.chain_window_seconds} 秒内按顺序出现；"
                "这是规则关联建议，不代表已确认根因。"
            )
            chains.append(
                EventChain(
                    chain_id=chain_id,
                    member_record_ids=[member.record_id for member in members],
                    start_time=members[0].event_time,
                    end_time=members[-1].event_time,
                    start_record_id=members[0].record_id,
                    association_rule=rule,
                    explanation=explanation,
                )
            )
            for member in members:
                causes[member.record_id] = cause
                evidence[member.record_id].append(f"命中 {rule} 关联事件链规则。")
            index = cursor

    chains.sort(key=lambda chain: (chain.start_time, str(chain.chain_id)))
    return chains, causes


def _cause_category(
    record: AlarmRecord,
    chain_cause: CauseCategory | None,
) -> tuple[CauseCategory, str]:
    if chain_cause is not None:
        return chain_cause, f"原因类别由关联序列规则建议为 {chain_cause}，不代表已确认根因。"

    text = f"{record.tag} {record.description}".lower()
    keyword_rules: tuple[tuple[CauseCategory, tuple[str, ...], str], ...] = (
        ("MAINTENANCE_TEST", ("维护", "测试", "校验", "maintenance", "test"), "维护/测试文本"),
        ("INSTRUMENT_ISSUE", ("仪表", "传感器", "变送器", "测点", "漂移", "instrument", "sensor", "drift"), "仪表文本"),
        ("EQUIPMENT_FAULT", ("设备", "故障", "跳停", "泵", "风机", "压缩机", "equipment", "fault", "trip"), "设备文本"),
        ("PROCESS_DISTURBANCE", ("工艺", "扰动", "级联", "洪泛", "process", "cascade", "alarm_flood"), "工艺文本"),
    )
    for category, keywords, reason in keyword_rules:
        if any(keyword in text for keyword in keywords):
            return category, f"依据{reason}建议原因类别为 {category}，不代表已确认根因。"
    return "UNKNOWN", "没有足够的文本或序列证据，原因类别保留 UNKNOWN。"
