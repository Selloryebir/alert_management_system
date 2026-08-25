from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_DNS, uuid5

import pytest
from fastapi.testclient import TestClient

from algorithm_service.app import app
from algorithm_service.models import AnalysisRequest
from algorithm_service.rules import analyze as analyze_rules


client = TestClient(app)
BASE_TIME = datetime(2026, 1, 15, 8, 0, tzinfo=timezone(timedelta(hours=8)))
PARAMETERS = {
    "duplicate_window_seconds": 30,
    "chatter_window_seconds": 60,
    "chatter_min_count": 4,
    "short_lived_seconds": 10,
    "persistent_requires_ack": True,
    "chain_window_seconds": 60,
    "chain_min_steps": 5,
}
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def identity(name: str) -> str:
    return str(uuid5(NAMESPACE_DNS, f"alert-management-test:{name}"))


def record(
    name: str,
    seconds: int,
    *,
    tag: str | None = None,
    description: str = "[SYNTHETIC] 普通报警",
    priority: str = "P3",
    state: str = "ACTIVE",
    return_after: int | None = None,
    ack_after: int | None = None,
    value: str = "50.00",
) -> dict[str, Any]:
    event_time = BASE_TIME + timedelta(seconds=seconds)
    return {
        "record_id": identity(name),
        "batch_id": identity("batch"),
        "source_row": seconds + 2,
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
        "site": "SYNTHETIC_SITE_01",
        "area": "SYNTHETIC_AREA_01",
        "unit": "SYNTHETIC_UNIT_01",
        "tag": tag or f"SYNTHETIC-{name}",
        "description": description,
        "priority": priority,
        "state": state,
        "value": value,
        "threshold": "75.00",
        "engineering_unit": "SYNTHETIC_UNIT_VALUE",
        "source_system": "SYNTHETIC_DCS",
        "operator": "SYNTHETIC_OPERATOR_01",
        "raw_payload": {"source_row": str(seconds + 2), "tag": tag or name},
    }


def payload(records: list[dict[str, Any]], *, run: str = "run") -> dict[str, Any]:
    return {
        "analysis_run_id": identity(run),
        "contract_version": "v1",
        "algorithm_version": "0.1.0",
        "parameters": dict(PARAMETERS),
        "records": records,
    }


def analyze(records: list[dict[str, Any]], *, run: str = "run") -> dict[str, Any]:
    response = client.post("/api/v1/analyze", json=payload(records, run=run))
    assert response.status_code == 200, response.text
    return response.json()


def by_id(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["record_id"]: item for item in result["record_results"]}


def test_all_noise_types_and_alarm_classes_are_explainable() -> None:
    records = [
        record("duplicate-original", 0, tag="DUP", description="重复场景", value="51.00"),
        record("duplicate-copy", 5, tag="DUP", description="重复场景", value="51.00"),
        record("chatter-1", 100, tag="CHATTER", state="ACTIVE"),
        record("chatter-2", 105, tag="CHATTER", state="RETURNED", return_after=20),
        record("chatter-3", 110, tag="CHATTER", state="ACTIVE", value="51.00"),
        record("chatter-4", 115, tag="CHATTER", state="RETURNED", return_after=20, value="51.00"),
        record("short", 200, state="RETURNED", return_after=4),
        record("persistent", 300, priority="P1", state="ACTIVE", ack_after=60),
        record("normal", 400),
    ]

    result = analyze(records)
    indexed = by_id(result)

    assert indexed[identity("duplicate-original")]["noise_type"] == "DUPLICATE"
    assert indexed[identity("duplicate-copy")]["noise_type"] == "DUPLICATE"
    assert {indexed[identity(f"chatter-{index}")]["noise_type"] for index in range(1, 5)} == {"CHATTER"}
    assert indexed[identity("short")]["noise_type"] == "SHORT_LIVED"
    assert indexed[identity("persistent")]["noise_type"] == "PERSISTENT"
    assert indexed[identity("normal")]["noise_type"] == "NORMAL"
    assert indexed[identity("duplicate-copy")]["alarm_class"] == "NUISANCE"
    assert indexed[identity("persistent")]["alarm_class"] == "ACTIONABLE"
    assert indexed[identity("normal")]["alarm_class"] == "STANDARD"
    assert all(item["evidence"] for item in result["record_results"])
    assert result["summary"]["input_count"] == 9
    assert result["summary"]["success_count"] == 9
    assert result["summary"]["failure_count"] == 0


def test_noise_priority_is_duplicate_then_chatter_then_short_lived() -> None:
    records = [
        record("priority-1", 0, tag="PRIORITY", state="RETURNED", return_after=4),
        record("priority-2", 5, tag="PRIORITY", state="ACTIVE", value="51.00"),
        record("priority-3", 10, tag="PRIORITY", state="RETURNED", return_after=4, value="52.00"),
        record("priority-4", 15, tag="PRIORITY", state="ACTIVE", value="53.00"),
        record("priority-5", 20, tag="PRIORITY", state="RETURNED", return_after=4, value="52.00"),
    ]

    result = analyze(records)
    target = by_id(result)[identity("priority-5")]

    assert target["noise_type"] == "DUPLICATE"
    assert any("高频" in item for item in target["evidence"])
    assert any("恢复" in item for item in target["evidence"])
    assert any("重复" in item for item in target["evidence"])


@pytest.mark.parametrize(
    ("name", "description", "expected"),
    [
        ("process", "[SYNTHETIC] 工艺扰动报警", "PROCESS_DISTURBANCE"),
        ("equipment", "[SYNTHETIC] 压缩机故障报警", "EQUIPMENT_FAULT"),
        ("instrument", "[SYNTHETIC] 仪表漂移报警", "INSTRUMENT_ISSUE"),
        ("maintenance", "[SYNTHETIC] 维护测试报警", "MAINTENANCE_TEST"),
        ("unknown", "[SYNTHETIC] 未分类报警", "UNKNOWN"),
    ],
)
def test_cause_category_is_a_text_based_suggestion(name: str, description: str, expected: str) -> None:
    result = analyze([record(name, 0, description=description)])
    item = result["record_results"][0]

    assert item["cause_category"] == expected
    assert "根因" in item["evidence"][-1] or expected == "UNKNOWN"


def test_equipment_and_process_sequences_form_deterministic_chains() -> None:
    records: list[dict[str, Any]] = []
    for offset, (prefix, chinese) in enumerate(
        (("EQUIPMENT_TRIP", "设备跳停序列"), ("PROCESS_CASCADE", "工艺扰动级联"))
    ):
        for step in range(1, 6):
            records.append(
                record(
                    f"{prefix}-{step}",
                    offset * 200 + (step - 1) * 11,
                    tag=f"SYNTHETIC-{prefix}-{step:03d}",
                    description=f"[SYNTHETIC] {chinese}步骤 {step}",
                )
            )

    first = analyze(list(reversed(records)), run="chain-run")
    second = analyze(list(reversed(records)), run="chain-run")

    assert first == second
    assert first["summary"]["event_chain_count"] == 2
    assert [chain["association_rule"] for chain in first["event_chains"]] == [
        "EQUIPMENT_TRIP_SEQUENCE",
        "PROCESS_CASCADE_SEQUENCE",
    ]
    for chain in first["event_chains"]:
        assert len(chain["member_record_ids"]) == 5
        assert chain["start_record_id"] == chain["member_record_ids"][0]
        assert "关系键为 SYNTHETIC_SITE_01/SYNTHETIC_AREA_01/SYNTHETIC_UNIT_01" in chain["explanation"]
        assert "不代表已确认根因" in chain["explanation"]
        assert datetime.fromisoformat(chain["start_time"]) < datetime.fromisoformat(chain["end_time"])


def test_sequence_steps_from_different_units_do_not_form_a_chain() -> None:
    records = []
    for step in range(1, 6):
        item = record(
            f"cross-unit-{step}",
            (step - 1) * 10,
            tag=f"SYNTHETIC-EQUIPMENT_TRIP-{step:03d}",
            description=f"[SYNTHETIC] 设备跳停序列步骤 {step}",
        )
        item["unit"] = f"SYNTHETIC_UNIT_{step:02d}"
        records.append(item)

    result = analyze(records, run="cross-unit-chain")

    assert result["event_chains"] == []
    assert result["summary"]["event_chain_count"] == 0


def test_response_echoes_versions_run_id_parameters_and_has_stable_counts() -> None:
    request = payload([record("identity", 0)], run="identity-run")
    response = client.post("/api/v1/analyze", json=request)

    assert response.status_code == 200
    result = response.json()
    assert result["analysis_run_id"] == request["analysis_run_id"]
    assert result["contract_version"] == "v1"
    assert result["algorithm_version"] == "0.1.0"
    assert result["rule_version"] == "rules-v1.0.0"
    assert result["parameters"] == PARAMETERS
    assert result["summary"]["noise_type_counts"] == {
        "NORMAL": 1,
        "DUPLICATE": 0,
        "CHATTER": 0,
        "SHORT_LIVED": 0,
        "PERSISTENT": 0,
    }
    assert sum(result["summary"]["cause_category_counts"].values()) == 1
    assert result["errors"] == []


def test_pure_rule_function_is_deterministic_and_does_not_mutate_request() -> None:
    request = AnalysisRequest.model_validate(
        payload(
            [
                record("pure-original", 0, tag="PURE", value="55.00"),
                record("pure-copy", 5, tag="PURE", value="55.00"),
            ],
            run="pure-run",
        )
    )
    original = request.model_dump(mode="json")

    first = analyze_rules(request).model_dump(mode="json")
    second = analyze_rules(request).model_dump(mode="json")

    assert first == second
    assert request.model_dump(mode="json") == original
    assert [item["noise_type"] for item in first["record_results"]] == ["DUPLICATE", "DUPLICATE"]


def test_empty_input_is_a_successful_deterministic_noop() -> None:
    result = analyze([], run="empty")

    assert result["record_results"] == []
    assert result["event_chains"] == []
    assert result["errors"] == []
    assert result["summary"] == {
        "input_count": 0,
        "success_count": 0,
        "failure_count": 0,
        "noise_type_counts": {kind: 0 for kind in ("NORMAL", "DUPLICATE", "CHATTER", "SHORT_LIVED", "PERSISTENT")},
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


def test_committed_smoke_matches_independent_golden_result_exactly() -> None:
    smoke_path = REPOSITORY_ROOT / "samples" / "smoke" / "synthetic_smoke_utf8.csv"
    expected_path = REPOSITORY_ROOT / "samples" / "expected" / "analysis-smoke-expected.json"
    expected = json.loads(expected_path.read_text("utf-8"))
    records: list[dict[str, Any]] = []
    record_rows: dict[str, int] = {}
    with smoke_path.open(encoding="utf-8-sig", newline="") as handle:
        for raw in csv.DictReader(handle):
            source_row = int(raw["source_row"])
            item = {
                "record_id": identity(f"smoke-{source_row}"),
                "batch_id": identity("smoke-batch"),
                "source_row": source_row,
                "event_time": raw["event_time"],
                "return_time": raw["return_time"] or None,
                "ack_time": raw["ack_time"] or None,
                "site": raw["site"],
                "area": raw["area"],
                "unit": raw["unit"] or None,
                "tag": raw["tag"],
                "description": raw["description"],
                "priority": raw["priority"],
                "state": raw["state"],
                "value": raw["value"] or None,
                "threshold": raw["threshold"] or None,
                "engineering_unit": raw["engineering_unit"] or None,
                "source_system": raw["source_system"],
                "operator": raw["operator"] or None,
                "raw_payload": raw,
            }
            records.append(item)
            record_rows[item["record_id"]] = source_row

    result = analyze(records, run="committed-smoke")
    actual_records = [
        {
            "source_row": record_rows[item["record_id"]],
            "noise_type": item["noise_type"],
            "alarm_class": item["alarm_class"],
            "cause_category": item["cause_category"],
        }
        for item in result["record_results"]
    ]
    actual_chains = [
        {
            "member_source_rows": [record_rows[record_id] for record_id in chain["member_record_ids"]],
            "start_time": chain["start_time"],
            "end_time": chain["end_time"],
            "association_rule_category": chain["association_rule"],
        }
        for chain in result["event_chains"]
    ]

    assert actual_records == expected["records"]
    assert actual_chains == [
        {
            "member_source_rows": chain["member_source_rows"],
            "start_time": chain["start_time"],
            "end_time": chain["end_time"],
            "association_rule_category": chain["association_rule_category"],
        }
        for chain in expected["event_chains"]
    ]
    assert result["summary"]["noise_type_counts"] == expected["summary"]["noise_type_counts"]
    assert result["summary"]["cause_category_counts"] == expected["summary"]["cause_category_counts"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda request: request.update({"contract_version": "v2"}),
        lambda request: request.update({"algorithm_version": "9.9.9"}),
        lambda request: request["parameters"].pop("chain_min_steps"),
        lambda request: request["records"][0].update({"priority": "P0"}),
        lambda request: request["records"][0].update({"event_time": "2026-01-15T08:00:00"}),
        lambda request: request.update({"unknown": True}),
    ],
)
def test_invalid_v1_request_is_rejected(mutation: Any) -> None:
    request = payload([record("invalid", 0)])
    mutation(request)

    response = client.post("/api/v1/analyze", json=request)

    assert response.status_code == 422


def test_duplicate_record_ids_and_cross_batch_input_are_rejected() -> None:
    duplicate = record("same", 0)
    repeated = {**duplicate, "source_row": 3}
    cross_batch = record("other-batch", 10)
    cross_batch["batch_id"] = identity("batch-2")

    duplicate_response = client.post("/api/v1/analyze", json=payload([duplicate, repeated]))
    cross_batch_response = client.post("/api/v1/analyze", json=payload([duplicate, cross_batch]))

    assert duplicate_response.status_code == 422
    assert "record_id 不得重复" in duplicate_response.text
    assert cross_batch_response.status_code == 422
    assert "一次分析只能包含一个导入批次" in cross_batch_response.text
