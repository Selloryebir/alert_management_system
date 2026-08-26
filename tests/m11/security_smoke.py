#!/usr/bin/env python3
"""M11 身份、权限和输入边界黑盒验收。

只依赖 Python 3 标准库和公开 HTTP API。脚本不会启动进程、配置 TLS 或读取数据库，
因此可以同时用于原生包、Compose 和 CI 中已经启动的同源主系统。
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import http.cookiejar
from io import BytesIO
import json
from pathlib import Path
import re
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener
from uuid import uuid4
import zipfile


CHINESE = re.compile(r"[\u3400-\u9fff]")
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
LIMIT_ERRORS = (400, 413, 414, 422, 429)


def fail(message: str, detail: Any | None = None) -> None:
    if detail is None:
        raise AssertionError(message)
    if isinstance(detail, bytes):
        detail = detail.decode("utf-8", errors="replace")[:2000]
    raise AssertionError(f"{message}\n{json.dumps(detail, ensure_ascii=False, indent=2, default=str)}")


def identifier(payload: Any, field: str, context: str) -> str:
    if not isinstance(payload, dict) or not isinstance(payload.get(field), str) or not payload[field]:
        fail(f"{context} 缺少 {field}", payload)
    return payload[field]


def assert_chinese_error(payload: Any, context: str, code: str | None = None) -> None:
    if not isinstance(payload, dict):
        fail(f"{context} 错误响应不是 JSON 对象", payload)
    if code is not None and payload.get("code") != code:
        fail(f"{context} 错误码不是 {code}", payload)
    message = payload.get("message")
    if not isinstance(message, str) or not CHINESE.search(message):
        fail(f"{context} 没有返回中文错误", payload)
    if not isinstance(payload.get("code"), str) or not isinstance(payload.get("trace_id"), str):
        fail(f"{context} 缺少稳定错误码或 trace_id", payload)


class Api:
    """隔离 Cookie 的同源会话客户端。"""

    def __init__(self, base_url: str, label: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.label = label
        self.cookies = http.cookiejar.CookieJar()
        self.opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(self.cookies))
        self.csrf_header: str | None = None
        self.csrf_token: str | None = None

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Any | None = None,
        raw_body: bytes | None = None,
        content_type: str | None = None,
        headers: dict[str, str] | None = None,
        expected: tuple[int, ...] = (200,),
        csrf: bool = True,
        timeout: int = 120,
    ) -> tuple[int, dict[str, str], bytes]:
        method = method.upper()
        actual_headers = {"Accept": "application/json", **(headers or {})}
        data = raw_body
        if body is not None:
            if raw_body is not None:
                fail("body 与 raw_body 不能同时提供")
            data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            content_type = "application/json"
        if content_type is not None:
            actual_headers["Content-Type"] = content_type
        if csrf and method in MUTATING:
            if self.csrf_header is None or self.csrf_token is None:
                self.refresh_csrf()
            actual_headers[self.csrf_header or "X-XSRF-TOKEN"] = self.csrf_token or ""
        request = Request(self.base_url + path, method=method, data=data, headers=actual_headers)
        try:
            with self.opener.open(request, timeout=timeout) as response:
                status = response.status
                response_headers = self._headers(response.headers.items())
                raw = response.read()
        except HTTPError as error:
            status = error.code
            response_headers = self._headers(error.headers.items())
            raw = error.read()
        except (URLError, TimeoutError, ConnectionError) as error:
            fail(f"{self.label} 无法访问 {path}: {error}")
        if status not in expected:
            fail(
                f"{self.label}: {method} {path} 返回 HTTP {status}，预期 {expected}",
                raw,
            )
        return status, response_headers, raw

    def json(
        self,
        method: str,
        path: str,
        *,
        body: Any | None = None,
        raw_body: bytes | None = None,
        content_type: str | None = None,
        expected: tuple[int, ...] = (200,),
        csrf: bool = True,
    ) -> Any:
        status, _headers, raw = self.request(
            method,
            path,
            body=body,
            raw_body=raw_body,
            content_type=content_type,
            expected=expected,
            csrf=csrf,
        )
        if status == 204 and not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            fail(f"{self.label}: {method} {path} 未返回合法 JSON", raw)

    def refresh_csrf(self) -> dict[str, Any]:
        _status, _headers, raw = self.request("GET", "/api/v1/auth/csrf", csrf=False)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            fail(f"{self.label}: CSRF 初始化未返回 JSON", raw)
        self.csrf_header = payload.get("header_name")
        self.csrf_token = payload.get("token")
        if not isinstance(self.csrf_header, str) or not isinstance(self.csrf_token, str):
            fail(f"{self.label}: CSRF 响应缺少 token/header_name", payload)
        return payload

    def login(self, username: str, password: str, expected: tuple[int, ...] = (200,)) -> tuple[Any, dict[str, str]]:
        status, headers, raw = self.request(
            "POST",
            "/api/v1/auth/login",
            body={"username": username, "password": password},
            expected=expected,
        )
        try:
            return json.loads(raw), headers
        except json.JSONDecodeError:
            fail(f"{self.label}: 登录响应未返回 JSON (HTTP {status})", raw)

    def multipart(
        self,
        path: str,
        *,
        project_id: str,
        filename: str,
        content: bytes,
        fields: dict[str, str] | None = None,
        expected: tuple[int, ...] = (200,),
        timeout: int = 180,
    ) -> Any:
        boundary = f"----m11-{uuid4().hex}"
        parts = [self._field(boundary, "project_id", project_id)]
        for key, value in (fields or {}).items():
            parts.append(self._field(boundary, key, value))
        parts.extend([
            (
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
                f"filename=\"{filename}\"\r\nContent-Type: application/octet-stream\r\n\r\n"
            ).encode("utf-8"),
            content,
            f"\r\n--{boundary}--\r\n".encode("ascii"),
        ])
        _status, _headers, raw = self.request(
            "POST",
            path,
            raw_body=b"".join(parts),
            content_type=f"multipart/form-data; boundary={boundary}",
            expected=expected,
            timeout=timeout,
        )
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            fail(f"{self.label}: {path} 未返回合法 JSON", raw)

    @staticmethod
    def _field(boundary: str, name: str, value: str) -> bytes:
        return (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n"
        ).encode("utf-8")

    @staticmethod
    def _headers(items: Any) -> dict[str, str]:
        result: dict[str, str] = {}
        for key, value in items:
            normalized = key.lower()
            result[normalized] = value if normalized not in result else result[normalized] + "\n" + value
        return result


class Evidence:
    def __init__(self, output_dir: Path, base_url: str) -> None:
        self.output_dir = output_dir
        self.path = output_dir / "m11-security-results.json"
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.base_url = base_url
        self.checks: list[dict[str, Any]] = []

    def check(self, name: str, action: Callable[[], Any]) -> Any:
        result = action()
        item: dict[str, Any] = {"name": name, "status": "passed"}
        if isinstance(result, dict):
            item["evidence"] = result
        self.checks.append(item)
        self.write("in_progress")
        print(f"[通过] {name}")
        return result

    def write(self, status: str, error: str | None = None) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "stage": "M11",
            "status": status,
            "base_url": self.base_url,
            "started_at": self.started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "checks": self.checks,
        }
        if error is not None:
            payload["error"] = error[:2000]
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)


def derived_password(purpose: str, seed: str) -> str:
    digest = hashlib.sha256(f"{purpose}:{seed}".encode("utf-8")).hexdigest()[:28]
    return f"M11!{digest}"


def expect_error(
    api: Api,
    method: str,
    path: str,
    *,
    body: Any | None = None,
    raw_body: bytes | None = None,
    content_type: str | None = None,
    expected: tuple[int, ...],
    context: str,
    code: str | None = None,
    csrf: bool = True,
) -> dict[str, Any]:
    payload = api.json(
        method,
        path,
        body=body,
        raw_body=raw_body,
        content_type=content_type,
        expected=expected,
        csrf=csrf,
    )
    assert_chinese_error(payload, context, code)
    return payload


def create_project(api: Api, suffix: str, marker: str, *, hostile: bool = False) -> dict[str, Any]:
    sql = "'; DROP TABLE business_project;--" if hostile else marker
    xss = "<script>alert('m11')</script>" if hostile else f"{marker}客户"
    traversal = "../../Windows/System32" if hostile else f"{marker}厂区"
    body = {
        "code": f"M11-{marker}-{suffix}",
        "name": f"{marker}-{sql}-{suffix}",
        "client_name": xss,
        "site": traversal,
        "unit_name": f"{marker}装置",
        "report_title": f"{marker}报警分析报告",
        "report_fields": ["summary", "priority", "noise", "cause", "disposition", "chains"],
    }
    payload = api.json("POST", "/api/v1/projects", body=body, expected=(200, 201))
    project_id = identifier(payload, "project_id", f"创建项目 {marker}")
    for field in ("code", "name", "client_name", "site", "unit_name"):
        if payload.get(field) != body[field]:
            fail(f"项目字段 {field} 没有按文本保存", payload)
    payload["project_id"] = project_id
    payload["request"] = body
    return payload


def create_user(api: Api, username: str, display_name: str) -> dict[str, Any]:
    password = derived_password("temporary", username)
    payload = api.json(
        "POST",
        "/api/v1/admin/users",
        body={
            "username": username,
            "display_name": display_name,
            "password": password,
            "global_role": "NONE",
        },
        expected=(200, 201),
    )
    identifier(payload, "user_id", f"创建账号 {username}")
    if payload.get("username") != username or payload.get("must_change_password") is not True:
        fail(f"账号 {username} 初始状态错误", payload)
    payload["temporary_password"] = password
    payload["active_password"] = derived_password("active", username)
    return payload


def activate_user(base_url: str, user: dict[str, Any], label: str) -> Api:
    api = Api(base_url, label)
    login, _headers = api.login(user["username"], user["temporary_password"])
    if login.get("must_change_password") is not True:
        fail(f"{label} 首次登录没有要求改密", login)
    blocked = expect_error(
        api,
        "GET",
        "/api/v1/projects",
        expected=(403,),
        context=f"{label} 首次改密门禁",
        code="PASSWORD_CHANGE_REQUIRED",
    )
    if "修改密码" not in blocked.get("message", ""):
        fail(f"{label} 首次改密门禁提示不可操作", blocked)
    changed = api.json(
        "POST",
        "/api/v1/auth/password",
        body={
            "current_password": user["temporary_password"],
            "new_password": user["active_password"],
        },
    )
    if changed.get("must_change_password") is not False:
        fail(f"{label} 改密响应错误", changed)
    expect_error(api, "GET", "/api/v1/auth/me", expected=(401,), context=f"{label} 改密会话失效")
    api.login(user["username"], user["active_password"])
    return api


def add_member(admin: Api, project_id: str, user_id: str, role: str) -> None:
    payload = admin.json(
        "PUT",
        f"/api/v1/projects/{project_id}/members/{user_id}",
        body={"project_role": role},
    )
    if payload.get("user_id") != user_id or payload.get("project_role") != role:
        fail("成员角色保存错误", payload)


def csv_fixture(prefix: str, rows: int = 6, *, invalid_priority: bool = False, cell: str | None = None) -> bytes:
    header = (
        "event_time,site,area,unit,tag,description,priority,state,value,threshold,"
        "engineering_unit,source_system,operator\n"
    )
    lines = [header]
    for index in range(rows):
        priority = "P9" if invalid_priority else ("P1" if index % 2 == 0 else "P2")
        description = cell if cell is not None else f"{prefix}报警{index}"
        state = "ACTIVE" if index % 2 == 0 else "RETURNED"
        lines.append(
            f"2026-08-26T09:{index % 60:02d}:00+08:00,{prefix}厂区,{prefix}区域,{prefix}装置,"
            f"{prefix}-TAG-{index},{description},{priority},{state},{10 + index},12,MPa,"
            f"M11_{prefix},源操作员{index}\n"
        )
    return "".join(lines).encode("utf-8")


def import_and_analyze(api: Api, project_id: str, marker: str) -> dict[str, str]:
    preview = api.multipart(
        "/api/v1/imports/preview",
        project_id=project_id,
        filename=f"{marker}.csv",
        content=csv_fixture(marker),
    )
    batch_id = identifier(preview, "batch_id", f"{marker} 导入预览")
    if preview.get("status") != "READY" or preview.get("error_count") != 0:
        fail(f"{marker} 导入预览未通过", preview)
    confirmed = api.json("POST", f"/api/v1/imports/{batch_id}/confirm")
    if confirmed.get("status") != "IMPORTED":
        fail(f"{marker} 导入确认失败", confirmed)
    run = api.json("POST", f"/api/v1/imports/{batch_id}/analyses", expected=(200, 201))
    run_id = identifier(run, "run_id", f"{marker} 分析")
    if run.get("status") != "COMPLETED" or not run.get("results"):
        fail(f"{marker} 分析没有成功形成结果", run)
    record_id = identifier(run["results"][0], "record_id", f"{marker} 分析首条结果")
    return {"batch_id": batch_id, "run_id": run_id, "record_id": record_id}


def xlsx_with_sheets(sheet_count: int) -> bytes:
    """生成 POI 可打开的最小 XLSX；无需第三方库。"""
    relationships = []
    sheets = []
    overrides = []
    output = BytesIO()
    for index in range(1, sheet_count + 1):
        relationships.append(
            f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/'
            f'officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>'
        )
        sheets.append(f'<sheet name="S{index}" sheetId="{index}" r:id="rId{index}"/>')
        overrides.append(
            f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        )
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            + "".join(overrides)
            + "</Types>",
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        )
        archive.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets>{"".join(sheets)}</sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(relationships)
            + "</Relationships>",
        )
        sheet = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>event_time</t></is></c></row>'
            '</sheetData></worksheet>'
        )
        for index in range(1, sheet_count + 1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", sheet)
    return output.getvalue()


def batch_ids(api: Api, project_id: str) -> set[str]:
    payload = api.json("GET", f"/api/v1/imports?{urlencode({'project_id': project_id, 'limit': 100})}")
    if not isinstance(payload, list):
        fail("批次列表响应不是数组", payload)
    return {identifier(item, "batch_id", "批次列表项") for item in payload}


def corrections_fixture(count: int) -> str:
    return json.dumps(
        {str(source_row): {"description": f"第 {source_row} 行修正"} for source_row in range(2, count + 2)},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def boundary_case(
    admin: Api,
    project_id: str,
    name: str,
    *,
    filename: str,
    content: bytes,
    fields: dict[str, str] | None = None,
    expected_code: str | None = None,
) -> dict[str, Any]:
    before = batch_ids(admin, project_id)
    payload = admin.multipart(
        "/api/v1/imports/preview",
        project_id=project_id,
        filename=filename,
        content=content,
        fields=fields,
        expected=LIMIT_ERRORS,
        timeout=240,
    )
    assert_chinese_error(payload, name, expected_code)
    after = batch_ids(admin, project_id)
    if after != before:
        fail(f"{name} 失败后留下了半批次", {"before": sorted(before), "after": sorted(after)})
    return {"http_error_code": payload.get("code"), "zero_partial_batch": True}


def main() -> int:
    parser = argparse.ArgumentParser(description="M11 身份权限和输入边界黑盒验收")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--bootstrap-username", default="admin")
    parser.add_argument("--bootstrap-password-file", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    evidence = Evidence(args.output_dir.resolve(), args.base_url)
    try:
        password_file = args.bootstrap_password_file.resolve()
        if not password_file.is_file():
            fail(f"首个管理员密码文件不存在：{password_file}")
        bootstrap_password = password_file.read_text(encoding="utf-8").strip()
        if not bootstrap_password:
            fail("首个管理员密码文件为空")

        anonymous = Api(args.base_url, "匿名会话")

        def anonymous_check() -> dict[str, Any]:
            payload = expect_error(
                anonymous,
                "GET",
                "/api/v1/projects",
                expected=(401,),
                context="匿名业务访问",
                code="AUTH_REQUIRED",
            )
            _status, headers, _raw = anonymous.request("GET", "/api/v1/health")
            required_headers = {
                "content-security-policy": "default-src 'self'",
                "x-content-type-options": "nosniff",
                "x-frame-options": "DENY",
                "referrer-policy": "same-origin",
            }
            missing = [key for key, marker in required_headers.items() if marker.lower() not in headers.get(key, "").lower()]
            if missing:
                fail("安全响应头不完整", {"missing": missing, "headers": headers})
            return {"status": 401, "code": payload["code"], "security_headers": sorted(required_headers)}

        evidence.check("匿名 401 与安全响应头", anonymous_check)

        admin = Api(args.base_url, "系统管理员")
        active_admin_password = derived_password("admin-active", bootstrap_password)

        def bootstrap_check() -> dict[str, Any]:
            login, headers = admin.login(args.bootstrap_username, bootstrap_password, expected=(200, 401))
            if isinstance(login, dict) and login.get("code"):
                assert_chinese_error(login, "首个管理员登录")
                login, headers = admin.login(args.bootstrap_username, active_admin_password)
            if login.get("global_role") != "SYSTEM_ADMIN":
                fail("首个账号不是系统管理员", login)
            cookie = headers.get("set-cookie", "")
            if "JSESSIONID" not in cookie or "HttpOnly" not in cookie or "SameSite=Lax" not in cookie:
                fail("会话 Cookie 缺少 JSESSIONID/HttpOnly/SameSite=Lax", {"set-cookie": cookie})
            if login.get("must_change_password") is True:
                expect_error(
                    admin,
                    "GET",
                    "/api/v1/projects",
                    expected=(403,),
                    context="管理员首次改密门禁",
                    code="PASSWORD_CHANGE_REQUIRED",
                )
                admin.json(
                    "POST",
                    "/api/v1/auth/password",
                    body={"current_password": bootstrap_password, "new_password": active_admin_password},
                )
                expect_error(admin, "GET", "/api/v1/auth/me", expected=(401,), context="管理员改密会话失效")
                login, _headers = admin.login(args.bootstrap_username, active_admin_password)
            if login.get("must_change_password") is not False:
                fail("管理员未完成首次改密", login)
            return {"username": login.get("username"), "global_role": login.get("global_role")}

        evidence.check("首个管理员、首次改密与 Cookie", bootstrap_check)

        suffix = uuid4().hex[:8]
        blocked_code = f"M11-CSRF-{suffix}"

        def csrf_check() -> dict[str, Any]:
            payload = expect_error(
                admin,
                "POST",
                "/api/v1/projects",
                body={
                    "code": blocked_code,
                    "name": blocked_code,
                    "client_name": "CSRF负控",
                    "site": "负控厂区",
                    "unit_name": "负控装置",
                },
                expected=(403,),
                context="缺少 CSRF",
                code="CSRF_INVALID",
                csrf=False,
            )
            projects = admin.json("GET", f"/api/v1/projects?{urlencode({'q': blocked_code})}")
            if projects:
                fail("CSRF 失败请求仍创建了项目", projects)
            return {"status": 403, "code": payload["code"], "zero_partial_project": True}

        evidence.check("CSRF 403 与零半落库", csrf_check)

        project_a = evidence.check("创建项目 A", lambda: create_project(admin, suffix, "A"))
        project_b = evidence.check("创建项目 B", lambda: create_project(admin, suffix, "B"))
        hostile_project = evidence.check(
            "SQL/XSS/路径文本按普通文本处理",
            lambda: create_project(admin, suffix, "TEXT", hostile=True),
        )
        project_a_id = project_a["project_id"]
        project_b_id = project_b["project_id"]
        hostile_id = hostile_project["project_id"]

        def text_query_check() -> dict[str, Any]:
            sql_marker = "'; DROP TABLE business_project;--"
            results = admin.json("GET", f"/api/v1/projects?{urlencode({'q': sql_marker})}")
            ids = {item.get("project_id") for item in results}
            if ids != {hostile_id}:
                fail("SQL 负控查询出现注入或错误匹配", results)
            fetched = admin.json("GET", f"/api/v1/projects/{hostile_id}")
            if fetched.get("client_name") != "<script>alert('m11')</script>" or fetched.get("site") != "../../Windows/System32":
                fail("XSS/路径文本未按原始文本返回", fetched)
            return {"parameterized_query": True, "text_round_trip": True}

        evidence.check("参数化查询与危险文本往返", text_query_check)

        manager_user = create_user(admin, f"manager.{suffix}", "项目负责人甲")
        analyst_user = create_user(admin, f"analyst.{suffix}", "分析人员甲")
        foreign_user = create_user(admin, f"foreign.{suffix}", "项目负责人乙")
        lock_user = create_user(admin, f"locked.{suffix}", "锁定测试账号")
        for project_id, user, role in (
            (project_a_id, manager_user, "MANAGER"),
            (project_a_id, analyst_user, "ANALYST"),
            (project_b_id, foreign_user, "MANAGER"),
        ):
            add_member(admin, project_id, user["user_id"], role)

        manager = evidence.check(
            "负责人首次改密",
            lambda: activate_user(args.base_url, manager_user, "项目负责人甲"),
        )
        analyst = evidence.check(
            "分析人员首次改密",
            lambda: activate_user(args.base_url, analyst_user, "分析人员甲"),
        )
        foreign = evidence.check(
            "跨项目负责人首次改密",
            lambda: activate_user(args.base_url, foreign_user, "项目负责人乙"),
        )

        def lockout_check() -> dict[str, Any]:
            lock_client = Api(args.base_url, "锁定测试账号")
            for attempt in range(1, 6):
                payload, _headers = lock_client.login(lock_user["username"], "Wrong-Password!", expected=(401,))
                assert_chinese_error(payload, f"第 {attempt} 次错误登录", "LOGIN_FAILED")
            payload, _headers = lock_client.login(
                lock_user["username"], lock_user["temporary_password"], expected=(401,)
            )
            assert_chinese_error(payload, "锁定后正确密码登录", "LOGIN_FAILED")
            listed = admin.json("GET", "/api/v1/admin/users")
            locked = next((item for item in listed if item.get("user_id") == lock_user["user_id"]), None)
            if not locked or not locked.get("locked_until"):
                fail("五次失败后账号没有持久化锁定时间", locked)
            reset_password = derived_password("lock-reset", lock_user["username"])
            admin.json(
                "POST",
                f"/api/v1/admin/users/{lock_user['user_id']}/reset-password",
                body={"new_password": reset_password},
            )
            login, _headers = lock_client.login(lock_user["username"], reset_password)
            if login.get("must_change_password") is not True:
                fail("管理员重置后没有解除锁定并要求改密", login)
            return {"failed_attempts": 5, "locked": True, "reset_unlocked": True}

        evidence.check("五次失败锁定与管理员解锁", lockout_check)

        def role_matrix_check() -> dict[str, Any]:
            if manager.json("GET", f"/api/v1/projects/{project_a_id}").get("project_role") != "MANAGER":
                fail("负责人未取得 MANAGER 项目职责")
            members = manager.json("GET", f"/api/v1/projects/{project_a_id}/members")
            if not any(item.get("user_id") == analyst_user["user_id"] for item in members):
                fail("负责人无法读取所属项目成员", members)
            manager.json("PATCH", f"/api/v1/projects/{project_a_id}", body={"report_title": "M11负责人报告"})
            expect_error(
                manager,
                "GET",
                "/api/v1/admin/users",
                expected=(403,),
                context="负责人访问账号管理",
                code="PERMISSION_DENIED",
            )
            if analyst.json("GET", f"/api/v1/projects/{project_a_id}").get("project_role") != "ANALYST":
                fail("分析人员未取得 ANALYST 项目职责")
            expect_error(
                analyst,
                "PATCH",
                f"/api/v1/projects/{project_a_id}",
                body={"report_title": "不应保存"},
                expected=(403,),
                context="分析人员修改项目",
                code="PERMISSION_DENIED",
            )
            expect_error(
                analyst,
                "GET",
                f"/api/v1/projects/{project_a_id}/members",
                expected=(403,),
                context="分析人员读取成员管理",
                code="PERMISSION_DENIED",
            )
            expect_error(
                analyst,
                "GET",
                f"/api/v1/audit-events?{urlencode({'project_id': project_a_id})}",
                expected=(403,),
                context="分析人员读取审计",
                code="PERMISSION_DENIED",
            )
            if foreign.json("GET", f"/api/v1/projects/{project_b_id}").get("project_role") != "MANAGER":
                fail("跨项目负责人未取得项目 B 职责")
            return {"roles": ["SYSTEM_ADMIN", "MANAGER", "ANALYST"], "forbidden_actions": 4}

        evidence.check("系统管理员/负责人/分析人员角色矩阵", role_matrix_check)

        facts_a = evidence.check(
            "分析人员完成项目 A 导入分析",
            lambda: import_and_analyze(analyst, project_a_id, f"M11A{suffix}"),
        )
        facts_b = evidence.check(
            "系统管理员完成项目 B 导入分析",
            lambda: import_and_analyze(admin, project_b_id, f"M11B{suffix}"),
        )

        def idor_check() -> dict[str, Any]:
            b_before = admin.json("GET", f"/api/v1/projects/{project_b_id}")
            record_before = admin.json(
                "GET", f"/api/v1/analyses/{facts_b['run_id']}/alarms/{facts_b['record_id']}"
            )
            denials = [
                expect_error(manager, "GET", f"/api/v1/projects/{project_b_id}", expected=(404,), context="跨项目 project UUID"),
                expect_error(manager, "GET", f"/api/v1/imports/{facts_b['batch_id']}", expected=(404,), context="跨项目 batch UUID"),
                expect_error(manager, "GET", f"/api/v1/analyses/{facts_b['run_id']}", expected=(404,), context="跨项目 run UUID"),
                expect_error(
                    manager,
                    "GET",
                    f"/api/v1/analyses/{facts_b['run_id']}/alarms/{facts_b['record_id']}",
                    expected=(404,),
                    context="跨项目 record UUID",
                ),
                expect_error(
                    manager,
                    "POST",
                    f"/api/v1/analyses/{facts_b['run_id']}/reports/pdf",
                    body={"operator": "伪造导出人"},
                    expected=(404,),
                    context="跨项目 report UUID",
                ),
                expect_error(
                    manager,
                    "PATCH",
                    f"/api/v1/projects/{project_b_id}",
                    body={"report_title": "越权写入"},
                    expected=(404,),
                    context="跨项目修改",
                ),
            ]
            for payload in denials:
                assert_chinese_error(payload, "跨项目 UUID", "RESOURCE_NOT_FOUND")
            b_after = admin.json("GET", f"/api/v1/projects/{project_b_id}")
            record_after = admin.json(
                "GET", f"/api/v1/analyses/{facts_b['run_id']}/alarms/{facts_b['record_id']}"
            )
            if b_after.get("report_title") != b_before.get("report_title") or record_after != record_before:
                fail("跨项目拒绝后业务事实发生变化", {"project": b_after, "record": record_after})
            return {"resources": ["project", "batch", "run", "record", "report"], "zero_unauthorized_write": True}

        evidence.check("跨项目 UUID/IDOR 与零越权写入", idor_check)

        def actor_check() -> dict[str, Any]:
            detail = analyst.json(
                "GET", f"/api/v1/analyses/{facts_a['run_id']}/alarms/{facts_a['record_id']}"
            )
            current_noise = detail.get("noise_type")
            target_noise = "NORMAL" if current_noise != "NORMAL" else "DUPLICATE"
            changed = analyst.json(
                "PATCH",
                f"/api/v1/analyses/{facts_a['run_id']}/alarms/{facts_a['record_id']}/classification",
                body={
                    "noise_type": target_noise,
                    "alarm_class": "ACTIONABLE",
                    "cause_category": "INSTRUMENT_ISSUE",
                    "operator": "伪造管理员<script>",
                    "reason": "M11 验证真实会话操作者",
                },
            )
            override = changed.get("classification_override") or {}
            if override.get("operator") != analyst_user["display_name"] or "伪造管理员" in override.get("operator", ""):
                fail("分类修订采用了请求体伪造 actor", changed)
            disposition = manager.json(
                "PATCH",
                f"/api/v1/analyses/{facts_a['run_id']}/alarms/{facts_a['record_id']}/disposition",
                body={
                    "status": "IN_PROGRESS",
                    "operator": "伪造处置人",
                    "assignee": analyst_user["username"],
                    "note": "M11 处置 actor 验证",
                },
            )
            if disposition.get("operator") != manager_user["display_name"]:
                fail("处置采用了请求体伪造 actor", disposition)
            audit = admin.json(
                "GET",
                f"/api/v1/audit-events?{urlencode({'event_type': 'RESULT_OVERRIDDEN', 'target_id': facts_a['record_id']})}",
            )
            events = audit.get("items") or []
            if not events or events[0].get("actor_user_id") != analyst_user["user_id"]:
                fail("审计没有记录真实 actor_user_id", audit)
            _status, _headers, pdf = analyst.request(
                "POST",
                f"/api/v1/analyses/{facts_a['run_id']}/reports/pdf",
                body={"operator": "伪造报告人"},
            )
            if not pdf.startswith(b"%PDF"):
                fail("授权报告不是可识别 PDF")
            return {"classification_actor": analyst_user["display_name"], "disposition_actor": manager_user["display_name"]}

        evidence.check("伪造 actor 被忽略且审计关联真实账号", actor_check)

        def source_operator_check() -> dict[str, Any]:
            payload = analyst.json(
                "POST",
                f"/api/v1/projects/{project_a_id}/manual-alarms",
                body={
                    "event_time": "2026-08-26T10:00:00+08:00",
                    "site": "<script>业务厂区</script>",
                    "area": "'; SELECT pg_sleep(30);--",
                    "unit": "../../业务装置",
                    "tag": f"M11-MANUAL-{suffix}",
                    "description": "<img src=x onerror=alert(1)>",
                    "priority": "P2",
                    "state": "ACTIVE",
                    "value": 12.5,
                    "threshold": 10,
                    "engineering_unit": "MPa",
                    "source_system": "M11_MANUAL",
                    "operator": "源操作员<script>",
                },
                expected=(200, 201),
            )
            if payload.get("operator") != "源操作员<script>" or payload.get("description") != "<img src=x onerror=alert(1)>":
                fail("报警事实字段被误当成登录 actor 或执行为标记", payload)
            # 注入文本写入后仍能读取项目，证明参数化查询没有执行第二条语句。
            analyst.json("GET", f"/api/v1/projects/{project_a_id}")
            return {"source_operator_preserved": True, "hostile_text_inert": True}

        evidence.check("源操作员与 SQL/XSS/路径业务文本隔离", source_operator_check)

        def session_invalidation_check() -> dict[str, Any]:
            manager.json("POST", "/api/v1/auth/logout")
            expect_error(manager, "GET", "/api/v1/auth/me", expected=(401,), context="退出后会话失效")
            manager.login(manager_user["username"], manager_user["active_password"])

            analyst_reset = derived_password("admin-reset", analyst_user["username"])
            admin.json(
                "POST",
                f"/api/v1/admin/users/{analyst_user['user_id']}/reset-password",
                body={"new_password": analyst_reset},
            )
            expect_error(analyst, "GET", "/api/v1/auth/me", expected=(401,), context="管理员重置后旧会话失效")
            reset_login, _headers = analyst.login(analyst_user["username"], analyst_reset)
            if reset_login.get("must_change_password") is not True:
                fail("重置密码后未要求首次改密", reset_login)

            admin.json(
                "PATCH",
                f"/api/v1/admin/users/{foreign_user['user_id']}",
                body={"status": "DISABLED"},
            )
            expect_error(foreign, "GET", "/api/v1/auth/me", expected=(401,), context="停用账号后旧会话失效")
            return {"logout": True, "password_reset": True, "account_disable": True}

        evidence.check("退出/重置/停用立即使会话失效", session_invalidation_check)

        # 一个成功的路径型文件名，用来证明文件名只作为事实文本处理，不参与磁盘路径。
        path_preview = admin.multipart(
            "/api/v1/imports/preview",
            project_id=project_a_id,
            filename="../../m11-path.csv",
            content=csv_fixture("PATH", rows=1),
        )
        identifier(path_preview, "batch_id", "路径型文件名导入")
        evidence.check(
            "路径型上传文件名不影响服务",
            lambda: {"status": path_preview.get("status"), "health": admin.json("GET", "/api/v1/health").get("status")},
        )

        def query_limit_check() -> dict[str, Any]:
            # 使用 ASCII 保证请求行仍低于常见容器的 8 KiB 上限，使拒绝确实来自应用的 2 KiB 契约。
            query = "q=" + ("a" * 2049)
            payload = expect_error(
                admin,
                "GET",
                f"/api/v1/projects?{query}",
                expected=(414,),
                context="2 KiB 查询上限",
                code="QUERY_TOO_LARGE",
            )
            return {"status": 414, "code": payload["code"]}

        evidence.check("查询字符串 2 KiB 上限", query_limit_check)

        def json_limit_check() -> dict[str, Any]:
            before = len(admin.json("GET", "/api/v1/admin/users"))
            oversized = json.dumps(
                {
                    "username": f"oversized.{suffix}",
                    "display_name": "大" * (1024 * 1024),
                    "password": derived_password("oversized", suffix),
                    "global_role": "NONE",
                },
                ensure_ascii=False,
            ).encode("utf-8")
            payload = expect_error(
                admin,
                "POST",
                "/api/v1/admin/users",
                raw_body=oversized,
                content_type="application/json",
                expected=(413,),
                context="1 MiB JSON 上限",
                code="REQUEST_BODY_TOO_LARGE",
            )
            after = len(admin.json("GET", "/api/v1/admin/users"))
            if after != before:
                fail("超大 JSON 失败后仍创建了账号", {"before": before, "after": after})
            return {"status": 413, "code": payload["code"], "zero_partial_user": True}

        evidence.check("普通 JSON 1 MiB 上限", json_limit_check)

        # 每个导入负控都在前后读取批次列表，拒绝“报错但仍留下半批次”。
        evidence.check(
            "字段映射 32 KiB 上限",
            lambda: boundary_case(
                admin,
                project_a_id,
                "字段映射 32 KiB 上限",
                filename="mapping.csv",
                content=csv_fixture("MAP", rows=1),
                fields={"mapping": json.dumps({"description": "x" * (32 * 1024 + 1)})},
            ),
        )
        evidence.check(
            "行修正 1 MiB 上限",
            lambda: boundary_case(
                admin,
                project_a_id,
                "行修正 1 MiB 上限",
                filename="corrections.csv",
                content=csv_fixture("CORR", rows=1),
                fields={"corrections": json.dumps({"2": {"description": "修" * (1024 * 1024)}})},
            ),
        )
        evidence.check(
            "行修正 1000 行上限",
            lambda: boundary_case(
                admin,
                project_a_id,
                "行修正 1000 行上限",
                filename="too-many-corrections.csv",
                content=csv_fixture("CORRROWS", rows=1001),
                fields={"corrections": corrections_fixture(1001)},
            ),
        )
        evidence.check(
            "单文件 50 MiB 上限",
            lambda: boundary_case(
                admin,
                project_a_id,
                "单文件 50 MiB 上限",
                filename="oversized.csv",
                content=b"0" * (50 * 1024 * 1024 + 1),
            ),
        )
        evidence.check(
            "数据行 100000 上限",
            lambda: boundary_case(
                admin,
                project_a_id,
                "数据行 100000 上限",
                filename="too-many-rows.csv",
                content=csv_fixture("ROWS", rows=100_001),
            ),
        )
        evidence.check(
            "列数 256 上限",
            lambda: boundary_case(
                admin,
                project_a_id,
                "列数 256 上限",
                filename="too-many-columns.csv",
                content=((",".join(f"h{i}" for i in range(257)) + "\n") + (",".join("v" for _ in range(257)) + "\n")).encode(),
            ),
        )
        evidence.check(
            "表头 120 字符上限",
            lambda: boundary_case(
                admin,
                project_a_id,
                "表头 120 字符上限",
                filename="long-header.csv",
                content=(("h" * 121) + "\nvalue\n").encode(),
            ),
        )
        evidence.check(
            "单元格 4096 字符上限",
            lambda: boundary_case(
                admin,
                project_a_id,
                "单元格 4096 字符上限",
                filename="long-cell.csv",
                content=csv_fixture("CELL", rows=1, cell="单" * 4097),
            ),
        )
        evidence.check(
            "XLSX 工作表 8 个上限",
            lambda: boundary_case(
                admin,
                project_a_id,
                "XLSX 工作表 8 个上限",
                filename="nine-sheets.xlsx",
                content=xlsx_with_sheets(9),
            ),
        )
        evidence.check(
            "校验错误最多保存 1000 个",
            lambda: boundary_case(
                admin,
                project_a_id,
                "校验错误最多保存 1000 个",
                filename="error-limit.csv",
                content=csv_fixture("ERRORLIMIT", rows=1001, invalid_priority=True),
                expected_code="IMPORT_ERROR_LIMIT",
            ),
        )
        evidence.check(
            "超过 200 个可修正错误行拒绝在线修正",
            lambda: boundary_case(
                admin,
                project_a_id,
                "超过 200 个可修正错误行",
                filename="too-many-errors.csv",
                content=csv_fixture("ERRORS", rows=201, invalid_priority=True),
            ),
        )

        evidence.write("passed")
        print(f"M11 黑盒验收通过，证据：{evidence.path}")
        return 0
    except Exception as error:
        evidence.write("failed", str(error))
        print(f"M11 黑盒验收失败：{error}")
        print(f"失败证据：{evidence.path}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
