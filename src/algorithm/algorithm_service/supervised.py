"""只读加载并执行加密的监督原因分类候选。"""

from __future__ import annotations

from base64 import b64decode
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import numpy as np
from sklearn.ensemble import AdaBoostClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.pipeline import Pipeline
from sklearn.svm import LinearSVC
from sklearn.tree import DecisionTreeClassifier
import skops.io as sio

from algorithm_service.models import AlarmRecord, CauseCategory


MODEL_VERSION = "cause-hybrid-svm-adaboost-v1"
MODEL_FORMAT = "alert-management-skops-aesgcm-v1"
MODEL_MAGIC = b"AMSM1"
MODEL_AAD = MODEL_FORMAT.encode("ascii")
MODEL_KEY_FILE_ENV = "ALGORITHM_MODEL_KEY_FILE"
MODEL_FILE_ENV = "ALGORITHM_MODEL_FILE"
KNOWN_CAUSES = (
    "PROCESS_DISTURBANCE",
    "EQUIPMENT_FAULT",
    "INSTRUMENT_ISSUE",
    "MAINTENANCE_TEST",
)
CONSERVATIVE_TERMS = (
    "未发现故障",
    "无明确原因",
    "并非故障",
    "排除故障",
    "证据不足",
    "原因不确定",
    "uncertain",
    "no fault found",
    "not a fault",
    "normal operation",
)


class ModelConfigurationError(ValueError):
    """监督模型文件或密钥不符合发布契约。"""


@dataclass(frozen=True, slots=True)
class BranchDecision:
    category: CauseCategory
    score: float
    margin: float


@dataclass(frozen=True, slots=True)
class SupervisedDecision:
    category: CauseCategory
    accepted: bool
    evidence: str


def _read_key(path: Path) -> bytes:
    try:
        encoded = path.read_text(encoding="ascii").strip()
        key = b64decode(encoded, altchars=b"-_", validate=True)
    except (OSError, UnicodeError, ValueError) as exc:
        raise ModelConfigurationError("监督模型密钥文件不可读或格式无效") from exc
    if len(key) != 32:
        raise ModelConfigurationError("监督模型密钥必须解码为 32 字节")
    return key


def encrypt_model_bytes(serialized: bytes, key: bytes, nonce: bytes) -> bytes:
    """使用 AES-256-GCM 认证加密可信 skops 制品。"""

    if len(key) != 32 or len(nonce) != 12:
        raise ValueError("密钥必须为 32 字节且 nonce 必须为 12 字节")
    return MODEL_MAGIC + nonce + AESGCM(key).encrypt(nonce, serialized, MODEL_AAD)


def decrypt_model_bytes(envelope: bytes, key: bytes) -> bytes:
    if len(envelope) <= len(MODEL_MAGIC) + 12 or not envelope.startswith(MODEL_MAGIC):
        raise ModelConfigurationError("监督模型文件头无效")
    nonce_start = len(MODEL_MAGIC)
    nonce = envelope[nonce_start : nonce_start + 12]
    try:
        return AESGCM(key).decrypt(nonce, envelope[nonce_start + 12 :], MODEL_AAD)
    except InvalidTag as exc:
        raise ModelConfigurationError("监督模型密钥错误或文件已被篡改") from exc


def _trusted_types(serialized: bytes) -> list[str]:
    unknown = sio.get_untrusted_types(data=serialized)
    if unknown:
        raise ModelConfigurationError("监督模型包含未允许的序列化类型")
    return []


def structural_features(record: AlarmRecord) -> np.ndarray:
    """构造不含场站标识的低维工程特征。"""

    duration = 0.0
    if record.return_time is not None:
        duration = max(0.0, (record.return_time - record.event_time).total_seconds())
    deviation = 0.0
    has_deviation = record.value is not None and record.threshold not in (None, 0)
    if has_deviation:
        deviation = float((record.value - record.threshold) / abs(record.threshold))
        deviation = max(-10.0, min(10.0, deviation))
    priority = [float(record.priority == item) for item in ("P1", "P2", "P3", "P4")]
    state = [float(record.state == item) for item in ("ACTIVE", "RETURNED", "ACKNOWLEDGED")]
    return np.asarray(
        priority
        + state
        + [
            min(duration, 86_400.0) / 86_400.0,
            float(record.return_time is not None),
            float(record.ack_time is not None),
            deviation,
            float(has_deviation),
        ],
        dtype=np.float64,
    )


def text_feature(record: AlarmRecord) -> str:
    return record.description.strip().lower()


def has_conservative_language(record: AlarmRecord) -> bool:
    text = text_feature(record)
    return any(term in text for term in CONSERVATIVE_TERMS)


def _branch_decisions(estimator: Any, features: Any) -> list[BranchDecision]:
    scores = np.asarray(estimator.decision_function(features), dtype=np.float64)
    if scores.ndim == 1:
        scores = scores.reshape(1, -1)
    decisions: list[BranchDecision] = []
    for row in scores:
        order = np.argsort(row)
        best_index = int(order[-1])
        second = float(row[order[-2]]) if row.size > 1 else -float(row[best_index])
        category = str(estimator.classes_[best_index])
        if category not in KNOWN_CAUSES:
            raise ModelConfigurationError("监督模型返回了未知原因类别")
        decisions.append(
            BranchDecision(
                category=category,  # type: ignore[arg-type]
                score=float(row[best_index]),
                margin=float(row[best_index] - second),
            )
        )
    return decisions


class SupervisedModel:
    """经版本和结构校验的只读 SVM/AdaBoost 双分支。"""

    def __init__(self, bundle: Mapping[str, Any]):
        required_fields = {
            "format",
            "model_version",
            "classes",
            "seed",
            "thresholds",
            "text_pipeline",
            "structure_model",
        }
        if set(bundle) != required_fields:
            raise ModelConfigurationError("监督模型必要字段不完整或包含未声明字段")
        if bundle.get("format") != MODEL_FORMAT or bundle.get("model_version") != MODEL_VERSION:
            raise ModelConfigurationError("监督模型版本或格式不匹配")
        classes = bundle.get("classes")
        if not isinstance(classes, (list, tuple)) or tuple(classes) != KNOWN_CAUSES:
            raise ModelConfigurationError("监督模型类别顺序不匹配")
        if bundle.get("seed") != 20260827:
            raise ModelConfigurationError("监督模型训练种子不匹配")
        text_pipeline = bundle.get("text_pipeline")
        structure_model = bundle.get("structure_model")
        if not isinstance(text_pipeline, Pipeline) or set(text_pipeline.named_steps) != {
            "tfidf",
            "classifier",
        }:
            raise ModelConfigurationError("监督模型 SVM 分支结构无效")
        if not isinstance(text_pipeline.named_steps["tfidf"], TfidfVectorizer) or not isinstance(
            text_pipeline.named_steps["classifier"], LinearSVC
        ):
            raise ModelConfigurationError("监督模型 SVM 分支类型无效")
        if not isinstance(structure_model, AdaBoostClassifier) or not isinstance(
            structure_model.estimator, DecisionTreeClassifier
        ):
            raise ModelConfigurationError("监督模型 AdaBoost 分支类型无效")
        if structure_model.estimator.max_depth != 1:
            raise ModelConfigurationError("监督模型 AdaBoost 基学习器不是单层树")
        text_classifier = text_pipeline.named_steps["classifier"]
        text_vectorizer = text_pipeline.named_steps["tfidf"]
        if (
            not hasattr(text_vectorizer, "vocabulary_")
            or not hasattr(text_classifier, "classes_")
            or not hasattr(text_classifier, "coef_")
            or not hasattr(structure_model, "classes_")
            or not hasattr(structure_model, "estimators_")
            or tuple(sorted(map(str, text_classifier.classes_))) != tuple(sorted(KNOWN_CAUSES))
            or tuple(sorted(map(str, structure_model.classes_))) != tuple(sorted(KNOWN_CAUSES))
        ):
            raise ModelConfigurationError("监督模型分支未完成训练或类别不匹配")
        thresholds = bundle.get("thresholds")
        if not isinstance(thresholds, dict):
            raise ModelConfigurationError("监督模型冻结阈值无效")
        self.text_pipeline = text_pipeline
        self.structure_model = structure_model
        self.thresholds = dict(thresholds)
        required = {"svm_score", "svm_margin", "adaboost_score", "adaboost_margin"}
        if set(self.thresholds) != required or not all(
            type(value) in (int, float) and np.isfinite(value) for value in self.thresholds.values()
        ):
            raise ModelConfigurationError("监督模型冻结阈值无效")

    @classmethod
    def load(cls, model_path: Path, key_path: Path) -> SupervisedModel:
        try:
            envelope = model_path.read_bytes()
        except OSError as exc:
            raise ModelConfigurationError("监督模型文件不可读") from exc
        serialized = decrypt_model_bytes(envelope, _read_key(key_path))
        try:
            bundle = sio.loads(serialized, trusted=_trusted_types(serialized))
        except Exception as exc:
            raise ModelConfigurationError("监督模型安全序列化内容无效") from exc
        if not isinstance(bundle, dict):
            raise ModelConfigurationError("监督模型根结构无效")
        try:
            return cls(bundle)
        except ModelConfigurationError:
            raise
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise ModelConfigurationError("监督模型必要结构无效") from exc

    def decide(self, record: AlarmRecord) -> SupervisedDecision:
        return self.decide_many([record])[0]

    def decide_many(self, records: list[AlarmRecord]) -> list[SupervisedDecision]:
        if not records:
            return []
        svm_decisions = _branch_decisions(
            self.text_pipeline, [text_feature(record) for record in records]
        )
        ada_decisions = _branch_decisions(
            self.structure_model,
            np.vstack([structural_features(record) for record in records]),
        )
        return [
            self._combine(record, svm, ada)
            for record, svm, ada in zip(records, svm_decisions, ada_decisions, strict=True)
        ]

    def _combine(
        self, record: AlarmRecord, svm: BranchDecision, ada: BranchDecision
    ) -> SupervisedDecision:
        conservative = has_conservative_language(record)
        accepted = (
            not conservative
            and svm.category == ada.category
            and svm.score >= self.thresholds["svm_score"]
            and svm.margin >= self.thresholds["svm_margin"]
            and ada.score >= self.thresholds["adaboost_score"]
            and ada.margin >= self.thresholds["adaboost_margin"]
        )
        category: CauseCategory = svm.category if accepted else "UNKNOWN"
        evidence = (
            f"SUPERVISED_CAUSE_V1：model={MODEL_VERSION}；"
            f"SVM={svm.category},score={svm.score:.6f},margin={svm.margin:.6f}；"
            f"AdaBoost={ada.category},score={ada.score:.6f},margin={ada.margin:.6f}；"
            f"双分支{'同意并通过冻结阈值' if accepted else '未同时同意或未通过冻结阈值'}；"
            f"保守语义边界={'命中' if conservative else '未命中'}；"
            "score/margin 为判别量，不是概率，结果保留人工复核。"
        )
        return SupervisedDecision(category=category, accepted=accepted, evidence=evidence)


def load_model_from_environment(environ: Mapping[str, str] | None = None) -> SupervisedModel | None:
    values = os.environ if environ is None else environ
    model_value = values.get(MODEL_FILE_ENV, "").strip()
    key_value = values.get(MODEL_KEY_FILE_ENV, "").strip()
    if not model_value and not key_value:
        return None
    if not model_value or not key_value:
        raise ModelConfigurationError(f"{MODEL_FILE_ENV} 与 {MODEL_KEY_FILE_ENV} 必须同时设置")
    return SupervisedModel.load(Path(model_value), Path(key_value))


_configured_model: SupervisedModel | None = None


def configure_model(environ: Mapping[str, str] | None = None) -> None:
    global _configured_model
    _configured_model = load_model_from_environment(environ)


def configured_model() -> SupervisedModel | None:
    return _configured_model
