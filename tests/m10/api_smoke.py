#!/usr/bin/env python3
"""M10 项目化业务 API 黑盒验收。只使用公开 HTTP 契约。"""

from __future__ import annotations

import argparse
from io import BytesIO
import json
from pathlib import Path
import re
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4
import zipfile


HTTP = build_opener(ProxyHandler({}))
CHINESE = re.compile(r"[\u3400-\u9fff]")


def fail(message: str, detail: Any | None = None) -> None:
    if detail is None:
        raise AssertionError(message)
    raise AssertionError(f"{message}\n{json.dumps(detail, ensure_ascii=False, indent=2, default=str)}")


class Api:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Any | None = None,
        headers: dict[str, str] | None = None,
        expected: tuple[int, ...] = (200,),
        timeout: int = 90,
    ) -> tuple[int, dict[str, str], bytes]:
        data: bytes | None = None
        actual_headers = {"Accept": "application/json", **(headers or {})}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
            actual_headers["Content-Type"] = "application/json"
        request = Request(self.base_url + path, method=method, data=data, headers=actual_headers)
        try:
            with HTTP.open(request, timeout=timeout) as response:
                status = response.status
                response_headers = {key.lower(): value for key, value in response.headers.items()}
                raw = response.read()
        except HTTPError as error:
            status = error.code
            response_headers = {key.lower(): value for key, value in error.headers.items()}
            raw = error.read()
        except URLError as error:
            fail(f"无法访问 {path}: {error.reason}")
        if status not in expected:
            fail(
                f"{method} {path} 返回 HTTP {status}，预期 {expected}",
                raw.decode("utf-8", errors="replace")[:2000],
            )
        return status, response_headers, raw

    def json(
        self,
        method: str,
        path: str,
        *,
        body: Any | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> Any:
        status, _headers, raw = self.request(method, path, body=body, expected=expected)
        if not raw and status in (204,):
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as error:
            fail(f"{method} {path} 未返回合法 JSON: {error}", raw.decode(errors="replace")[:2000])

    def multipart(
        self,
        path: str,
        *,
        project_id: str,
        dataset: Path,
        corrections: dict[str, dict[str, str]] | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> Any:
        boundary = f"----m10-{uuid4().hex}"
        parts = [
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"project_id\"\r\n\r\n{project_id}\r\n".encode(),
            (
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
                f"filename=\"{dataset.name}\"\r\nContent-Type: text/csv\r\n\r\n"
            ).encode(),
            dataset.read_bytes(),
        ]
        if corrections is not None:
            parts.extend([
                f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"corrections\"\r\n\r\n".encode(),
                json.dumps(corrections, ensure_ascii=False, separators=(",", ":")).encode(),
            ])
        parts.append(f"\r\n--{boundary}--\r\n".encode())
        _status, _headers, raw = self._raw_multipart(path, b"".join(parts), boundary, expected)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            fail(f"POST {path} 未返回合法 JSON", raw.decode(errors="replace")[:2000])

    def _raw_multipart(
        self, path: str, data: bytes, boundary: str, expected: tuple[int, ...]
    ) -> tuple[int, dict[str, str], bytes]:
        request = Request(
            self.base_url + path,
            method="POST",
            data=data,
            headers={
                "Accept": "application/json",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
        )
        try:
            with HTTP.open(request, timeout=120) as response:
                result = (response.status, dict(response.headers.items()), response.read())
        except HTTPError as error:
            result = (error.code, dict(error.headers.items()), error.read())
        if result[0] not in expected:
            fail(
                f"POST {path} 返回 HTTP {result[0]}，预期 {expected}",
                result[2].decode(errors="replace")[:2000],
            )
        return result


def identifier(payload: Any, field: str, context: str) -> str:
    if not isinstance(payload, dict) or not isinstance(payload.get(field), str) or not payload[field]:
        fail(f"{context} 缺少 {field}", payload)
    return payload[field]


def assert_error_chinese(payload: Any, context: str) -> None:
    if not isinstance(payload, dict):
        fail(f"{context} 错误响应不是 JSON 对象", payload)
    message = payload.get("message")
    if not isinstance(message, str) or not CHINESE.search(message):
        fail(f"{context} 未返回可操作的中文错误", payload)


def create_project(api: Api, *, code: str, name: str, title: str, fields: list[str]) -> dict[str, Any]:
    response = api.json(
        "POST",
        "/api/v1/projects",
        body={
            "code": code,
            "name": name,
            "client_name": f"{name}客户",
            "site": f"{name}厂区",
            "unit_name": f"{name}装置",
            "report_title": title,
            "report_fields": fields,
        },
        expected=(200, 201),
    )
    project_id = identifier(response, "project_id", f"创建项目 {name}")
    if response.get("status") != "ACTIVE" or response.get("code") != code or response.get("name") != name:
        fail("新项目事实与请求不一致", response)
    if response.get("report_title") != title or response.get("report_fields") != fields:
        fail("新项目报告设置未原样保存", response)
    response["project_id"] = project_id
    return response


def expect_conflict(api: Api, method: str, path: str, body: Any | None, context: str) -> dict[str, Any]:
    response = api.json(method, path, body=body, expected=(409,))
    assert_error_chinese(response, context)
    return response


def import_dataset(api: Api, project_id: str, dataset: Path) -> dict[str, Any]:
    preview = api.multipart("/api/v1/imports/preview", project_id=project_id, dataset=dataset)
    batch_id = identifier(preview, "batch_id", "导入预览")
    if preview.get("project_id") != project_id:
        fail("预览批次未返回所属项目", preview)
    if preview.get("status") != "READY" or preview.get("valid_rows") != 300 or preview.get("error_count") != 0:
        fail("固定 300 行样例预览结果错误", preview)
    confirmed = api.json("POST", f"/api/v1/imports/{batch_id}/confirm")
    if confirmed.get("project_id") != project_id or confirmed.get("status") != "IMPORTED":
        fail("确认导入未保留项目归属或状态", confirmed)
    return confirmed


def analyze(api: Api, batch_id: str) -> dict[str, Any]:
    run = api.json("POST", f"/api/v1/imports/{batch_id}/analyses", body=None, expected=(200, 201))
    run_id = identifier(run, "run_id", "启动分析")
    deadline = time.monotonic() + 90
    while run.get("status") == "ANALYZING" and time.monotonic() < deadline:
        time.sleep(0.5)
        run = api.json("GET", f"/api/v1/imports/{batch_id}/analyses/latest")
    if run.get("status") != "COMPLETED" or run.get("batch_id") != batch_id:
        fail("分析未在时限内完成", run)
    run["run_id"] = run_id
    return run


def xlsx_text(raw: bytes, context: str) -> str:
    try:
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            names = [name for name in archive.namelist() if name.endswith(".xml")]
            return "\n".join(archive.read(name).decode("utf-8", errors="replace") for name in names)
    except zipfile.BadZipFile:
        fail(f"{context} 不是可打开的 XLSX 文件")


def assert_project_list(api: Api, project_id: str, own_batches: set[str], foreign: set[str]) -> None:
    query = urlencode({"project_id": project_id, "limit": "100"})
    payload = api.json("GET", f"/api/v1/imports?{query}")
    if not isinstance(payload, list):
        fail("项目批次列表不是数组", payload)
    actual = {identifier(item, "batch_id", "项目批次列表项") for item in payload}
    if not own_batches.issubset(actual) or actual.intersection(foreign):
        fail("项目批次列表发生缺失或跨项目泄漏", {"actual": sorted(actual), "own": sorted(own_batches), "foreign": sorted(foreign)})
    if any(item.get("project_id") != project_id for item in payload):
        fail("项目批次列表包含错误 project_id", payload)


def correction_flow(api: Api, project_id: str, dataset: Path) -> tuple[str, str]:
    rejected = api.multipart("/api/v1/imports/preview", project_id=project_id, dataset=dataset)
    rejected_batch_id = identifier(rejected, "batch_id", "非法小样首次预览")
    if rejected.get("status") != "REJECTED" or rejected.get("valid_rows") != 1:
        fail("非法小样首次预览未进入 REJECTED", rejected)
    source_rows = rejected.get("source_rows")
    if not isinstance(source_rows, list) or [row.get("source_row") for row in source_rows] != [2, 3]:
        fail("非法小样未按文件原值返回 source_rows", rejected)
    source_2 = source_rows[0].get("values", {})
    if source_2.get("priority") != "p9" or source_2.get("value") != "bad":
        fail("source_rows 未保留待修正行的原始文本", source_2)
    error_pairs = {(item.get("source_row"), item.get("field")) for item in rejected.get("errors", [])}
    if not {(2, "priority"), (2, "value")}.issubset(error_pairs):
        fail("非法小样没有精确报告原始源行和字段", rejected)

    for corrections, expected_message in (
        ({"999": {"priority": "P1"}}, "不存在的源行号：999"),
        ({"2": {"unknown": "x"}}, "未知目标字段：unknown"),
    ):
        invalid = api.multipart(
            "/api/v1/imports/preview",
            project_id=project_id,
            dataset=dataset,
            corrections=corrections,
            expected=(400,),
        )
        assert_error_chinese(invalid, "非法逐行修正")
        if expected_message not in invalid.get("message", ""):
            fail("非法逐行修正未返回精确可行动错误", invalid)

    corrections = {"2": {"priority": "P1", "value": "88.5"}}
    corrected = api.multipart(
        "/api/v1/imports/preview",
        project_id=project_id,
        dataset=dataset,
        corrections=corrections,
    )
    batch_id = identifier(corrected, "batch_id", "修正后预览")
    if corrected.get("status") != "READY" or corrected.get("valid_rows") != 2 or corrected.get("error_count") != 0:
        fail("逐行修正后未通过全量重新校验", corrected)
    if corrected.get("corrections") != corrections or corrected.get("source_rows") != source_rows:
        fail("修正响应未同时保存修正值和原始 source_rows", corrected)
    preview = {row.get("source_row"): row for row in corrected.get("preview_rows", [])}
    if preview.get(2, {}).get("priority") != "P1" or preview.get(2, {}).get("value") != 88.5:
        fail("预览规范化值未应用指定行修正", corrected)
    if preview.get(2, {}).get("raw_payload", {}).get("priority") != "p9" \
            or preview.get(2, {}).get("raw_payload", {}).get("value") != "bad":
        fail("预览修正覆盖了 raw_payload 原值", corrected)

    confirmed = api.json("POST", f"/api/v1/imports/{batch_id}/confirm")
    if confirmed.get("status") != "IMPORTED":
        fail("修正批次未能确认导入", confirmed)
    records = api.json("GET", f"/api/v1/imports/{batch_id}/records?page=0&size=20")
    if records.get("total") != 2:
        fail("修正批次记录总数错误", records)
    by_source = {row.get("source_row"): row for row in records.get("items", [])}
    persisted = by_source.get(2, {})
    if persisted.get("priority") != "P1" or persisted.get("value") != 88.5:
        fail("确认导入后规范化修正值未持久化", records)
    if persisted.get("raw_payload", {}).get("priority") != "p9" \
            or persisted.get("raw_payload", {}).get("value") != "bad":
        fail("确认导入后 raw_payload 未保留原始文本", records)
    return rejected_batch_id, batch_id


def assert_manual_list(
    api: Api,
    project_id: str,
    record_id: str,
    *,
    description: str,
    invalidated: bool,
) -> None:
    alarms = api.json("GET", f"/api/v1/projects/{project_id}/manual-alarms")
    if not isinstance(alarms, list):
        fail("人工补录列表不是数组", alarms)
    matching = [item for item in alarms if item.get("record_id") == record_id]
    if len(matching) != 1:
        fail("重新请求后人工补录缺失或重复", alarms)
    alarm = matching[0]
    if alarm.get("description") != description or bool(alarm.get("invalidated_at")) != invalidated:
        fail("人工补录重新请求未返回最新持久状态", alarm)


def assert_overview(overview: Any, *, batches: int, alarms: int, valid: int, invalid: int) -> None:
    expected = {
        "batch_count": batches,
        "alarm_count": alarms,
        "valid_alarm_count": valid,
        "invalid_alarm_count": invalid,
    }
    if not isinstance(overview, dict):
        fail("项目 overview 不是对象", overview)
    statistics = overview.get("statistics")
    if not isinstance(statistics, dict):
        fail("项目 overview 缺少 statistics", overview)
    actual = {key: statistics.get(key) for key in expected}
    if actual != expected:
        fail("项目 overview 未返回真实项目计数", {"expected": expected, "actual": actual, "full": overview})
    if not isinstance(statistics.get("pending_disposition_count"), int) or not isinstance(overview.get("recent_tasks"), list):
        fail("项目 overview 缺少待办数或最近任务", overview)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--correction-dataset", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    api = Api(args.base_url)
    suffix = uuid4().hex[:10].upper()
    code_a, code_b = f"M10-A-{suffix}", f"M10-B-{suffix}"
    name_a, name_b = f"M10甲项目-{suffix}", f"M10乙项目-{suffix}"
    title_a, title_b = f"甲项目报警报告-{suffix}", f"乙项目报警报告-{suffix}"

    project_a = create_project(api, code=code_a, name=name_a, title=title_a, fields=["summary", "priority", "disposition"])
    project_b = create_project(api, code=code_b, name=name_b, title=title_b, fields=["summary", "area", "chains"])
    project_empty = create_project(
        api,
        code=f"M10-E-{suffix}",
        name=f"M10空项目-{suffix}",
        title=f"空项目报告-{suffix}",
        fields=["summary"],
    )
    project_correction = create_project(
        api,
        code=f"M10-R-{suffix}",
        name=f"M10修正项目-{suffix}",
        title=f"修正项目报告-{suffix}",
        fields=["summary"],
    )
    a_id, b_id, empty_id = project_a["project_id"], project_b["project_id"], project_empty["project_id"]
    correction_id = project_correction["project_id"]

    expect_conflict(
        api,
        "POST",
        "/api/v1/projects",
        {
            "code": code_a,
            "name": f"不同名称-{suffix}",
            "client_name": "重复编号客户",
            "site": "重复编号厂区",
            "unit_name": "重复编号装置",
        },
        "重复项目编号",
    )
    expect_conflict(
        api,
        "POST",
        "/api/v1/projects",
        {
            "code": f"M10-C-{suffix}",
            "name": name_a,
            "client_name": "重复名称客户",
            "site": "重复名称厂区",
            "unit_name": "重复名称装置",
        },
        "重复项目名称",
    )

    rejected_batch_id, correction_batch_id = correction_flow(api, correction_id, args.correction_dataset)
    assert_project_list(api, correction_id, {rejected_batch_id, correction_batch_id}, set())
    assert_overview(
        api.json("GET", f"/api/v1/projects/{correction_id}/overview"),
        batches=2,
        alarms=2,
        valid=2,
        invalid=0,
    )

    batch_a = import_dataset(api, a_id, args.dataset)
    batch_b = import_dataset(api, b_id, args.dataset)
    batch_a_id, batch_b_id = batch_a["batch_id"], batch_b["batch_id"]
    assert_project_list(api, a_id, {batch_a_id}, {batch_b_id})
    assert_project_list(api, b_id, {batch_b_id}, {batch_a_id})
    assert_overview(api.json("GET", f"/api/v1/projects/{a_id}/overview"), batches=1, alarms=300, valid=300, invalid=0)
    assert_overview(api.json("GET", f"/api/v1/projects/{b_id}/overview"), batches=1, alarms=300, valid=300, invalid=0)

    run_a, run_b = analyze(api, batch_a_id), analyze(api, batch_b_id)
    alarms_a = api.json("GET", f"/api/v1/analyses/{run_a['run_id']}/alarms?page=0&size=5")
    first_alarm = alarms_a.get("items", [None])[0] if isinstance(alarms_a, dict) else None
    record_id = identifier(first_alarm, "record_id", "甲项目报警列表")
    disposition = api.json(
        "PATCH",
        f"/api/v1/analyses/{run_a['run_id']}/alarms/{record_id}/disposition",
        body={"status": "IN_PROGRESS", "operator": "M10验收员", "assignee": "甲班值长", "note": "M10 项目责任人验证"},
    )
    if disposition.get("status") != "IN_PROGRESS" or disposition.get("assignee") != "甲班值长":
        fail("处置未保存责任人", disposition)
    assigned = api.json(
        "GET",
        f"/api/v1/analyses/{run_a['run_id']}/alarms?{urlencode({'page': 0, 'size': 20, 'assignee': '甲班值长'})}",
    )
    if record_id not in {item.get("record_id") for item in assigned.get("items", [])}:
        fail("责任人筛选未返回已分配报警", assigned)

    manual = api.json(
        "POST",
        f"/api/v1/projects/{a_id}/manual-alarms",
        body={
            "event_time": "2026-08-26T09:30:00+08:00",
            "site": "甲厂区",
            "area": "甲区域",
            "unit": "甲装置",
            "tag": f"M10-MANUAL-{suffix}",
            "description": "M10 人工补录报警",
            "priority": "P2",
            "state": "ACTIVE",
            "value": 88.0,
            "threshold": 80.0,
            "engineering_unit": "℃",
            "source_system": "MANUAL_ENTRY",
            "operator": "M10验收员",
        },
        expected=(200, 201),
    )
    manual_record_id = identifier(manual, "record_id", "人工补录")
    manual_batch_id = identifier(manual, "batch_id", "人工补录")
    assert_manual_list(
        api, a_id, manual_record_id, description="M10 人工补录报警", invalidated=False
    )
    revised = api.json(
        "PATCH",
        f"/api/v1/projects/{a_id}/manual-alarms/{manual_record_id}",
        body={"description": "M10 人工补录已修订", "edited_by": "M10验收员", "reason": "核对后修订描述"},
    )
    if revised.get("description") != "M10 人工补录已修订":
        fail("人工补录修订未生效", revised)
    assert_manual_list(
        api, a_id, manual_record_id, description="M10 人工补录已修订", invalidated=False
    )
    invalidated = api.json(
        "POST",
        f"/api/v1/projects/{a_id}/manual-alarms/{manual_record_id}/invalidate",
        body={"operator": "M10验收员", "reason": "M10 受控作废验证"},
    )
    if not invalidated.get("invalidated_at") or invalidated.get("invalidated_by") != "M10验收员":
        fail("人工补录作废响应未明确失效状态", invalidated)
    assert_manual_list(
        api, a_id, manual_record_id, description="M10 人工补录已修订", invalidated=True
    )
    assert_manual_list(
        api, a_id, manual_record_id, description="M10 人工补录已修订", invalidated=True
    )
    assert_project_list(api, a_id, {batch_a_id, manual_batch_id}, {batch_b_id})
    assert_overview(api.json("GET", f"/api/v1/projects/{a_id}/overview"), batches=2, alarms=301, valid=300, invalid=1)

    for run, own_title, foreign_title in ((run_a, title_a, title_b), (run_b, title_b, title_a)):
        _status, headers, report = api.request(
            "POST",
            f"/api/v1/analyses/{run['run_id']}/reports/xlsx",
            body={"operator": "M10验收员"},
            expected=(200,),
            timeout=120,
        )
        if "spreadsheetml" not in headers.get("content-type", "") or len(report) < 500:
            fail("项目 XLSX 报告不可打开或内容类型错误", headers)
        report_text = xlsx_text(report, own_title)
        if own_title not in report_text or foreign_title in report_text:
            fail("报告抬头未按项目隔离", {"own": own_title, "foreign": foreign_title})

    for project_id, own_code, foreign_code in ((a_id, code_a, code_b), (b_id, code_b, code_a)):
        _status, headers, exported = api.request("GET", f"/api/v1/projects/{project_id}/export")
        if "application/json" not in headers.get("content-type", ""):
            fail("项目导出不是 JSON", headers)
        decoded = exported.decode("utf-8")
        payload = json.loads(decoded)
        if own_code not in decoded or foreign_code in decoded or payload.get("project", {}).get("project_id", payload.get("project_id")) != project_id:
            fail("项目导出发生缺失或跨项目泄漏", payload)

    archived_a = api.json("POST", f"/api/v1/projects/{a_id}/archive")
    if archived_a.get("status") != "ARCHIVED":
        fail("项目归档状态错误", archived_a)
    assert_manual_list(
        api, a_id, manual_record_id, description="M10 人工补录已修订", invalidated=True
    )
    archived_preview = api.multipart(
        "/api/v1/imports/preview", project_id=a_id, dataset=args.dataset, expected=(409,)
    )
    assert_error_chinese(archived_preview, "归档项目导入")
    archived_analysis = api.json(
        "POST", f"/api/v1/imports/{batch_a_id}/analyses", expected=(409,)
    )
    assert_error_chinese(archived_analysis, "归档项目分析")
    if "归档" not in archived_analysis.get("message", ""):
        fail("归档项目分析被其他批次状态偶然拒绝，未证明项目只读边界", archived_analysis)
    expect_conflict(
        api,
        "PATCH",
        f"/api/v1/projects/{a_id}",
        {"site": "归档后不应写入"},
        "归档项目设置写入",
    )
    restored_a = api.json("POST", f"/api/v1/projects/{a_id}/restore")
    if restored_a.get("status") != "ACTIVE":
        fail("项目恢复状态错误", restored_a)
    updated_a = api.json("PATCH", f"/api/v1/projects/{a_id}", body={"site": "恢复后可写入厂区"})
    if updated_a.get("site") != "恢复后可写入厂区":
        fail("项目恢复后仍不能继续写入", updated_a)

    api.json("POST", f"/api/v1/projects/{b_id}/archive")
    expect_conflict(api, "DELETE", f"/api/v1/projects/{b_id}", None, "有数据项目删除")
    api.json("POST", f"/api/v1/projects/{empty_id}/archive")
    api.json("DELETE", f"/api/v1/projects/{empty_id}", expected=(200, 204))
    api.json("GET", f"/api/v1/projects/{empty_id}", expected=(404,))

    overview_a = api.json("GET", f"/api/v1/projects/{a_id}/overview")
    overview_b = api.json("GET", f"/api/v1/projects/{b_id}/overview")
    assert_overview(overview_a, batches=2, alarms=301, valid=300, invalid=1)
    assert_overview(overview_b, batches=1, alarms=300, valid=300, invalid=0)

    result = {
        "status": "PASS",
        "project_a": {"project_id": a_id, "batch_id": batch_a_id, "run_id": run_a["run_id"]},
        "project_b": {"project_id": b_id, "batch_id": batch_b_id, "run_id": run_b["run_id"]},
        "correction_project": {
            "project_id": correction_id,
            "rejected_batch_id": rejected_batch_id,
            "corrected_batch_id": correction_batch_id,
        },
        "overview_a": overview_a["statistics"],
        "overview_b": overview_b["statistics"],
        "verified": [
            "duplicate-code-and-name-rejected",
            "import-and-analysis-project-isolation",
            "overview-exact-counts",
            "assignee-filter",
            "manual-edit-and-invalidate",
            "manual-list-persists-after-each-state-change",
            "rejected-source-row-corrections-preserve-raw-values",
            "project-report-and-export-isolation",
            "archive-read-only-and-restore",
            "empty-archived-delete-only",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
