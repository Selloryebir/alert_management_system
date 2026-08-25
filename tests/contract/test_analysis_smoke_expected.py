from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SMOKE_PATH = ROOT / "samples" / "smoke" / "synthetic_smoke_utf8.csv"
EXPECTED_PATH = ROOT / "samples" / "expected" / "analysis-smoke-expected.json"
BUILDER_PATH = ROOT / "samples" / "expected" / "build_analysis_smoke_expected.py"
SMOKE_SHA256 = "f8a2b4dcb5a6629839330689681867ee37d82fdc752266ca610bf6ddbf43b8a2"
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


def load_builder():
    specification = importlib.util.spec_from_file_location("analysis_smoke_oracle", BUILDER_PATH)
    assert specification and specification.loader
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def read_smoke() -> list[dict[str, str]]:
    with SMOKE_PATH.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def seconds_between(first: str, second: str) -> float:
    return (datetime.fromisoformat(second) - datetime.fromisoformat(first)).total_seconds()


def scenario_rows(rows: list[dict[str, str]], first: int, last: int) -> list[dict[str, str]]:
    return [row for row in rows if first <= int(row["source_row"]) <= last]


def test_smoke_satisfies_frozen_rule_preconditions() -> None:
    assert hashlib.sha256(SMOKE_PATH.read_bytes()).hexdigest() == SMOKE_SHA256
    rows = read_smoke()
    assert [int(row["source_row"]) for row in rows] == list(range(2, 302))
    for scenario, first, last in SCENARIOS:
        selected = scenario_rows(rows, first, last)
        assert len(selected) == last - first + 1
        assert all(row["tag"].startswith(f"SYNTHETIC-{scenario}-") for row in selected)

    duplicates = scenario_rows(rows, 62, 91)
    for first, second in zip(duplicates[::2], duplicates[1::2], strict=True):
        assert {key: value for key, value in first.items() if key not in {"source_row", "event_time"}} == {
            key: value for key, value in second.items() if key not in {"source_row", "event_time"}
        }
        assert 0 < seconds_between(first["event_time"], second["event_time"]) <= PARAMETERS[
            "duplicate_window_seconds"
        ]

    chatter_by_tag: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in scenario_rows(rows, 92, 131):
        chatter_by_tag[row["tag"]].append(row)
    assert len(chatter_by_tag) == 4
    for chatter_rows in chatter_by_tag.values():
        assert len(chatter_rows) == 10
        assert len(chatter_rows) >= PARAMETERS["chatter_min_count"]
        assert seconds_between(chatter_rows[0]["event_time"], chatter_rows[-1]["event_time"]) <= PARAMETERS[
            "chatter_window_seconds"
        ]
        assert [row["state"] for row in chatter_rows] == ["ACTIVE", "RETURNED"] * 5
        assert len({(row["site"], row["area"], row["unit"]) for row in chatter_rows}) == 1

    for row in scenario_rows(rows, 132, 161):
        assert row["state"] == "RETURNED"
        assert 0 <= seconds_between(row["event_time"], row["return_time"]) <= PARAMETERS[
            "short_lived_seconds"
        ]

    for row in scenario_rows(rows, 162, 191):
        assert row["state"] == "ACTIVE"
        assert row["return_time"] == ""
        assert row["ack_time"] if PARAMETERS["persistent_requires_ack"] else True

    for first in list(range(222, 252, 5)) + list(range(252, 282, 5)):
        chain_rows = scenario_rows(rows, first, first + 4)
        assert len(chain_rows) == PARAMETERS["chain_min_steps"]
        assert len({(row["site"], row["area"], row["unit"]) for row in chain_rows}) == 1
        assert [int(row["tag"].rsplit("-", 1)[1]) for row in chain_rows] == [1, 2, 3, 4, 5]
        assert seconds_between(chain_rows[0]["event_time"], chain_rows[-1]["event_time"]) <= PARAMETERS[
            "chain_window_seconds"
        ]


def test_golden_records_cover_all_rows_with_exact_counts() -> None:
    expected = json.loads(EXPECTED_PATH.read_text("utf-8"))
    assert expected["synthetic"] is True
    assert expected["oracle"] == "independent-smoke-scenario-contract"
    assert expected["input"]["sha256"] == SMOKE_SHA256
    assert expected["rule_version"] == "hybrid-v2.0.0"
    assert expected["parameters"] == PARAMETERS

    records = expected["records"]
    assert len(records) == 300
    assert [record["source_row"] for record in records] == list(range(2, 302))
    assert Counter(record["noise_type"] for record in records) == {
        "NORMAL": 170,
        "DUPLICATE": 30,
        "CHATTER": 40,
        "SHORT_LIVED": 30,
        "PERSISTENT": 30,
    }
    assert Counter(record["alarm_class"] for record in records) == {
        "STANDARD": 170,
        "NUISANCE": 100,
        "ACTIONABLE": 30,
    }
    assert Counter(record["cause_category"] for record in records) == {
        "PROCESS_DISTURBANCE": 30,
        "EQUIPMENT_FAULT": 30,
        "INSTRUMENT_ISSUE": 30,
        "MAINTENANCE_TEST": 20,
        "UNKNOWN": 190,
    }
    assert expected["summary"]["input_count"] == 300


def test_event_chains_are_exact_and_non_overlapping() -> None:
    expected = json.loads(EXPECTED_PATH.read_text("utf-8"))
    rows_by_source = {int(row["source_row"]): row for row in read_smoke()}
    chains = expected["event_chains"]
    assert len(chains) == 12
    assert Counter(chain["association_rule_category"] for chain in chains) == {
        "MARKOV_TRANSITION_HYBRID_V2": 12,
    }

    all_members = [source_row for chain in chains for source_row in chain["member_source_rows"]]
    assert len(all_members) == len(set(all_members)) == 60
    assert set(all_members) == set(range(222, 282))
    for chain in chains:
        members = chain["member_source_rows"]
        assert len(members) == PARAMETERS["chain_min_steps"] == 5
        assert members == list(range(members[0], members[0] + 5))
        assert chain["start_time"] == rows_by_source[members[0]]["event_time"]
        assert chain["end_time"] == rows_by_source[members[-1]]["event_time"]
        assert seconds_between(chain["start_time"], chain["end_time"]) <= PARAMETERS[
            "chain_window_seconds"
        ]


def test_golden_file_is_byte_rebuildable(tmp_path: Path) -> None:
    builder = load_builder()
    rebuilt = tmp_path / "analysis-smoke-expected.json"
    builder.write_expected(rebuilt, SMOKE_PATH)
    assert rebuilt.read_bytes() == EXPECTED_PATH.read_bytes()
