from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from math import exp
from pathlib import Path
import random
from typing import Any
from uuid import NAMESPACE_DNS, UUID, uuid5

import pytest
from fastapi.testclient import TestClient

from algorithm_service.app import app
from algorithm_service.models import AnalysisRequest
from algorithm_service.rules import analyze as analyze_rules


client = TestClient(app)
BASE_TIME = datetime(2031, 4, 8, 9, 0, tzinfo=timezone(timedelta(hours=8)))
PARAMETERS = {
    "duplicate_window_seconds": 30,
    "chatter_window_seconds": 60,
    "chatter_min_count": 4,
    "chatter_min_transition_ratio": 0.8,
    "short_lived_seconds": 10,
    "persistent_requires_ack": True,
    "episode_gap_seconds": 60,
    "chain_window_seconds": 60,
    "chain_min_steps": 5,
    "min_episode_support": 3,
    "min_transition_probability": 0.6,
    "min_lift": 2.0,
    "expert_min_score": 0.35,
    "expert_min_margin": 0.1,
}


def identity(name: str) -> str:
    return str(uuid5(NAMESPACE_DNS, f"algorithm-v2-test:{name}"))


def record(
    name: str,
    seconds: int,
    *,
    tag: str | None = None,
    description: str = "普通运行报警",
    priority: str = "P3",
    state: str = "ACTIVE",
    return_after: int | None = None,
    ack_after: int | None = None,
    value: str = "50.00",
    site: str = "SITE-A",
    area: str = "AREA-A",
    unit: str | None = "UNIT-A",
    source_row: int | None = None,
) -> dict[str, Any]:
    event_time = BASE_TIME + timedelta(seconds=seconds)
    row = source_row if source_row is not None else seconds + 2
    return {
        "record_id": identity(name),
        "batch_id": identity("batch"),
        "source_row": row,
        "event_time": event_time.isoformat(),
        "return_time": (
            (event_time + timedelta(seconds=return_after)).isoformat()
            if return_after is not None
            else None
        ),
        "ack_time": (
            (event_time + timedelta(seconds=ack_after)).isoformat()
            if ack_after is not None
            else None
        ),
        "site": site,
        "area": area,
        "unit": unit,
        "tag": tag or f"TAG-{name}",
        "description": description,
        "priority": priority,
        "state": state,
        "value": value,
        "threshold": "75.00",
        "engineering_unit": "unit",
        "source_system": "LAB-DCS",
        "operator": "operator-a",
        "raw_payload": {"trace": name},
    }


def payload(
    records: list[dict[str, Any]],
    *,
    run: str = "run",
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "analysis_run_id": identity(run),
        "contract_version": "v2",
        "algorithm_version": "0.2.0",
        "parameters": dict(parameters or PARAMETERS),
        "records": records,
    }


def analyze(
    records: list[dict[str, Any]],
    *,
    run: str = "run",
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = client.post("/api/v2/analyze", json=payload(records, run=run, parameters=parameters))
    assert response.status_code == 200, response.text
    return response.json()


def by_id(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["record_id"]: item for item in result["record_results"]}


def sequence_records(
    episode: int,
    start: int,
    *,
    tags: tuple[str, ...] = ("A-17", "B-29", "C-41", "D-53", "E-67"),
    unit: str = "UNIT-A",
    spacing: int = 5,
) -> list[dict[str, Any]]:
    return [
        record(
            f"episode-{episode}-{index}",
            start + index * spacing,
            tag=tag,
            description="中性运行信息",
            value=f"{51 + episode + index / 10:.2f}",
            unit=unit,
            source_row=episode * 10 + index + 2,
        )
        for index, tag in enumerate(tags)
    ]


def normalized(result: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    labels = {item["record_id"]: item["tag"] for item in records}
    return {
        "records": sorted(
            (
                labels[item["record_id"]],
                item["noise_type"],
                item["alarm_class"],
                item["cause_category"],
                item["score"],
            )
            for item in result["record_results"]
        ),
        "chains": sorted(
            tuple(labels[record_id] for record_id in chain["member_record_ids"])
            for chain in result["event_chains"]
        ),
        "summary": result["summary"],
    }


def test_v2_endpoint_returns_versions_and_echoes_all_parameters() -> None:
    request = payload([record("identity", 0)], run="identity-run")
    response = client.post("/api/v2/analyze", json=request)

    assert response.status_code == 200
    result = response.json()
    assert result["analysis_run_id"] == request["analysis_run_id"]
    assert result["contract_version"] == "v2"
    assert result["algorithm_version"] == "0.2.0"
    assert result["rule_version"] == "hybrid-v2.0.0"
    assert result["parameters"] == PARAMETERS
    assert client.post("/api/v1/analyze", json=request).status_code == 404


def test_noise_rules_use_explicit_formula_scores_and_explanations() -> None:
    records = [
        record("duplicate-a", 0, tag="DUP-A", description="读数偏离", value="51.00"),
        record("duplicate-b", 5, tag="DUP-A", description="读数偏离", value="51.00"),
        record("chatter-a", 100, tag="CHAT-A", state="ACTIVE", value="51.00"),
        record("chatter-b", 105, tag="CHAT-A", state="RETURNED", return_after=20, value="52.00"),
        record("chatter-c", 110, tag="CHAT-A", state="ACTIVE", value="53.00"),
        record("chatter-d", 115, tag="CHAT-A", state="RETURNED", return_after=20, value="54.00"),
        record("short", 200, state="RETURNED", return_after=4),
        record("persistent", 300, priority="P1", state="ACTIVE", ack_after=60),
        record("normal", 400),
    ]

    indexed = by_id(analyze(records))

    for name in ("duplicate-a", "duplicate-b"):
        item = indexed[identity(name)]
        assert item["noise_type"] == "DUPLICATE"
        assert item["score"] == pytest.approx(exp(-5 / 30), abs=1e-6)
        assert any("EXPERT_DUPLICATE_V2" in line and "exp(" in line for line in item["evidence"])
    for name in ("chatter-a", "chatter-b", "chatter-c", "chatter-d"):
        item = indexed[identity(name)]
        assert item["noise_type"] == "CHATTER"
        assert item["score"] == 1.0
        assert any("A=T/(N-1)=1.000000" in line for line in item["evidence"])
    short = indexed[identity("short")]
    assert short["noise_type"] == "SHORT_LIVED"
    assert short["score"] == pytest.approx(exp(-4 / 10), abs=1e-6)
    assert any("EXPERT_SHORT_LIVED_V2" in line for line in short["evidence"])
    persistent = indexed[identity("persistent")]
    assert persistent["noise_type"] == "PERSISTENT"
    assert persistent["score"] == 1.0
    assert any("确认条件满足(True)" in line for line in persistent["evidence"])
    normal = indexed[identity("normal")]
    assert normal["noise_type"] == "NORMAL"
    assert normal["score"] == 1.0
    assert any("不是正常概率" in line for line in normal["evidence"])


def test_duplicate_relation_scope_and_chatter_binary_state_are_conservative() -> None:
    records = [
        record("scope-a", 0, tag="SAME", description="相同", unit="UNIT-A"),
        record("scope-b", 5, tag="SAME", description="相同", unit="UNIT-B"),
        record("ack-a", 100, tag="ACK", state="ACTIVE", value="51"),
        record("ack-b", 105, tag="ACK", state="ACKNOWLEDGED", value="52"),
        record("ack-c", 110, tag="ACK", state="ACTIVE", value="53"),
        record("ack-d", 115, tag="ACK", state="ACKNOWLEDGED", value="54"),
    ]

    indexed = by_id(analyze(records))

    assert indexed[identity("scope-a")]["noise_type"] == "NORMAL"
    assert indexed[identity("scope-b")]["noise_type"] == "NORMAL"
    assert {indexed[identity(f"ack-{name}")]["noise_type"] for name in "abcd"} == {"NORMAL"}


def test_chatter_finds_an_interior_qualifying_window_with_a_quiet_tail() -> None:
    states = (
        "ACTIVE",
        "ACTIVE",
        "ACTIVE",
        "RETURNED",
        "ACTIVE",
        "RETURNED",
        "ACTIVE",
    )
    records = [
        record(
            f"interior-{index}",
            index * 5,
            tag="INTERIOR",
            state=state,
            return_after=20 if state == "RETURNED" else None,
            value=str(50 + index),
        )
        for index, state in enumerate(states)
    ]

    indexed = by_id(analyze(records, run="interior-chatter"))

    assert indexed[identity("interior-0")]["noise_type"] == "NORMAL"
    assert {
        indexed[identity(f"interior-{index}")]["noise_type"]
        for index in range(1, 7)
    } == {"CHATTER"}
    assert indexed[identity("interior-6")]["score"] == 0.8
    assert any(
        "A=T/(N-1)=0.800000" in line
        for line in indexed[identity("interior-6")]["evidence"]
    )


def test_chatter_evidence_size_is_linear_for_a_large_same_time_group() -> None:
    records = [
        record(
            f"dense-{index}",
            0,
            tag="DENSE",
            state="ACTIVE" if index % 2 == 0 else "RETURNED",
            return_after=20 if index % 2 else None,
            value=str(index),
            source_row=index + 2,
        )
        for index in range(500)
    ]

    result = analyze(records, run="dense-chatter")
    chatter_evidence = [
        line
        for item in result["record_results"]
        for line in item["evidence"]
        if line.startswith("EXPERT_CHATTER_V2")
    ]

    assert len(chatter_evidence) == 500
    assert max(map(len, chatter_evidence)) < 400
    assert sum(map(len, chatter_evidence)) < 200_000


@pytest.mark.parametrize(
    ("name", "description", "expected_category"),
    [
        ("process", "反应器出现工艺扰动", "PROCESS_DISTURBANCE"),
        ("equipment", "循环泵发生跳停故障", "EQUIPMENT_FAULT"),
        ("instrument", "压力变送器出现漂移", "INSTRUMENT_ISSUE"),
        ("maintenance", "仪表回路正在维护校验", "MAINTENANCE_TEST"),
        ("unknown", "一般运行状态变化", "UNKNOWN"),
    ],
)
def test_weighted_expert_classifier_explains_or_abstains(
    name: str,
    description: str,
    expected_category: str,
) -> None:
    item = analyze([record(name, 0, tag="POINT-X", description=description)])["record_results"][0]

    assert item["cause_category"] == expected_category
    cause_evidence = next(line for line in item["evidence"] if line.startswith("EXPERT_CAUSE_V2"))
    assert "类别分数[" in cause_evidence
    assert "margin=" in cause_evidence
    if expected_category == "UNKNOWN":
        assert "保留 UNKNOWN" in cause_evidence
    else:
        assert "贡献特征[" in cause_evidence
        assert "不代表已确认根因" in cause_evidence


def test_conflicting_expert_features_abstain() -> None:
    parameters = {**PARAMETERS, "expert_min_margin": 0.3}
    item = analyze(
        [record("conflict", 0, tag="POINT-X", description="工艺扰动导致压缩机跳停故障")],
        parameters=parameters,
    )["record_results"][0]

    assert item["cause_category"] == "UNKNOWN"
    assert any("证据不足或冲突" in line for line in item["evidence"])


def test_markov_discovers_unseen_repeated_sequences_without_text_steps() -> None:
    records = sequence_records(0, 0) + sequence_records(1, 120) + sequence_records(2, 240)

    result = analyze(records, run="markov")

    assert result["summary"]["event_chain_count"] == 3
    for chain in result["event_chains"]:
        assert chain["association_rule"] == "MARKOV_TRANSITION_HYBRID_V2"
        assert len(chain["member_record_ids"]) == 5
        assert "C=3,E=3,P(v|u)=1.000000" in chain["explanation"]
        assert "P(v)=0.250000" in chain["explanation"]
        assert "lift=4.000000" in chain["explanation"]
        assert "不代表已确认根因" in chain["explanation"]
    assert all(
        any("成员源行[" in line for line in item["evidence"])
        for item in result["record_results"]
    )
    assert {item["cause_category"] for item in result["record_results"]} == {"UNKNOWN"}


def test_markov_rejects_insufficient_support_random_order_and_cross_scope() -> None:
    one_episode = sequence_records(0, 0)
    assert analyze(one_episode, run="one")["event_chains"] == []

    permutations = (
        ("A", "B", "C", "D", "E"),
        ("B", "D", "A", "E", "C"),
        ("C", "A", "D", "B", "E"),
    )
    random_order = sum(
        (sequence_records(index, index * 120, tags=tags) for index, tags in enumerate(permutations)),
        [],
    )
    assert analyze(random_order, run="random-negative")["event_chains"] == []

    cross_scope = sum(
        (
            sequence_records(
                index,
                0,
                tags=("X-A", "X-B", "X-C", "X-D", "X-E"),
                unit=f"UNIT-{index}",
            )
            for index in range(3)
        ),
        [],
    )
    assert analyze(cross_scope, run="cross-scope")["event_chains"] == []


def test_markov_rejects_transition_lag_outside_window() -> None:
    records = (
        sequence_records(0, 0, spacing=20)
        + sequence_records(1, 200, spacing=20)
        + sequence_records(2, 400, spacing=20)
    )
    parameters = {**PARAMETERS, "chain_window_seconds": 10}

    result = analyze(records, run="lag-negative", parameters=parameters)

    assert result["event_chains"] == []


def test_order_time_shift_and_uuid_remap_preserve_normalized_result() -> None:
    records = sequence_records(0, 0) + sequence_records(1, 120) + sequence_records(2, 240)
    shuffled = list(records)
    random.Random(20260901).shuffle(shuffled)

    shifted = deepcopy(shuffled)
    for item in shifted:
        for field in ("event_time", "return_time", "ack_time"):
            if item[field] is not None:
                item[field] = (datetime.fromisoformat(item[field]) + timedelta(days=37)).isoformat()
        item["record_id"] = str(uuid5(UUID(item["record_id"]), "remapped"))

    first = analyze(records, run="invariance")
    second = analyze(shifted, run="invariance-shifted")

    assert normalized(first, records) == normalized(second, shifted)


def test_pure_function_is_deterministic_and_does_not_mutate_request() -> None:
    request = AnalysisRequest.model_validate(
        payload(
            [
                record("pure-a", 0, tag="PURE", description="读数偏离", value="55.00"),
                record("pure-b", 5, tag="PURE", description="读数偏离", value="55.00"),
            ],
            run="pure",
        )
    )
    original = request.model_dump(mode="json")

    first = analyze_rules(request).model_dump(mode="json")
    second = analyze_rules(request).model_dump(mode="json")

    assert first == second
    assert request.model_dump(mode="json") == original


def test_empty_input_is_a_successful_deterministic_noop() -> None:
    result = analyze([], run="empty")

    assert result["record_results"] == []
    assert result["event_chains"] == []
    assert result["errors"] == []
    assert result["summary"] == {
        "input_count": 0,
        "success_count": 0,
        "failure_count": 0,
        "noise_type_counts": {
            kind: 0 for kind in ("NORMAL", "DUPLICATE", "CHATTER", "SHORT_LIVED", "PERSISTENT")
        },
        "cause_category_counts": {
            kind: 0
            for kind in (
                "PROCESS_DISTURBANCE",
                "EQUIPMENT_FAULT",
                "INSTRUMENT_ISSUE",
                "MAINTENANCE_TEST",
                "UNKNOWN",
            )
        },
        "event_chain_count": 0,
    }


@pytest.mark.parametrize(
    "mutation",
    [
        lambda request: request.update({"contract_version": "v1"}),
        lambda request: request.update({"algorithm_version": "9.9.9"}),
        lambda request: request["parameters"].pop("min_lift"),
        lambda request: request["parameters"].update({"chatter_min_transition_ratio": 1.1}),
        lambda request: request["parameters"].update({"min_episode_support": 1}),
        lambda request: request["records"][0].update({"priority": "P0"}),
        lambda request: request["records"][0].update({"event_time": "2031-04-08T09:00:00"}),
        lambda request: request.update({"unknown": True}),
    ],
)
def test_invalid_v2_request_is_rejected(mutation: Any) -> None:
    request = payload([record("invalid", 0)])
    mutation(request)

    response = client.post("/api/v2/analyze", json=request)

    assert response.status_code == 422


def test_duplicate_ids_and_cross_batch_input_are_rejected() -> None:
    duplicate = record("same", 0)
    repeated = {**duplicate, "source_row": 3}
    cross_batch = record("other-batch", 10)
    cross_batch["batch_id"] = identity("batch-2")

    duplicate_response = client.post("/api/v2/analyze", json=payload([duplicate, repeated]))
    cross_batch_response = client.post("/api/v2/analyze", json=payload([duplicate, cross_batch]))

    assert duplicate_response.status_code == 422
    assert "record_id 不得重复" in duplicate_response.text
    assert cross_batch_response.status_code == 422
    assert "一次分析只能包含一个导入批次" in cross_batch_response.text


def test_runtime_has_no_demo_or_expected_result_dependency() -> None:
    package = Path(__file__).resolve().parents[1] / "algorithm_service"
    runtime_text = "\n".join(path.read_text("utf-8") for path in sorted(package.glob("*.py"))).lower()

    for forbidden in ("synthetic", "samples/expected", "步骤 1..5", "equipment_trip", "process_cascade"):
        assert forbidden not in runtime_text
    assert "postgresql" not in runtime_text
