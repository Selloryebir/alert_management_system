"""分析接口 v2 的 Pydantic 契约。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


NoiseType = Literal["NORMAL", "DUPLICATE", "CHATTER", "SHORT_LIVED", "PERSISTENT"]
AlarmClass = Literal["NUISANCE", "ACTIONABLE", "STANDARD"]
CauseCategory = Literal[
    "PROCESS_DISTURBANCE",
    "EQUIPMENT_FAULT",
    "INSTRUMENT_ISSUE",
    "MAINTENANCE_TEST",
    "UNKNOWN",
]


class ContractModel(BaseModel):
    """禁止未声明字段，避免 Java 与 Python 契约静默漂移。"""

    model_config = ConfigDict(extra="forbid")


class RuleParameters(ContractModel):
    duplicate_window_seconds: Annotated[int, Field(gt=0)]
    chatter_window_seconds: Annotated[int, Field(gt=0)]
    chatter_min_count: Annotated[int, Field(ge=2)]
    chatter_min_transition_ratio: Annotated[float, Field(ge=0.0, le=1.0)]
    short_lived_seconds: Annotated[int, Field(gt=0)]
    persistent_requires_ack: bool
    episode_gap_seconds: Annotated[int, Field(gt=0)]
    chain_window_seconds: Annotated[int, Field(gt=0)]
    chain_min_steps: Annotated[int, Field(ge=2, le=5)]
    min_episode_support: Annotated[int, Field(ge=2)]
    min_transition_probability: Annotated[float, Field(gt=0.0, le=1.0)]
    min_lift: Annotated[float, Field(ge=1.0)]
    expert_min_score: Annotated[float, Field(ge=0.0, le=1.0)]
    expert_min_margin: Annotated[float, Field(ge=0.0, le=1.0)]


class AlarmRecord(ContractModel):
    record_id: UUID
    batch_id: UUID
    source_row: Annotated[int, Field(gt=0)]
    event_time: datetime
    return_time: datetime | None = None
    ack_time: datetime | None = None
    site: Annotated[str, Field(min_length=1, max_length=100)]
    area: Annotated[str, Field(min_length=1, max_length=100)]
    unit: Annotated[str, Field(max_length=100)] | None = None
    tag: Annotated[str, Field(min_length=1, max_length=120)]
    description: Annotated[str, Field(min_length=1, max_length=500)]
    priority: Literal["P1", "P2", "P3", "P4"]
    state: Literal["ACTIVE", "RETURNED", "ACKNOWLEDGED"]
    value: Decimal | None = None
    threshold: Decimal | None = None
    engineering_unit: Annotated[str, Field(max_length=40)] | None = None
    source_system: Annotated[str, Field(min_length=1, max_length=100)]
    operator: Annotated[str, Field(max_length=100)] | None = None
    raw_payload: dict[str, str]

    @field_validator("event_time", "return_time", "ack_time")
    @classmethod
    def time_must_include_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("时间必须包含时区")
        return value

    @model_validator(mode="after")
    def later_times_must_not_precede_event(self) -> AlarmRecord:
        if self.return_time is not None and self.return_time < self.event_time:
            raise ValueError("return_time 不得早于 event_time")
        if self.ack_time is not None and self.ack_time < self.event_time:
            raise ValueError("ack_time 不得早于 event_time")
        return self


class AnalysisRequest(ContractModel):
    analysis_run_id: UUID
    contract_version: Literal["v2"]
    algorithm_version: Literal["0.2.0"]
    parameters: RuleParameters
    records: list[AlarmRecord]

    @model_validator(mode="after")
    def records_must_form_one_batch(self) -> AnalysisRequest:
        record_ids = [record.record_id for record in self.records]
        if len(record_ids) != len(set(record_ids)):
            raise ValueError("records 中的 record_id 不得重复")
        if len({record.batch_id for record in self.records}) > 1:
            raise ValueError("一次分析只能包含一个导入批次")
        return self


class RecordResult(ContractModel):
    record_id: UUID
    noise_type: NoiseType
    alarm_class: AlarmClass
    cause_category: CauseCategory
    score: Annotated[float, Field(ge=0.0, le=1.0)]
    evidence: list[str]


class EventChain(ContractModel):
    chain_id: UUID
    member_record_ids: list[UUID]
    start_time: datetime
    end_time: datetime
    start_record_id: UUID
    association_rule: str
    explanation: str


class AnalysisSummary(ContractModel):
    input_count: Annotated[int, Field(ge=0)]
    success_count: Annotated[int, Field(ge=0)]
    failure_count: Annotated[int, Field(ge=0)]
    noise_type_counts: dict[NoiseType, Annotated[int, Field(ge=0)]]
    cause_category_counts: dict[CauseCategory, Annotated[int, Field(ge=0)]]
    event_chain_count: Annotated[int, Field(ge=0)]


class AnalysisError(ContractModel):
    record_id: UUID | None
    code: str
    message: str


class AnalysisResponse(ContractModel):
    analysis_run_id: UUID
    contract_version: Literal["v2"]
    algorithm_version: Literal["0.2.0"]
    rule_version: Literal["hybrid-v2.0.0"]
    parameters: RuleParameters
    record_results: list[RecordResult]
    event_chains: list[EventChain]
    summary: AnalysisSummary
    errors: list[AnalysisError]
