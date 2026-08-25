"""FastAPI 应用入口。"""

from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

from algorithm_service import CONTRACT_VERSION, SERVICE_NAME, SERVICE_VERSION
from algorithm_service.models import AnalysisRequest, AnalysisResponse
from algorithm_service.rules import analyze


class HealthResponse(BaseModel):
    status: Literal["UP"]
    service: str
    version: str
    contract_version: str


app = FastAPI(
    title="报警管理系统算法服务",
    version=SERVICE_VERSION,
)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """返回进程级健康信息，不执行算法或访问外部系统。"""

    return HealthResponse(
        status="UP",
        service=SERVICE_NAME,
        version=SERVICE_VERSION,
        contract_version=CONTRACT_VERSION,
    )


@app.post("/api/v1/analyze", response_model=AnalysisResponse)
def analyze_records(request: AnalysisRequest) -> AnalysisResponse:
    """按显式 v1 参数运行纯计算规则，不访问业务数据库。"""

    return analyze(request)
