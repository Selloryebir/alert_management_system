#!/usr/bin/env python3
"""基于冻结的 Smoke 场景声明构建 hybrid-v2 独立黄金预期。

本脚本不导入、不调用算法服务，也不读取算法输出。分类答案只来自本文件内
冻结的 source_row 场景区间和数据契约枚举；输入文件仅提供原始时间戳用于
精确记录事件链边界。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path


RULE_VERSION = "hybrid-v2.0.0"
ASSOCIATION_RULE = "MARKOV_TRANSITION_HYBRID_V2"
INPUT_SHA256 = "f8a2b4dcb5a6629839330689681867ee37d82fdc752266ca610bf6ddbf43b8a2"
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

SCENARIOS = (
    ("ALARM_FLOOD", 2, 61),
    ("DUPLICATE", 62, 91),
    ("CHATTER", 92, 131),
    ("SHORT_LIVED", 132, 161),
    ("PERSISTENT", 162, 191),
    ("INSTRUMENT_DRIFT", 192, 221),
    ("EQUIPMENT_TRIP", 222, 251),
    ("PROCESS_CASCADE", 252, 281),
    ("MAINTENANCE_TEST", 282, 301),
)

NOISE_BY_SCENARIO = {
    "DUPLICATE": "DUPLICATE",
    "CHATTER": "CHATTER",
    "SHORT_LIVED": "SHORT_LIVED",
    "PERSISTENT": "PERSISTENT",
}
CAUSE_BY_SCENARIO = {
    "PROCESS_CASCADE": "PROCESS_DISTURBANCE",
    "EQUIPMENT_TRIP": "EQUIPMENT_FAULT",
    "INSTRUMENT_DRIFT": "INSTRUMENT_ISSUE",
    "MAINTENANCE_TEST": "MAINTENANCE_TEST",
}
EXPECTED_NOISE_COUNTS = {
    "NORMAL": 170,
    "DUPLICATE": 30,
    "CHATTER": 40,
    "SHORT_LIVED": 30,
    "PERSISTENT": 30,
}
EXPECTED_ALARM_CLASS_COUNTS = {
    "STANDARD": 170,
    "NUISANCE": 100,
    "ACTIONABLE": 30,
}
EXPECTED_CAUSE_COUNTS = {
    "PROCESS_DISTURBANCE": 30,
    "EQUIPMENT_FAULT": 30,
    "INSTRUMENT_ISSUE": 30,
    "MAINTENANCE_TEST": 20,
    "UNKNOWN": 190,
}

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = REPO_ROOT / "samples" / "smoke" / "synthetic_smoke_utf8.csv"
DEFAULT_OUTPUT = Path(__file__).with_name("analysis-smoke-expected.json")


def scenario_for(source_row: int) -> str:
    for scenario, first, last in SCENARIOS:
        if first <= source_row <= last:
            return scenario
    raise ValueError(f"source_row 不在冻结 Smoke 场景范围内：{source_row}")


def expected_record(source_row: int) -> dict[str, object]:
    scenario = scenario_for(source_row)
    noise_type = NOISE_BY_SCENARIO.get(scenario, "NORMAL")
    if noise_type in {"DUPLICATE", "CHATTER", "SHORT_LIVED"}:
        alarm_class = "NUISANCE"
    elif noise_type == "PERSISTENT":
        alarm_class = "ACTIONABLE"
    else:
        alarm_class = "STANDARD"
    return {
        "source_row": source_row,
        "noise_type": noise_type,
        "alarm_class": alarm_class,
        "cause_category": CAUSE_BY_SCENARIO.get(scenario, "UNKNOWN"),
    }


def load_input(path: Path) -> list[dict[str, str]]:
    input_bytes = path.read_bytes()
    digest = hashlib.sha256(input_bytes).hexdigest()
    if digest != INPUT_SHA256:
        raise ValueError(f"Smoke 输入 SHA256 已变化：{digest}")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    source_rows = [int(row["source_row"]) for row in rows]
    if source_rows != list(range(2, 302)):
        raise ValueError("Smoke source_row 必须连续覆盖 2..301")
    for row in rows:
        scenario = scenario_for(int(row["source_row"]))
        if not row["tag"].startswith(f"SYNTHETIC-{scenario}-"):
            raise ValueError(f"场景区间与标签不一致：source_row={row['source_row']}")
    return rows


def build_chains(rows_by_source: dict[int, dict[str, str]]) -> list[dict[str, object]]:
    chains = []
    definitions = (
        ("EQUIPMENT_TRIP", 222, ASSOCIATION_RULE),
        ("PROCESS_CASCADE", 252, ASSOCIATION_RULE),
    )
    for category, first_source_row, rule_category in definitions:
        for chain_index in range(6):
            first = first_source_row + chain_index * 5
            members = list(range(first, first + 5))
            chains.append(
                {
                    "chain_id": f"SYNTHETIC_CHAIN_{category}_{chain_index + 1:02d}",
                    "scenario": category,
                    "member_source_rows": members,
                    "start_time": rows_by_source[members[0]]["event_time"],
                    "end_time": rows_by_source[members[-1]]["event_time"],
                    "association_rule_category": rule_category,
                }
            )
    return chains


def build_document(input_path: Path = DEFAULT_INPUT) -> dict[str, object]:
    rows = load_input(input_path)
    rows_by_source = {int(row["source_row"]): row for row in rows}
    records = [expected_record(source_row) for source_row in range(2, 302)]
    chains = build_chains(rows_by_source)

    noise_counts = Counter(record["noise_type"] for record in records)
    alarm_class_counts = Counter(record["alarm_class"] for record in records)
    cause_counts = Counter(record["cause_category"] for record in records)
    if dict(noise_counts) != EXPECTED_NOISE_COUNTS:
        raise AssertionError(f"噪声计数错误：{dict(noise_counts)}")
    if dict(alarm_class_counts) != EXPECTED_ALARM_CLASS_COUNTS:
        raise AssertionError(f"报警分类计数错误：{dict(alarm_class_counts)}")
    if dict(cause_counts) != EXPECTED_CAUSE_COUNTS:
        raise AssertionError(f"原因类别计数错误：{dict(cause_counts)}")

    return {
        "schema_version": 1,
        "synthetic": True,
        "oracle": "independent-smoke-scenario-contract",
        "input": {
            "path": "samples/smoke/synthetic_smoke_utf8.csv",
            "sha256": INPUT_SHA256,
            "generator_version": "2.0.0",
            "seed": 20260825,
            "row_count": 300,
        },
        "rule_version": RULE_VERSION,
        "parameters": PARAMETERS,
        "summary": {
            "input_count": 300,
            "noise_type_counts": EXPECTED_NOISE_COUNTS,
            "alarm_class_counts": EXPECTED_ALARM_CLASS_COUNTS,
            "cause_category_counts": EXPECTED_CAUSE_COUNTS,
            "event_chain_counts": {
                ASSOCIATION_RULE: 12,
                "total": 12,
            },
        },
        "records": records,
        "event_chains": chains,
    }


def write_expected(output_path: Path, input_path: Path = DEFAULT_INPUT) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(build_document(input_path), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    write_expected(args.output.resolve(), args.input.resolve())


if __name__ == "__main__":
    main()
