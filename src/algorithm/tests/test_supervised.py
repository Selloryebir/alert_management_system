from __future__ import annotations

from base64 import urlsafe_b64encode
from copy import deepcopy
from difflib import SequenceMatcher
import importlib.util
import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import skops.io as sio

from algorithm_service.models import AnalysisRequest
from algorithm_service.rules import analyze
from algorithm_service import supervised
from algorithm_service.supervised import (
    MODEL_FORMAT,
    MODEL_VERSION,
    ModelConfigurationError,
    SupervisedModel,
    encrypt_model_bytes,
    load_model_from_environment,
)


ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "tools" / "model-training" / "data" / "engineering-scenarios.jsonl"
BOUNDARY_DATA = ROOT / "tools" / "model-training" / "data" / "boundary-scenarios.jsonl"
TRAIN_PATH = ROOT / "tools" / "model-training" / "train.py"


def analysis_request(records: list[Any]) -> AnalysisRequest:
    return AnalysisRequest.model_validate(
        {
            "analysis_run_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "contract_version": "v2",
            "algorithm_version": "0.2.0",
            "parameters": {
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
                "expert_min_margin": 0.10,
            },
            "records": records,
        }
    )


def load_training_module() -> Any:
    spec = importlib.util.spec_from_file_location("model_training", TRAIN_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def training() -> Any:
    return load_training_module()


@pytest.fixture(scope="module")
def rows(training: Any) -> list[dict[str, Any]]:
    return training.load_rows(DATA)


@pytest.fixture(scope="module")
def bundle(training: Any, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return training.train_bundle(rows)


@pytest.fixture(autouse=True)
def restore_configured_model() -> Any:
    previous = supervised._configured_model
    try:
        yield
    finally:
        supervised._configured_model = previous


def test_predefined_groups_are_disjoint_and_metrics_are_deterministic(
    training: Any, rows: list[dict[str, Any]]
) -> None:
    groups = {
        split: {row["group_id"] for row in rows if row["split"] == split}
        for split in training.SPLITS
    }
    assert not groups["train"] & groups["validation"]
    assert not groups["train"] & groups["test"]
    assert not groups["validation"] & groups["test"]

    first = training.train_bundle(rows)
    second = training.train_bundle(rows)
    assert training.evaluate(first, rows, "validation") == training.evaluate(
        second, rows, "validation"
    )
    test_metrics = training.evaluate(first, rows, "test")
    assert test_metrics["count"] == 12
    assert set(test_metrics["classification"]) >= {*supervised.KNOWN_CAUSES, "macro avg"}
    assert 0.0 <= test_metrics["coverage"] <= 1.0
    assert len(test_metrics["confusion_matrix"]) == 5


def test_split_text_is_independent_and_metadata_never_enters_features(
    rows: list[dict[str, Any]], bundle: dict[str, Any]
) -> None:
    by_split = {
        split: [row for row in rows if row["split"] == split]
        for split in ("train", "validation", "test")
    }
    for first, second in (("train", "validation"), ("train", "test"), ("validation", "test")):
        for left in by_split[first]:
            for right in by_split[second]:
                left_text = left["record"].description.casefold().replace(" ", "")
                right_text = right["record"].description.casefold().replace(" ", "")
                assert left_text != right_text
                assert SequenceMatcher(None, left_text, right_text).ratio() < 0.88

    source = rows[0]
    altered_metadata = {
        **source,
        "label": "MAINTENANCE_TEST",
        "split": "test",
        "group_id": "metadata-must-not-be-a-feature",
    }
    assert supervised.text_feature(source["record"]) == supervised.text_feature(
        altered_metadata["record"]
    )
    assert np.array_equal(
        supervised.structural_features(source["record"]),
        supervised.structural_features(altered_metadata["record"]),
    )
    model = SupervisedModel(bundle)
    assert model.decide(source["record"]) == model.decide(altered_metadata["record"])


def test_group_leakage_is_rejected(training: Any, rows: list[dict[str, Any]]) -> None:
    leaked = deepcopy(rows)
    leaked[-1]["group_id"] = next(row["group_id"] for row in leaked if row["split"] == "train")
    with pytest.raises(training.TrainingDataError, match="group_id 泄漏"):
        training.validate_group_isolation(leaked)


def test_training_entry_rejects_cross_split_text_leakage(
    training: Any, rows: list[dict[str, Any]]
) -> None:
    leaked = deepcopy(rows)
    train_row = next(row for row in leaked if row["split"] == "train")
    validation_row = next(row for row in leaked if row["split"] == "validation")
    validation_row["record"] = validation_row["record"].model_copy(
        update={"description": train_row["record"].description}
    )
    with pytest.raises(training.TrainingDataError, match="跨 split 报警描述完全重复"):
        training.validate_group_isolation(leaked)


def test_encrypted_model_loads_and_wrong_key_or_tampering_fails(
    tmp_path: Path, bundle: dict[str, Any]
) -> None:
    key = bytes(range(32))
    serialized = sio.dumps(bundle)
    model_path = tmp_path / "model.enc"
    key_path = tmp_path / "key.txt"
    model_path.write_bytes(encrypt_model_bytes(serialized, key, bytes(range(12))))
    key_path.write_text(urlsafe_b64encode(key).decode("ascii"), encoding="ascii")

    model = SupervisedModel.load(model_path, key_path)
    assert model.thresholds

    key_path.write_text(urlsafe_b64encode(os.urandom(32)).decode("ascii"), encoding="ascii")
    with pytest.raises(ModelConfigurationError, match="密钥错误或文件已被篡改"):
        SupervisedModel.load(model_path, key_path)

    key_path.write_text(urlsafe_b64encode(key).decode("ascii"), encoding="ascii")
    tampered = bytearray(model_path.read_bytes())
    tampered[-1] ^= 1
    model_path.write_bytes(tampered)
    with pytest.raises(ModelConfigurationError, match="密钥错误或文件已被篡改"):
        SupervisedModel.load(model_path, key_path)


def test_missing_key_and_model_version_mismatch_fail_closed(
    tmp_path: Path, bundle: dict[str, Any]
) -> None:
    with pytest.raises(ModelConfigurationError, match="必须同时设置"):
        load_model_from_environment({"ALGORITHM_MODEL_FILE": str(tmp_path / "model.enc")})

    wrong = dict(bundle)
    wrong["model_version"] = "wrong-version"
    key = bytes(range(32))
    model_path = tmp_path / "wrong.enc"
    key_path = tmp_path / "key.txt"
    model_path.write_bytes(encrypt_model_bytes(sio.dumps(wrong), key, bytes(range(12))))
    key_path.write_text(urlsafe_b64encode(key).decode("ascii"), encoding="ascii")
    with pytest.raises(ModelConfigurationError, match="版本或格式不匹配"):
        SupervisedModel.load(model_path, key_path)

    missing = dict(bundle)
    missing.pop("thresholds")
    missing_path = tmp_path / "missing.enc"
    missing_path.write_bytes(encrypt_model_bytes(sio.dumps(missing), key, bytes(range(12))))
    with pytest.raises(ModelConfigurationError, match="必要字段"):
        SupervisedModel.load(missing_path, key_path)

    malformed = dict(bundle)
    malformed["classes"] = 7
    with pytest.raises(ModelConfigurationError, match="类别顺序"):
        SupervisedModel(malformed)


def test_untrusted_skops_type_is_rejected() -> None:
    serialized = sio.dumps({"unexpected": Path("untrusted")})
    with pytest.raises(ModelConfigurationError, match="未允许的序列化类型"):
        supervised._trusted_types(serialized)


def test_unseen_scenarios_are_classified_or_conservatively_abstained(
    rows: list[dict[str, Any]], bundle: dict[str, Any]
) -> None:
    model = SupervisedModel(bundle)
    test_rows = [row for row in rows if row["split"] == "test"]
    decisions = [model.decide(row["record"]) for row in test_rows]
    assert any(decision.accepted for decision in decisions)
    assert all("不是概率" in decision.evidence for decision in decisions)

    ambiguous = test_rows[0]["record"].model_copy(
        update={
            "tag": "NEUTRAL-X",
            "description": "一般运行状态变化",
            "priority": "P2",
            "state": "ACTIVE",
            "return_time": None,
            "ack_time": None,
            "value": None,
            "threshold": None,
        }
    )
    decision = model.decide(ambiguous)
    assert decision.category == "UNKNOWN"
    assert not decision.accepted

    negated = test_rows[2]["record"].model_copy(
        update={"description": "未发现故障，原因不确定，请人工核验"}
    )
    negated_decision = model.decide(negated)
    assert negated_decision.category == "UNKNOWN"
    assert not negated_decision.accepted
    assert "保守语义边界=命中" in negated_decision.evidence


def test_expert_result_is_not_overridden_and_unknown_can_only_use_agreement(
    rows: list[dict[str, Any]], bundle: dict[str, Any]
) -> None:
    supervised._configured_model = SupervisedModel(bundle)
    source = next(row["record"] for row in rows if row["label"] == "EQUIPMENT_FAULT")
    expert_known = source.model_copy(update={"description": "工艺扰动导致反应器压力温度流量异常"})
    request = analysis_request([expert_known])
    result = analyze(request).record_results[0]
    assert result.cause_category == "PROCESS_DISTURBANCE"
    assert any("SUPERVISED_CAUSE_V1" in item for item in result.evidence)
    assert any("专家结果保持不变" in item for item in result.evidence)

    expert_unknown = source.model_copy(update={"description": "轴承卡涩导致转子停机"})
    unknown_request = request.model_copy(update={"records": [expert_unknown]})
    supplemented = analyze(unknown_request).record_results[0]
    assert supplemented.cause_category == "EQUIPMENT_FAULT"
    assert any("由双分支一致结果补充" in item for item in supplemented.evidence)

    negated = source.model_copy(update={"description": "未发现设备故障，原因不确定"})
    negated_request = request.model_copy(update={"records": [negated]})
    negated_result = analyze(negated_request).record_results[0]
    assert negated_result.cause_category == "UNKNOWN"
    assert any("保守语义边界命中" in item for item in negated_result.evidence)

    maintenance = next(row["record"] for row in rows if row["label"] == "MAINTENANCE_TEST")
    maintenance_request = request.model_copy(update={"records": [maintenance]})
    maintenance_result = analyze(maintenance_request).record_results[0]
    assert maintenance_result.cause_category == "MAINTENANCE_TEST"


def test_declared_negation_maintenance_and_missing_value_boundaries(bundle: dict[str, Any]) -> None:
    cases = [json.loads(line) for line in BOUNDARY_DATA.read_text("utf-8").splitlines()]
    supervised._configured_model = SupervisedModel(bundle)
    request = analysis_request([case["record"] for case in cases])
    results = analyze(request).record_results
    actual = {str(result.record_id): result.cause_category for result in results}
    for case in cases:
        assert actual[case["record"]["record_id"]] == case["expected"]


def test_bundle_metadata_is_frozen(bundle: dict[str, Any]) -> None:
    assert bundle["format"] == MODEL_FORMAT
    assert bundle["model_version"] == MODEL_VERSION
