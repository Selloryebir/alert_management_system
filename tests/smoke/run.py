#!/usr/bin/env python3
"""从空项目卷执行 Docker Compose G7 验收。"""

from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT / "compose.yaml"
BASE_URL = "http://127.0.0.1:8080"
EXPECTED_PATH = ROOT / "samples/expected/analysis-smoke-expected.json"
SERVICES = ("postgres", "algorithm", "backend")
HTTP_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
)
CSRF_HEADER: str | None = None
CSRF_TOKEN: str | None = None
BOOTSTRAP_PASSWORD: str | None = None


class VerificationError(RuntimeError):
    """G7 验收失败。"""


def assert_host_port_free() -> None:
    try:
        with socket.create_connection(("127.0.0.1", 8080), timeout=1):
            pass
    except OSError:
        return
    raise VerificationError("验收端口 127.0.0.1:8080 已被其他进程占用，请先停止现有服务。")


def run_command(
    command: list[str], *, timeout: int = 1200, check: bool = True
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if check and result.returncode != 0:
        raise VerificationError(
            f"命令失败（{result.returncode}）：{' '.join(command)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def discover_docker() -> str:
    configured = os.environ.get("DOCKER_BIN", "").strip()
    candidates = [configured, shutil.which("docker"), shutil.which("docker.exe")]
    if shutil.which("powershell.exe") and shutil.which("wslpath"):
        result = run_command(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                "(Get-Command docker -ErrorAction Stop).Source",
            ],
            timeout=15,
            check=False,
        )
        windows_path = result.stdout.strip().replace("\r", "")
        if result.returncode == 0 and windows_path:
            translated = run_command(
                ["wslpath", "-u", windows_path], timeout=10, check=False
            )
            if translated.returncode == 0:
                candidates.append(translated.stdout.strip())
    for candidate in candidates:
        if not candidate:
            continue
        result = run_command(
            [candidate, "version", "--format", "{{.Server.Version}}"],
            timeout=30,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return candidate
    raise VerificationError(
        "未找到可用的 Docker Engine；请启动 Docker Desktop 或安装 Docker Engine。"
    )


def docker_path(docker: str, path: Path) -> str:
    if docker.lower().endswith(".exe") and shutil.which("wslpath"):
        return run_command(["wslpath", "-w", str(path)], timeout=10).stdout.strip()
    return str(path)


class Compose:
    def __init__(self, docker: str, project: str) -> None:
        self.docker = docker
        self.project = project
        self.prefix = [
            docker,
            "compose",
            "--file",
            docker_path(docker, COMPOSE_FILE),
            "--project-name",
            project,
            "--project-directory",
            docker_path(docker, ROOT),
        ]

    def run(
        self, *arguments: str, timeout: int = 1200, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        return run_command(
            [*self.prefix, *arguments], timeout=timeout, check=check
        )

    def resource_ids(self, resource: str) -> list[str]:
        command_by_resource = {
            "container": ["ps", "--all"],
            "volume": ["volume", "ls"],
            "network": ["network", "ls"],
        }
        result = run_command(
            [
                self.docker,
                *command_by_resource[resource],
                "--filter",
                f"label=com.docker.compose.project={self.project}",
                "--format",
                "{{.ID}}" if resource != "volume" else "{{.Name}}",
            ],
            timeout=30,
        )
        return [line for line in result.stdout.splitlines() if line.strip()]

    def assert_no_resources(self) -> None:
        remaining = {
            resource: self.resource_ids(resource)
            for resource in ("container", "volume", "network")
        }
        if any(remaining.values()):
            raise VerificationError(f"Compose 项目清理后仍有资源：{remaining}")

    def service_state(self, service: str) -> dict[str, Any]:
        container_id = self.run("ps", "--quiet", service, timeout=30).stdout.strip()
        if not container_id:
            raise VerificationError(f"服务没有容器：{service}")
        payload = json.loads(
            run_command([self.docker, "inspect", container_id], timeout=30).stdout
        )[0]
        state = payload["State"]
        return {
            "container_id": container_id,
            "image_id": payload["Image"],
            "status": state["Status"],
            "health": state.get("Health", {}).get("Status"),
        }


def http_request(
    path: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 60,
    expect_json: bool = True,
) -> Any:
    request_headers = dict(headers or {})
    if method not in {"GET", "HEAD", "OPTIONS"} and CSRF_HEADER and CSRF_TOKEN:
        request_headers[CSRF_HEADER] = CSRF_TOKEN
    request = urllib.request.Request(
        BASE_URL + path,
        data=body,
        method=method,
        headers=request_headers,
    )
    try:
        with HTTP_OPENER.open(request, timeout=timeout) as response:
            payload = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise VerificationError(f"HTTP {exc.code} {method} {path}: {detail}") from exc
    except OSError as exc:
        raise VerificationError(f"无法访问 {method} {path}: {exc}") from exc
    if not expect_json:
        return payload
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError(f"接口未返回有效 JSON：{method} {path}") from exc


def json_request(path: str, method: str, value: dict[str, Any]) -> Any:
    return http_request(
        path,
        method=method,
        body=json.dumps(value, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )


def prepare_compose_secrets() -> Path:
    global BOOTSTRAP_PASSWORD
    configured = os.environ.get("APP_SECRETS_DIR", "").strip()
    secret_root = Path(configured) if configured else ROOT / ".runtime/compose-secrets"
    secret_root.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        secret_root.chmod(0o700)
    os.environ["APP_SECRETS_DIR"] = str(secret_root.resolve())
    for name in ("database-password.txt", "bootstrap-admin-password.txt"):
        path = secret_root / name
        if path.exists() and (not path.is_file() or not path.read_text(encoding="utf-8").strip()):
            raise VerificationError(f"Compose 密钥不是非空普通文件：{path}")
        if not path.exists():
            path.write_text(secrets.token_urlsafe(32), encoding="utf-8")
        if os.name != "nt":
            # Compose 的 Linux 绑定文件保留宿主 UID；目录 0700 限制宿主访问，
            # 文件需允许镜像内的非 root 用户 10001 读取。
            path.chmod(0o644)
    protect_secret_permissions(secret_root)
    BOOTSTRAP_PASSWORD = (secret_root / "bootstrap-admin-password.txt").read_text(
        encoding="utf-8"
    ).strip()
    return secret_root


def protect_secret_permissions(secret_root: Path) -> None:
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    windows_root: str | None = None
    if os.name == "nt":
        windows_root = str(secret_root.resolve())
    elif powershell and shutil.which("wslpath") and str(secret_root).startswith("/mnt/"):
        windows_root = run_command(
            ["wslpath", "-w", str(secret_root.resolve())], timeout=10
        ).stdout.strip()
    if windows_root is None:
        return
    if not powershell:
        raise VerificationError("Windows 密钥目录需要 PowerShell 才能收敛 NTFS ACL。")
    script_path = str(ROOT / "scripts/security/protect-windows-secrets.ps1")
    if os.name != "nt":
        script_path = run_command(
            ["wslpath", "-w", script_path], timeout=10
        ).stdout.strip()
    run_command(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            script_path,
            "-SecretRoot",
            windows_root,
        ],
        timeout=30,
    )


def login_as_bootstrap_admin() -> dict[str, Any]:
    global CSRF_HEADER, CSRF_TOKEN
    if not BOOTSTRAP_PASSWORD:
        raise VerificationError("尚未准备初始管理员密码")
    csrf = http_request("/api/v1/auth/csrf")
    CSRF_HEADER = csrf.get("header_name")
    CSRF_TOKEN = csrf.get("token")
    if not CSRF_HEADER or not CSRF_TOKEN:
        raise VerificationError(f"CSRF 初始化响应不完整：{csrf}")
    current = json_request(
        "/api/v1/auth/login",
        "POST",
        {"username": "admin", "password": BOOTSTRAP_PASSWORD},
    )
    if current.get("username") != "admin" or current.get("global_role") != "SYSTEM_ADMIN":
        raise VerificationError(f"初始管理员登录身份不正确：{current}")
    if current.get("must_change_password"):
        new_password = "M11-" + secrets.token_urlsafe(18)
        changed = json_request(
            "/api/v1/auth/password",
            "POST",
            {"current_password": BOOTSTRAP_PASSWORD, "new_password": new_password},
        )
        if changed.get("must_change_password"):
            raise VerificationError(f"首次改密后仍被要求改密：{changed}")
        current = json_request(
            "/api/v1/auth/login",
            "POST",
            {"username": "admin", "password": new_password},
        )
    return current


def assert_system_up() -> dict[str, Any]:
    health = http_request("/api/v1/health")
    if health.get("status") != "UP":
        raise VerificationError(f"聚合健康不是 UP：{health}")
    components = health.get("components", {})
    for name in ("system", "database", "algorithm"):
        if components.get(name, {}).get("status") != "UP":
            raise VerificationError(f"组件健康不是 UP：{name}={components.get(name)}")
    return health


def wait_for_health(expected: str, timeout: int = 120) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        try:
            last = http_request("/api/v1/health", timeout=3)
            if last.get("status") == expected:
                return last
        except VerificationError as exc:
            last = str(exc)
        time.sleep(2)
    raise VerificationError(f"聚合健康未在 {timeout} 秒内变为 {expected}：{last}")


def wait_for_services_healthy(compose: Compose, timeout: int = 60) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            last = {service: compose.service_state(service) for service in SERVICES}
            if all(
                state["status"] == "running" and state["health"] == "healthy"
                for state in last.values()
            ):
                return last
        except VerificationError:
            pass
        time.sleep(2)
    raise VerificationError(f"服务未在 {timeout} 秒内全部恢复 healthy：{last}")


def wait_for_service_health(
    compose: Compose, service: str, expected: str, timeout: int = 60
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = compose.service_state(service)
        if last["health"] == expected:
            return last
        time.sleep(2)
    raise VerificationError(
        f"服务 {service} 未在 {timeout} 秒内变为 {expected}：{last}"
    )


def assert_frontend() -> None:
    html = http_request("/", expect_json=False).decode("utf-8")
    match = re.search(r'<script[^>]+src="([^"]+\.js)"', html)
    if not match:
        raise VerificationError("首页没有 Vue 构建脚本。")
    script = http_request(match.group(1), expect_json=False).decode("utf-8")
    for marker in ("报警管理系统", "仅使用合成数据"):
        if marker not in script:
            raise VerificationError(f"Vue 构建产物缺少标识：{marker}")


def multipart_file(path: Path) -> tuple[bytes, str]:
    boundary = f"----alert-management-{uuid.uuid4().hex}"
    content_type = {
        ".csv": "text/csv",
        ".txt": "text/plain",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }[path.suffix.lower()]
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="project_id"\r\n\r\n',
            b"00000000-0000-0000-0000-000000000001\r\n",
            f"--{boundary}\r\n".encode(),
            (
                f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
            ).encode(),
            f"Content-Type: {content_type}\r\n\r\n".encode(),
            path.read_bytes(),
            f"\r\n--{boundary}--\r\n".encode(),
        ]
    )
    return body, boundary


def import_and_confirm(path: Path) -> str:
    body, boundary = multipart_file(path)
    preview = http_request(
        "/api/v1/imports/preview",
        method="POST",
        body=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
        },
        timeout=120,
    )
    if (preview.get("status"), preview.get("total_rows"), preview.get("valid_rows")) != (
        "READY",
        300,
        300,
    ):
        raise VerificationError(f"样例预览不符合预期：{path.name}: {preview}")
    batch_id = preview["batch_id"]
    confirmed = http_request(
        f"/api/v1/imports/{batch_id}/confirm", method="POST", timeout=120
    )
    if confirmed.get("status") != "IMPORTED" or confirmed.get("valid_rows") != 300:
        raise VerificationError(f"样例确认不符合预期：{path.name}: {confirmed}")
    return batch_id


def normalized_records(batch_id: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    page = 0
    while True:
        payload = http_request(
            f"/api/v1/imports/{batch_id}/records?page={page}&size=200"
        )
        records.extend(payload["items"])
        if len(records) >= payload["total"]:
            break
        page += 1
    if len(records) != 300:
        raise VerificationError(f"规范化记录数不是 300：{len(records)}")
    return [
        {key: value for key, value in record.items() if key != "raw_payload"}
        for record in records
    ]


def canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def verify_equivalent_imports() -> str:
    paths = [
        ROOT / "samples/smoke/synthetic_smoke_utf8.csv",
        ROOT / "samples/smoke/synthetic_smoke_utf8.txt",
        ROOT / "samples/smoke/synthetic_smoke.xlsx",
    ]
    records_by_format: list[list[dict[str, Any]]] = []
    for path in paths:
        records_by_format.append(normalized_records(import_and_confirm(path)))
    if records_by_format[1:] != [records_by_format[0], records_by_format[0]]:
        raise VerificationError("CSV/TXT/XLSX 的 300 条规范化结果不一致。")
    digest = canonical_digest(records_by_format[0])
    reset = json_request(
        "/api/v1/demo/reset",
        "POST",
        {"operator": "SYNTHETIC_G7_REVIEWER", "confirmation": "RESET_DEMO"},
    )
    if reset.get("business_state") != "EMPTY":
        raise VerificationError(f"AC-003 后复位未返回 EMPTY：{reset}")
    return digest


def instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def verify_analysis() -> tuple[str, dict[str, Any]]:
    expected = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))
    batch_id = import_and_confirm(ROOT / "samples/smoke/synthetic_smoke_utf8.csv")
    actual = http_request(
        f"/api/v1/imports/{batch_id}/analyses", method="POST", timeout=180
    )
    for key, value in (
        ("status", "COMPLETED"),
        ("contract_version", "v2"),
        ("algorithm_version", "0.2.0"),
        ("rule_version", expected["rule_version"]),
    ):
        if actual.get(key) != value:
            raise VerificationError(f"分析字段不符：{key}={actual.get(key)!r}")
    if actual.get("failure") is not None or actual.get("parameters") != expected["parameters"]:
        raise VerificationError(f"分析失败或规则参数漂移：{actual}")
    fields = ("source_row", "noise_type", "alarm_class", "cause_category")
    actual_records = [{key: row[key] for key in fields} for row in actual["results"]]
    if actual_records != expected["records"]:
        raise VerificationError("300 条分析分类投影与固定预期不一致。")
    if not all(row.get("evidence") for row in actual["results"]):
        raise VerificationError("分析结果存在空证据。")
    actual_chains = sorted(
        (
            tuple(member["source_row"] for member in chain["members"]),
            instant(chain["start_time"]),
            instant(chain["end_time"]),
            chain["association_rule"],
        )
        for chain in actual["event_chains"]
    )
    expected_chains = sorted(
        (
            tuple(chain["member_source_rows"]),
            instant(chain["start_time"]),
            instant(chain["end_time"]),
            chain["association_rule_category"],
        )
        for chain in expected["event_chains"]
    )
    if actual_chains != expected_chains:
        raise VerificationError("12 条关联事件链与固定预期不一致。")
    summary = actual["summary"]
    expected_summary = expected["summary"]
    expected_projection = {
        "input_count": 300,
        "success_count": 300,
        "failure_count": 0,
        "noise_type_counts": expected_summary["noise_type_counts"],
        "cause_category_counts": expected_summary["cause_category_counts"],
        "event_chain_count": expected_summary["event_chain_counts"]["total"],
    }
    if summary != expected_projection:
        raise VerificationError(f"分析摘要与共享固定预期不一致：{summary}")
    return actual["run_id"], summary


def verify_disposition(run_id: str) -> dict[str, int]:
    alarms = http_request(
        f"/api/v1/analyses/{run_id}/alarms?page=0&size=1&cause_category=EQUIPMENT_FAULT"
    )
    if alarms.get("total") != 30 or alarms["items"][0].get("source_row") != 222:
        raise VerificationError(f"未精确找到 source_row=222：{alarms}")
    record_id = alarms["items"][0]["record_id"]
    transitions = [
        ("IN_PROGRESS", "[SYNTHETIC] G7 开始处置"),
        ("CLOSED", "[SYNTHETIC] G7 完成审核"),
    ]
    for status, note in transitions:
        result = json_request(
            f"/api/v1/analyses/{run_id}/alarms/{record_id}/disposition",
            "PATCH",
            {"status": status, "operator": "SYNTHETIC_G7_REVIEWER", "note": note},
        )
        if result.get("status") != status:
            raise VerificationError(f"处置状态未变为 {status}：{result}")
    detail = http_request(f"/api/v1/analyses/{run_id}/alarms/{record_id}")
    history = detail.get("disposition_history", [])
    expected_pairs = [("OPEN", "IN_PROGRESS"), ("IN_PROGRESS", "CLOSED")]
    if [(row.get("from_status"), row.get("to_status")) for row in history] != expected_pairs:
        raise VerificationError(f"处置历史不完整：{history}")
    for row, (_, note) in zip(history, transitions, strict=True):
        if (
            row.get("operator") != "admin"
            or row.get("note") != note
            or not row.get("occurred_at")
        ):
            raise VerificationError(f"处置历史字段不完整：{row}")
    final_disposition = detail.get("disposition", {})
    if (
        final_disposition.get("status") != "CLOSED"
        or final_disposition.get("operator") != "admin"
        or final_disposition.get("note") != transitions[-1][1]
        or not final_disposition.get("updated_at")
        or not final_disposition.get("closed_at")
    ):
        raise VerificationError(f"最终处置字段不完整：{final_disposition}")
    dashboard = http_request(f"/api/v1/analyses/{run_id}/dashboard")
    disposition = dashboard.get("disposition_counts")
    if disposition != {"OPEN": 299, "IN_PROGRESS": 0, "CLOSED": 1}:
        raise VerificationError(f"处置摘要不符：{disposition}")
    query = urllib.parse.urlencode(
        {"page": 0, "size": 10, "event_type": "DISPOSITION_CHANGED", "target_id": record_id}
    )
    audit = http_request(f"/api/v1/audit-events?{query}")
    if audit.get("total") != 2 or len(audit.get("items", [])) != 2:
        raise VerificationError(f"处置审计不是两条：{audit}")
    required = {
        "event_id",
        "event_type",
        "occurred_at",
        "operator",
        "target_type",
        "target_id",
        "result",
        "trace_id",
        "details",
    }
    expected_notes = {
        ("OPEN", "IN_PROGRESS"): transitions[0][1],
        ("IN_PROGRESS", "CLOSED"): transitions[1][1],
    }
    actual_pairs = set()
    for row in audit["items"]:
        if not required.issubset(row) or any(row[key] in (None, "") for key in required):
            raise VerificationError(f"处置审计字段不完整：{row}")
        if (
            row["event_type"] != "DISPOSITION_CHANGED"
            or row["operator"] != "admin"
            or row["target_type"] != "ALARM_RECORD"
            or row["target_id"] != record_id
            or row["result"] != "SUCCESS"
        ):
            raise VerificationError(f"处置审计身份字段不符：{row}")
        pair = (row["details"].get("from_status"), row["details"].get("to_status"))
        if (
            row["details"].get("run_id") != run_id
            or row["details"].get("note") != expected_notes.get(pair)
        ):
            raise VerificationError(f"处置审计详情字段不符：{row}")
        actual_pairs.add(pair)
    if actual_pairs != set(expected_pairs):
        raise VerificationError(f"处置审计前后状态不符：{actual_pairs}")
    return disposition


def git_metadata() -> tuple[str | None, bool | None]:
    commit = run_command(["git", "rev-parse", "HEAD"], timeout=10, check=False)
    dirty = run_command(["git", "status", "--porcelain"], timeout=10, check=False)
    return (
        commit.stdout.strip() if commit.returncode == 0 else None,
        bool(dirty.stdout.strip()) if dirty.returncode == 0 else None,
    )


def save_diagnostics(compose: Compose, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    for name, arguments in (
        ("compose-ps.txt", ("ps", "--all")),
        ("compose-logs.txt", ("logs", "--no-color", "--timestamps")),
    ):
        result = compose.run(*arguments, timeout=120, check=False)
        (output / name).write_text(
            result.stdout + ("\nSTDERR:\n" + result.stderr if result.stderr else ""),
            encoding="utf-8",
        )


def verify_docker(fresh_volume: bool) -> dict[str, Any]:
    if not fresh_volume:
        raise VerificationError("G7 正式验收必须显式使用 --fresh-volume。")
    assert_host_port_free()
    secret_root = prepare_compose_secrets()
    docker = discover_docker()
    project = f"alert-management-g7-{os.getpid()}-{uuid.uuid4().hex[:8]}".lower()
    compose = Compose(docker, project)
    started = time.monotonic()
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    output = ROOT / ".runtime/m7" / f"{timestamp}-{project}"
    output.mkdir(parents=True, exist_ok=True)
    source_commit, source_dirty = git_metadata()
    summary: dict[str, Any] = {
        "status": "FAILED",
        "source_commit": source_commit,
        "source_dirty": source_dirty,
        "compose_project": project,
        "fresh_volume": True,
        "secret_root": str(secret_root),
    }
    primary_error: Exception | None = None
    diagnostic_error: Exception | None = None
    cleanup_error: Exception | None = None
    try:
        compose.run("config", "--quiet", timeout=60)
        compose.run("down", "--volumes", "--remove-orphans", timeout=120)
        compose.assert_no_resources()
        compose.run(
            "up", "--build", "--detach", "--wait", "--wait-timeout", "240", timeout=1800
        )
        states = {service: compose.service_state(service) for service in SERVICES}
        for service, state in states.items():
            if state["status"] != "running" or state["health"] != "healthy":
                raise VerificationError(f"容器未健康运行：{service}={state}")
        if len(compose.resource_ids("volume")) != 1:
            raise VerificationError("项目启动后必须且只能有一个项目卷。")
        assert_system_up()
        assert_frontend()
        login_as_bootstrap_admin()

        compose.run("stop", "algorithm", timeout=60)
        degraded = wait_for_health("DEGRADED", timeout=60)
        if degraded.get("components", {}).get("algorithm", {}).get("status") != "DOWN":
            raise VerificationError(f"算法停止后未显示 DOWN：{degraded}")
        try:
            assert_system_up()
        except VerificationError:
            pass
        else:
            raise VerificationError("正向健康断言错误接受了 DEGRADED 状态。")
        wait_for_service_health(compose, "backend", "unhealthy", timeout=60)
        compose.run("start", "algorithm", timeout=60)
        wait_for_health("UP", timeout=120)
        states = wait_for_services_healthy(compose)

        normalized_digest = verify_equivalent_imports()
        run_id, analysis_summary = verify_analysis()
        disposition_summary = verify_disposition(run_id)
        summary.update(
            {
                "images": states,
                "normalized_records_sha256": normalized_digest,
                "analysis_summary": analysis_summary,
                "disposition_counts": disposition_summary,
                "health_failure_injection": "PASS",
            }
        )
    except Exception as exc:  # 保存原始失败后仍执行限定项目清理
        primary_error = exc
        try:
            save_diagnostics(compose, output)
        except Exception as diagnostic_exc:
            diagnostic_error = diagnostic_exc
    finally:
        try:
            result = compose.run(
                "down", "--volumes", "--remove-orphans", timeout=180, check=False
            )
            if result.returncode != 0:
                raise VerificationError(f"Compose 清理失败：{result.stderr}")
            compose.assert_no_resources()
        except Exception as exc:
            cleanup_error = exc
            if primary_error is None:
                try:
                    save_diagnostics(compose, output)
                except Exception as diagnostic_exc:
                    diagnostic_error = diagnostic_exc
    summary["duration_seconds"] = round(time.monotonic() - started, 3)
    summary["cleanup"] = "PASS" if cleanup_error is None else "FAILED"
    errors = [
        str(error)
        for error in (primary_error, diagnostic_error, cleanup_error)
        if error is not None
    ]
    if errors:
        summary["errors"] = errors
    else:
        summary["status"] = "PASS"
    summary_path = output / "verification-summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"G7 验收摘要：{summary_path}")
    if errors:
        raise VerificationError("；".join(errors))
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("docker",), required=True)
    parser.add_argument("--fresh-volume", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        summary = verify_docker(args.fresh_volume)
    except (VerificationError, subprocess.TimeoutExpired) as exc:
        print(f"G7 验收失败：{exc}", file=sys.stderr)
        return 1
    print(
        "G7 验收通过：空卷启动、健康失败注入、三格式导入、固定分析、处置审计和资源清理均成功；"
        f"耗时 {summary['duration_seconds']} 秒。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
