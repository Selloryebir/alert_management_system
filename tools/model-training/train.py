#!/usr/bin/env python3
"""从分组隔离的工程场景训练并认证加密监督原因模型。"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from base64 import urlsafe_b64encode
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import numpy as np
import skops.io as sio
from sklearn.ensemble import AdaBoostClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.pipeline import Pipeline
from sklearn.svm import LinearSVC
from sklearn.tree import DecisionTreeClassifier

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ALGORITHM_ROOT = REPOSITORY_ROOT / "src" / "algorithm"
if str(ALGORITHM_ROOT) not in sys.path:
    sys.path.insert(0, str(ALGORITHM_ROOT))

from algorithm_service.models import AlarmRecord  # noqa: E402
from algorithm_service.supervised import (  # noqa: E402
    KNOWN_CAUSES,
    MODEL_FORMAT,
    MODEL_VERSION,
    SupervisedModel,
    encrypt_model_bytes,
    structural_features,
    text_feature,
)

SEED = 20260827
SPLITS = ("train", "validation", "test")
DEFAULT_DATA = (
    Path(sys._MEIPASS) / "model-data" / "engineering-scenarios.jsonl"
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent / "data" / "engineering-scenarios.jsonl"
)
FROZEN_THRESHOLDS = {
    "svm_score": -0.4,
    "svm_margin": 0.05,
    "adaboost_score": 0.0,
    "adaboost_margin": 0.01,
}
MAX_CROSS_SPLIT_TEXT_SIMILARITY = 0.88


class TrainingDataError(ValueError):
    """训练数据或隔离契约无效。"""


def load_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise TrainingDataError("训练数据不可读") from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise TrainingDataError(f"第 {line_number} 行不是合法 JSON") from exc
        if not isinstance(row, dict):
            raise TrainingDataError(f"第 {line_number} 行必须是 JSON 对象")
        if row.get("label") not in KNOWN_CAUSES:
            raise TrainingDataError(f"第 {line_number} 行 label 无效")
        if row.get("split") not in SPLITS:
            raise TrainingDataError(f"第 {line_number} 行 split 无效")
        if not isinstance(row.get("group_id"), str) or not row["group_id"].strip():
            raise TrainingDataError(f"第 {line_number} 行 group_id 无效")
        try:
            row["record"] = AlarmRecord.model_validate(row["record"])
        except Exception as exc:
            raise TrainingDataError(f"第 {line_number} 行 record 无效") from exc
        rows.append(row)
    if not rows:
        raise TrainingDataError("训练数据为空")
    validate_group_isolation(rows)
    return rows


def validate_group_isolation(rows: list[dict[str, Any]]) -> None:
    groups_by_split = {
        split: {row["group_id"] for row in rows if row["split"] == split}
        for split in SPLITS
    }
    for split in SPLITS:
        if not groups_by_split[split]:
            raise TrainingDataError(f"{split} 分组为空")
        labels = {row["label"] for row in rows if row["split"] == split}
        missing = set(KNOWN_CAUSES) - labels
        if missing:
            raise TrainingDataError(f"{split} 缺少类别：{','.join(sorted(missing))}")
    for index, first in enumerate(SPLITS):
        for second in SPLITS[index + 1 :]:
            overlap = groups_by_split[first] & groups_by_split[second]
            if overlap:
                raise TrainingDataError(
                    f"{first}/{second} group_id 泄漏：{','.join(sorted(overlap))}"
                )
    normalized = [
        (
            row["split"],
            re.sub(
                r"\s+", "", unicodedata.normalize("NFKC", text_feature(row["record"]))
            ).lower(),
        )
        for row in rows
    ]
    for index, (first_split, first_text) in enumerate(normalized):
        for second_split, second_text in normalized[index + 1 :]:
            if first_split == second_split:
                continue
            if first_text == second_text:
                raise TrainingDataError("跨 split 报警描述完全重复")
            similarity = SequenceMatcher(None, first_text, second_text).ratio()
            if similarity >= MAX_CROSS_SPLIT_TEXT_SIMILARITY:
                raise TrainingDataError(f"跨 split 报警描述过度相似：{similarity:.3f}")


def _split(rows: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [row for row in rows if row["split"] == name]


def train_bundle(rows: list[dict[str, Any]]) -> dict[str, Any]:
    train = _split(rows, "train")
    texts = [text_feature(row["record"]) for row in train]
    structures = np.vstack([structural_features(row["record"]) for row in train])
    labels = [row["label"] for row in train]
    text_pipeline = Pipeline(
        [
            (
                "tfidf",
                TfidfVectorizer(
                    analyzer="char",
                    ngram_range=(2, 4),
                    lowercase=True,
                    min_df=1,
                    sublinear_tf=True,
                ),
            ),
            ("classifier", LinearSVC(C=1.0, random_state=SEED)),
        ]
    )
    structure_model = AdaBoostClassifier(
        estimator=DecisionTreeClassifier(max_depth=1, random_state=SEED),
        n_estimators=80,
        learning_rate=0.5,
        random_state=SEED,
    )
    text_pipeline.fit(texts, labels)
    structure_model.fit(structures, labels)
    return {
        "format": MODEL_FORMAT,
        "model_version": MODEL_VERSION,
        "classes": KNOWN_CAUSES,
        "seed": SEED,
        "thresholds": FROZEN_THRESHOLDS,
        "text_pipeline": text_pipeline,
        "structure_model": structure_model,
    }


def evaluate(
    bundle: dict[str, Any], rows: list[dict[str, Any]], split: str
) -> dict[str, Any]:
    model = SupervisedModel(bundle)
    selected = _split(rows, split)
    expected = [row["label"] for row in selected]
    predicted = [model.decide(row["record"]).category for row in selected]
    accepted = sum(value != "UNKNOWN" for value in predicted)
    labels = [*KNOWN_CAUSES, "UNKNOWN"]
    return {
        "count": len(selected),
        "group_count": len({row["group_id"] for row in selected}),
        "label_counts": dict(sorted(Counter(expected).items())),
        "coverage": accepted / len(selected),
        "classification": classification_report(
            expected,
            predicted,
            labels=list(KNOWN_CAUSES),
            output_dict=True,
            zero_division=0,
        ),
        "confusion_labels": labels,
        "confusion_matrix": confusion_matrix(
            expected, predicted, labels=labels
        ).tolist(),
    }


def write_artifacts(
    bundle: dict[str, Any],
    rows: list[dict[str, Any]],
    model_path: Path,
    key_path: Path,
    report_path: Path,
) -> None:
    existing = [path for path in (model_path, key_path, report_path) if path.exists()]
    if existing:
        raise FileExistsError(f"拒绝覆盖已有输出：{existing[0]}")
    key_path.parent.mkdir(parents=True, exist_ok=True)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    key = os.urandom(32)
    key_path.write_text(urlsafe_b64encode(key).decode("ascii"), encoding="ascii")
    if os.name != "nt":
        key_path.chmod(0o600)
    serialized = sio.dumps(bundle)
    model_path.write_bytes(encrypt_model_bytes(serialized, key, os.urandom(12)))
    report = {
        "schema_version": 1,
        "model_version": MODEL_VERSION,
        "applicability": "报警原因分类的项目自建工程场景分布",
        "data_boundary": "中英文描述、优先级、状态、时间和可选归一化偏差；不含现场设备拓扑或连续过程轨迹",
        "field_calibration": "部署到具体装置前，使用授权标注数据重新校准阈值并完成独立验证",
        "seed": SEED,
        "thresholds": FROZEN_THRESHOLDS,
        "group_isolation": True,
        "text_isolation": {
            "exact_duplicate_rejected": True,
            "maximum_similarity_exclusive": MAX_CROSS_SPLIT_TEXT_SIMILARITY,
        },
        "validation": evaluate(bundle, rows, "validation"),
        "test": evaluate(bundle, rows, "test"),
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--model-out", type=Path, required=True)
    parser.add_argument("--key-out", type=Path, required=True)
    parser.add_argument("--report-out", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = load_rows(args.data)
    bundle = train_bundle(rows)
    write_artifacts(bundle, rows, args.model_out, args.key_out, args.report_out)
    print(f"已生成认证加密模型：{args.model_out}")
    print(f"评估报告：{args.report_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
