#!/usr/bin/env python3
"""M12 恢复后运行质量、有限资源趋势和清理边界验收。"""

from __future__ import annotations

import hashlib
import importlib.util
import ipaddress
import json
import math
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from functools import cache
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / ".runtime"
RESULT_ROOT = RUNTIME / "reliability" / "results"
LOG_ROOT = RUNTIME / "logs"
PID_ROOT = RUNTIME / "pids"
PORTS = (55432, 8001, 8080)
DEFAULT_PROJECT_ID = "00000000-0000-0000-0000-000000000001"
MEMORY_LIMIT_MIB = {"backend": 1536.0, "algorithm": 1024.0, "postgres": 1536.0}
MOJIBAKE_PATTERN = re.compile(r"\ufffd|锟斤拷|Ã|Â")


class ReliabilityError(RuntimeError):
    """G12 有限可靠性验收失败。"""


def run_command(
    command: list[str], *, timeout: int = 1800, check: bool = True
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
        raise ReliabilityError(
            f"命令失败（{result.returncode}）：{' '.join(command)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


@cache
def discover_docker() -> str | None:
    """返回能够连接 Engine 的 Docker CLI，而不是仅判断命令是否存在。"""
    configured = os.environ.get("DOCKER_BIN", "").strip()
    candidates = [configured, shutil.which("docker"), shutil.which("docker.exe")]
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
    return None


def required_command_output(command: list[str], label: str, *, timeout: int = 30) -> str:
    """读取验收环境事实；命令失败或空输出都不能记作有效证据。"""
    last_result: subprocess.CompletedProcess[str] | None = None
    for _ in range(2):
        last_result = run_command(command, timeout=timeout, check=False)
        output = last_result.stdout.strip().replace("\r", "")
        if last_result.returncode == 0 and output:
            return output
    assert last_result is not None
    raise ReliabilityError(
        f"无法读取{label}：退出码 {last_result.returncode}，输出为空或命令失败。"
    )


def load_smoke_module() -> Any:
    path = ROOT / "tests" / "smoke" / "run.py"
    spec = importlib.util.spec_from_file_location("alert_management_smoke", path)
    if spec is None or spec.loader is None:
        raise ReliabilityError(f"无法加载共享业务验收模块：{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_clean_source() -> str:
    status = run_command(
        ["git", "status", "--porcelain", "--untracked-files=all"], timeout=30
    ).stdout.strip()
    if status:
        raise ReliabilityError("M12 正式可靠性验收拒绝脏工作树。")
    commit = run_command(["git", "rev-parse", "HEAD"], timeout=30).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ReliabilityError("无法确定固定候选提交。")
    return commit


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) != 0


def ensure_stopped() -> None:
    run_command([str(ROOT / "scripts/dev/stop.sh")], timeout=120)
    occupied = [port for port in PORTS if not port_is_free(port)]
    if occupied:
        raise ReliabilityError(f"停止后固定端口仍被占用：{occupied}")


def capture_environment() -> dict[str, Any]:
    memory_match = re.search(
        r"^MemTotal:\s+(\d+)\s+kB$",
        Path("/proc/meminfo").read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    environment: dict[str, Any] = {
        "platform": platform.platform(),
        "architecture": platform.machine(),
        "logical_cpu_count": os.cpu_count(),
        "wsl_total_memory_mib": round(int(memory_match.group(1)) / 1024, 1)
        if memory_match
        else None,
        "python": sys.version.splitlines()[0],
    }
    powershell = shutil.which("powershell.exe")
    if powershell:
        command = (
            "$utf8=New-Object System.Text.UTF8Encoding($false);"
            "[Console]::OutputEncoding=$utf8;$OutputEncoding=$utf8;"
            "$os=Get-CimInstance Win32_OperatingSystem;"
            "$cpu=Get-CimInstance Win32_Processor|Select-Object -First 1;"
            "[ordered]@{os=$os.Caption;version=$os.Version;"
            "total_memory_mib=[math]::Round($os.TotalVisibleMemorySize/1024,1);"
            "cpu=$cpu.Name;logical_cpu_count=$cpu.NumberOfLogicalProcessors}"
            "|ConvertTo-Json -Compress"
        )
        windows_output = required_command_output(
            [powershell, "-NoProfile", "-Command", command], "Windows 环境"
        )
        environment["windows"] = json.loads(windows_output)
    java = shutil.which("java") or shutil.which("java.exe")
    if java:
        result = run_command([java, "-version"], timeout=30, check=False)
        lines = (result.stdout + result.stderr).splitlines()
        environment["java"] = lines[0] if lines else "unknown"
    docker = discover_docker()
    if not docker:
        raise ReliabilityError("无法读取 Docker 环境：未找到能够连接 Engine 的客户端。")
    environment["docker_server"] = required_command_output(
        [docker, "version", "--format", "{{.Server.Version}}"], "Docker Server 版本"
    )
    environment["postgres_image_id"] = required_command_output(
        [docker, "inspect", "alert-management-m1-postgres", "--format", "{{.Image}}"],
        "PostgreSQL 镜像身份",
    )
    environment["postgres"] = required_command_output(
        [docker, "exec", "alert-management-m1-postgres", "postgres", "--version"],
        "PostgreSQL 版本",
    )
    return environment


def prepare_admin_password() -> Path:
    session_dir = RUNTIME / "reliability" / "admin-session"
    command = (
        'source scripts/dev/common.sh; '
        f'dev_admin_login "{session_dir}"'
    )
    run_command(["bash", "-lc", command], timeout=60)
    password_file = RUNTIME / "compose-secrets" / "bootstrap-admin-password.txt"
    if not password_file.is_file() or not password_file.read_text(encoding="utf-8").strip():
        raise ReliabilityError("开发管理员密码文件缺失或为空。")
    return password_file


def reset_business(smoke: Any, label: str) -> None:
    payload = smoke.json_request(
        "/api/v1/demo/reset",
        "POST",
        {"operator": label, "confirmation": "RESET_DEMO"},
    )
    if payload.get("business_state") != "EMPTY":
        raise ReliabilityError(f"演示复位未返回 EMPTY：{payload}")


def verify_smoke_cycle(smoke: Any, expected_summary: dict[str, Any]) -> dict[str, Any]:
    run_id, summary = smoke.verify_analysis()
    if summary != expected_summary:
        raise ReliabilityError(f"重复分析摘要漂移：{summary}")
    dashboard = smoke.http_request(f"/api/v1/analyses/{run_id}/dashboard")
    if dashboard.get("total") != 300:
        raise ReliabilityError(f"重复运行看板数量错误：{dashboard}")
    reset_business(smoke, "SYNTHETIC_M12_RELIABILITY")
    return summary


def multipart_import(smoke: Any, path: Path, expected_rows: int) -> str:
    body, boundary = smoke.multipart_file(path)
    preview = smoke.http_request(
        "/api/v1/imports/preview",
        method="POST",
        body=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
        },
        timeout=300,
    )
    actual = (preview.get("status"), preview.get("total_rows"), preview.get("valid_rows"))
    if actual != ("READY", expected_rows, expected_rows):
        raise ReliabilityError(f"{expected_rows} 行预览不符合预期：{actual}")
    batch_id = preview.get("batch_id")
    confirmed = smoke.http_request(
        f"/api/v1/imports/{batch_id}/confirm", method="POST", timeout=300
    )
    if confirmed.get("status") != "IMPORTED" or confirmed.get("valid_rows") != expected_rows:
        raise ReliabilityError(f"{expected_rows} 行确认不符合预期：{confirmed}")
    return str(batch_id)


def verify_demo_20k(smoke: Any, result_dir: Path, iteration: int) -> dict[str, Any]:
    sample = result_dir / "synthetic_demo_20000.csv"
    if not sample.is_file():
        run_command(
            [
                sys.executable,
                str(ROOT / "samples/generate_samples.py"),
                "--dataset",
                "demo",
                "--output",
                str(sample),
            ],
            timeout=120,
        )
    started = time.monotonic()
    batch_id = multipart_import(smoke, sample, 20_000)
    imported_seconds = time.monotonic() - started
    analysis_started = time.monotonic()
    analysis = smoke.http_request(
        f"/api/v1/imports/{batch_id}/analyses", method="POST", timeout=600
    )
    analysis_seconds = time.monotonic() - analysis_started
    summary = analysis.get("summary") or {}
    if (
        analysis.get("status") != "COMPLETED"
        or summary.get("input_count") != 20_000
        or summary.get("success_count") != 20_000
        or summary.get("failure_count") != 0
        or len(analysis.get("results", [])) != 20_000
    ):
        raise ReliabilityError(f"20,000 行分析结果不完整：{summary}")
    reset_business(smoke, "SYNTHETIC_M12_20K")
    result = {
        "iteration": iteration,
        "rows": 20_000,
        "import_seconds": round(imported_seconds, 3),
        "analysis_seconds": round(analysis_seconds, 3),
        "summary_sha256": digest_json(summary),
    }
    result["total_seconds"] = round(imported_seconds + analysis_seconds, 3)
    if result["total_seconds"] > 120:
        raise ReliabilityError(f"20,000 行单轮超过当前审核演示的 120 秒保护上限：{result}")
    return result


def assert_demo_performance(results: list[dict[str, Any]]) -> None:
    if len(results) != 3:
        raise ReliabilityError("20,000 行必须重复三轮才能判断候选内退化和重载资源趋势。")
    digests = {str(item["summary_sha256"]) for item in results}
    if len(digests) != 1:
        raise ReliabilityError(f"20,000 行三轮分析摘要漂移：{sorted(digests)}")
    first = float(results[0]["total_seconds"])
    allowed = max(first * 3, first + 30)
    for result in results[1:]:
        current = float(result["total_seconds"])
        if current > allowed:
            raise ReliabilityError(
                f"20,000 行后续轮次出现数量级退化：第一轮 {first}s，"
                f"第 {result['iteration']} 轮 {current}s，上限 {allowed}s"
            )


def linux_rss_mib(pid: int) -> float | None:
    status = Path(f"/proc/{pid}/status")
    if not status.is_file():
        return None
    match = re.search(r"^VmRSS:\s+(\d+)\s+kB$", status.read_text(), re.MULTILINE)
    return round(int(match.group(1)) / 1024, 3) if match else None


def windows_rss_mib(pid: int) -> float | None:
    if not shutil.which("powershell.exe"):
        return None
    result = run_command(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            f"$p=Get-Process -Id {pid} -ErrorAction SilentlyContinue; if ($p) {{ $p.WorkingSet64 }}",
        ],
        timeout=20,
        check=False,
    )
    value = result.stdout.strip().replace("\r", "")
    return round(int(value) / 1024 / 1024, 3) if value.isdigit() else None


def read_pid(name: str) -> tuple[int, bool] | None:
    linux_path = PID_ROOT / f"{name}.pid"
    windows_path = PID_ROOT / f"{name}.winpid"
    for path, is_windows in ((linux_path, False), (windows_path, True)):
        if path.is_file():
            value = path.read_text(encoding="utf-8").strip()
            if value.isdigit():
                return int(value), is_windows
    return None


def parse_memory(value: str) -> float:
    match = re.match(r"\s*([0-9.]+)\s*([KMG]i?B)", value)
    if not match:
        raise ReliabilityError(f"无法解析 Docker 内存：{value}")
    number = float(match.group(1))
    unit = match.group(2)
    return round(number * {"KB": 1 / 1024, "KiB": 1 / 1024, "MB": 1, "MiB": 1,
                           "GB": 1024, "GiB": 1024}[unit], 3)


def postgres_memory_mib() -> float | None:
    docker = discover_docker()
    if not docker:
        return None
    result = run_command(
        [docker, "stats", "--no-stream", "--format", "{{.MemUsage}}", "alert-management-m1-postgres"],
        timeout=30,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return parse_memory(result.stdout.split("/")[0])


def memory_sample(phase: str) -> dict[str, Any]:
    sample: dict[str, Any] = {"phase": phase}
    for name in ("backend", "algorithm"):
        owned = read_pid(name)
        if owned is None:
            raise ReliabilityError(f"缺少受管进程 PID：{name}")
        pid, is_windows = owned
        value = windows_rss_mib(pid) if is_windows else linux_rss_mib(pid)
        if value is None:
            raise ReliabilityError(f"无法读取受管进程内存：{name} PID={pid}")
        sample[name] = value
    postgres = postgres_memory_mib()
    if postgres is None:
        raise ReliabilityError("无法读取开发 PostgreSQL 容器内存。")
    sample["postgres"] = postgres
    return sample


def assert_memory(samples: list[dict[str, Any]]) -> None:
    if len(samples) < 5:
        raise ReliabilityError("资源样本不足，无法判断持续增长。")
    for component, limit in MEMORY_LIMIT_MIB.items():
        values = [sample[component] for sample in samples]
        if max(values) > limit:
            raise ReliabilityError(f"{component} 内存超过演示保护上限 {limit}MiB：{values}")
        repeated = [sample[component] for sample in samples if str(sample["phase"]).startswith("smoke-")]
        tail = repeated[-4:]
        if len(tail) < 4:
            raise ReliabilityError(f"{component} 缺少 300 行重复运行资源样本。")
        deltas = [right - left for left, right in zip(tail, tail[1:])]
        if all(delta > 8 for delta in deltas) and tail[-1] - tail[0] > 64:
            raise ReliabilityError(f"{component} 预热后仍持续增长：{values}")


def assert_heavy_memory(samples: list[dict[str, Any]]) -> None:
    heavy = [sample for sample in samples if str(sample["phase"]).startswith("demo-20000-")]
    if len(heavy) != 3:
        raise ReliabilityError("缺少三轮 20,000 行重载后的独立资源样本。")
    for component in MEMORY_LIMIT_MIB:
        values = [float(sample[component]) for sample in heavy]
        deltas = [right - left for left, right in zip(values, values[1:])]
        if all(delta > 32 for delta in deltas) and values[-1] - values[0] > 64:
            raise ReliabilityError(f"{component} 在三轮 20,000 行后持续显著增长：{values}")


def linux_remote_addresses(pid: int) -> set[str]:
    fd_root = Path(f"/proc/{pid}/fd")
    if not fd_root.is_dir():
        return set()
    inodes = set()
    for fd in fd_root.iterdir():
        try:
            match = re.fullmatch(r"socket:\[(\d+)]", os.readlink(fd))
        except OSError:
            continue
        if match:
            inodes.add(match.group(1))
    remotes: set[str] = set()
    for table, ipv6 in ((Path("/proc/net/tcp"), False), (Path("/proc/net/tcp6"), True)):
        if not table.is_file():
            continue
        remotes.update(parse_proc_tcp(table.read_text(), ipv6=ipv6, inodes=inodes))
    return remotes


def parse_proc_tcp(
    content: str, *, ipv6: bool, inodes: set[str] | None = None
) -> set[str]:
    remotes: set[str] = set()
    for line in content.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 10 or fields[3] != "01":
            continue
        if inodes is not None and fields[9] not in inodes:
            continue
        raw = fields[2].split(":", 1)[0]
        try:
            packed = bytes.fromhex(raw)
            if ipv6:
                chunks = [packed[index:index + 4][::-1] for index in range(0, 16, 4)]
                address = socket.inet_ntop(socket.AF_INET6, b"".join(chunks))
            else:
                address = socket.inet_ntoa(packed[::-1])
        except (OSError, ValueError):
            raise ReliabilityError("无法解析受管进程 TCP 连接表。")
        remotes.add(address)
    return remotes


def windows_remote_addresses(pid: int) -> set[str]:
    command = (
        "try {@(Get-NetTCPConnection -ErrorAction Stop | Where-Object {"
        f"$_.OwningProcess -eq {pid} -and $_.State -eq 'Established'"
        "}) | "
        "Select-Object -ExpandProperty RemoteAddress -Unique} "
        "catch {Write-Error $_; exit 1}"
    )
    result = run_command(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            command,
        ],
        timeout=5,
        check=False,
    )
    if result.returncode != 0:
        raise ReliabilityError(f"无法读取 Windows 进程 TCP 连接：PID={pid}")
    return {line.strip().replace("\r", "") for line in result.stdout.splitlines() if line.strip()}


def postgres_remote_addresses() -> set[str]:
    docker = discover_docker()
    if not docker:
        raise ReliabilityError("无法审计 PostgreSQL 连接：未找到 Docker。")
    remotes: set[str] = set()
    tcp_succeeded = False
    for path, ipv6 in (("/proc/net/tcp", False), ("/proc/net/tcp6", True)):
        result = run_command(
            [docker, "exec", "alert-management-m1-postgres", "cat", path],
            timeout=5,
            check=False,
        )
        if result.returncode == 0:
            remotes.update(parse_proc_tcp(result.stdout, ipv6=ipv6))
            if path == "/proc/net/tcp":
                tcp_succeeded = True
    if not tcp_succeeded:
        raise ReliabilityError("无法读取 PostgreSQL 容器的 IPv4 TCP 连接表。")
    return remotes


class ConnectionMonitor:
    def __init__(self) -> None:
        self.observed: dict[str, set[str]] = {
            "backend": set(),
            "algorithm": set(),
            "postgres": set(),
        }
        self.errors: list[str] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="m12-network-monitor", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> dict[str, list[str]]:
        self._stop.set()
        self._thread.join(timeout=30)
        if self._thread.is_alive():
            raise ReliabilityError("受管进程外联监视线程未能停止。")
        if self.errors:
            raise ReliabilityError(f"受管进程外联监视失败：{self.errors[-1]}")
        return {name: sorted(addresses) for name, addresses in self.observed.items()}

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                for name in ("backend", "algorithm"):
                    owned = read_pid(name)
                    if owned is None:
                        raise ReliabilityError(f"外联监视期间缺少 PID：{name}")
                    pid, is_windows = owned
                    addresses = (
                        windows_remote_addresses(pid)
                        if is_windows
                        else linux_remote_addresses(pid)
                    )
                    self.observed[name].update(addresses)
                self.observed["postgres"].update(postgres_remote_addresses())
            except Exception as exc:
                self.errors.append(f"{type(exc).__name__}: {exc}")
                return
            self._stop.wait(0.5)


def assert_no_external_connections(observed: dict[str, list[str]]) -> None:
    allowed = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
    for name in ("backend", "algorithm"):
        addresses = set(observed.get(name, []))
        unexpected = addresses - allowed
        if unexpected:
            raise ReliabilityError(f"{name} 存在未声明的非回环连接：{sorted(unexpected)}")
    unexpected_postgres = []
    for address in observed.get("postgres", []):
        parsed = ipaddress.ip_address(address)
        if not (parsed.is_loopback or parsed.is_private):
            unexpected_postgres.append(address)
    if unexpected_postgres:
        raise ReliabilityError(f"PostgreSQL 存在非本机/容器私网连接：{unexpected_postgres}")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assert_logs_clean(
    result_dir: Path, baseline: dict[str, int], started_at: datetime
) -> dict[str, dict[str, Any]]:
    pattern_text = (
        r"\ufffd|锟斤拷|Ã|Â|\?\?\?|OutOfMemoryError|Traceback|Unhandled|"
        r"Exception in thread|^.*\b(?:ERROR|FATAL|PANIC)\b"
    )
    patterns = re.compile(pattern_text, re.IGNORECASE | re.MULTILINE)
    secret_values = []
    secret_root = RUNTIME / "compose-secrets"
    for path in secret_root.glob("*.txt"):
        value = path.read_text(encoding="utf-8").strip()
        if len(value) >= 8:
            secret_values.append(value)
    candidates = []
    for path in sorted(LOG_ROOT.glob("*.log")):
        if baseline.get(path.name) != path.stat().st_mtime_ns:
            candidates.append(path)
    docker = discover_docker()
    if docker:
        postgres_log = result_dir / "postgres.log"
        result = run_command(
            [
                docker,
                "logs",
                "--since",
                started_at.isoformat(),
                "alert-management-m1-postgres",
            ],
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            raise ReliabilityError("无法读取本轮 PostgreSQL 容器日志。")
        postgres_log.write_text(result.stdout + result.stderr, encoding="utf-8")
        candidates.append(postgres_log)
    required_logs = {"backend.log", "algorithm.log", "postgres.log"}
    actual_logs = {path.name for path in candidates}
    missing_logs = sorted(required_logs - actual_logs)
    if missing_logs:
        raise ReliabilityError(f"缺少本轮必要服务日志：{missing_logs}")
    saved_root = result_dir / "service-logs"
    saved_root.mkdir()
    metadata: dict[str, dict[str, Any]] = {}
    for path in candidates:
        content = path.read_text(encoding="utf-8", errors="strict")
        for secret in secret_values:
            if secret in content:
                raise ReliabilityError(f"服务日志泄露实例密钥：{path.name}")
        match = patterns.search(content)
        if match:
            raise ReliabilityError(f"服务日志出现乱码或未处理异常：{path.name}: {match.group(0)}")
        destination = saved_root / path.name
        if destination.exists():
            destination = saved_root / f"postgres-{path.name}"
        shutil.copyfile(path, destination)
        metadata[destination.name] = {
            "bytes": destination.stat().st_size,
            "sha256": file_sha256(destination),
            "scan_rule": pattern_text,
            "matches": 0,
            "secret_matches": 0,
        }
    return metadata


def assert_cleanup(previous_pids: dict[str, tuple[int, bool]]) -> None:
    ensure_stopped()
    remaining_files = [path.name for path in PID_ROOT.glob("*pid")]
    if remaining_files:
        raise ReliabilityError(f"停止后仍有 PID 文件：{remaining_files}")
    for name, (pid, is_windows) in previous_pids.items():
        alive = windows_rss_mib(pid) is not None if is_windows else Path(f"/proc/{pid}").exists()
        if alive:
            raise ReliabilityError(f"停止后受管进程仍存在：{name} PID={pid}")


def digest_json(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def assert_summary_text_clean(value: Any, path: str = "summary") -> None:
    """阻止环境或指标中的乱码被写成 PASS；扫描规则文本自身除外。"""
    if isinstance(value, dict):
        for key, child in value.items():
            if key != "scan_rule":
                assert_summary_text_clean(child, f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            assert_summary_text_clean(child, f"{path}[{index}]")
        return
    if isinstance(value, str) and MOJIBAKE_PATTERN.search(value):
        raise ReliabilityError(f"可靠性摘要出现乱码：{path}")


def main() -> int:
    monotonic_started = time.monotonic()
    started_at = datetime.now(timezone.utc)
    result_dir = RESULT_ROOT / started_at.strftime("%Y%m%d-%H%M%S-%f")
    result_dir.mkdir(parents=True, exist_ok=False)
    summary: dict[str, Any] = {
        "status": "FAIL",
        "started_at": started_at.isoformat(),
        "source_commit": None,
        "environment": {},
        "quality": None,
        "cycles": [],
        "memory_mib": [],
        "connections": {},
        "demo_20000": [],
        "service_logs": {},
        "cleanup": "NOT_RUN",
    }
    stack_attempted = False
    cleanup_complete = False
    owned_pids: dict[str, tuple[int, bool]] = {}
    monitor: ConnectionMonitor | None = None
    try:
        summary["source_commit"] = assert_clean_source()
        run_command([str(ROOT / "scripts/dev/bootstrap.sh")], timeout=900)
        ensure_stopped()
        quality = run_command(
            [sys.executable, str(ROOT / "scripts/reliability/quality_audit.py")],
            timeout=1800,
            check=False,
        )
        quality_log = result_dir / "quality-audit.log"
        quality_log.write_text(quality.stdout + quality.stderr, encoding="utf-8")
        summary["quality"] = {
            "status": "PASS" if quality.returncode == 0 else "FAIL",
            "evidence": quality_log.name,
            "bytes": quality_log.stat().st_size,
            "sha256": file_sha256(quality_log),
        }
        if quality.returncode != 0:
            raise ReliabilityError(
                f"质量与依赖审计失败，详见 {quality_log}（退出码 {quality.returncode}）。"
            )
        log_baseline = {
            path.name: path.stat().st_mtime_ns for path in LOG_ROOT.glob("*.log")
        }
        service_started_at = datetime.now(timezone.utc)
        stack_attempted = True
        run_command([str(ROOT / "scripts/dev/start.sh")], timeout=900)
        owned_pids = {
            name: owned for name in ("backend", "algorithm") if (owned := read_pid(name))
        }
        summary["environment"] = capture_environment()
        password_file = prepare_admin_password()
        smoke = load_smoke_module()
        smoke.BOOTSTRAP_PASSWORD = password_file.read_text(encoding="utf-8").strip()
        smoke.login_as_bootstrap_admin()
        smoke.assert_system_up()
        smoke.assert_frontend()
        reset_business(smoke, "SYNTHETIC_M12_INITIAL")
        expected_summary = json.loads(
            (ROOT / "samples/expected/analysis-smoke-expected.json").read_text(encoding="utf-8")
        )["summary"]
        expected_projection = {
            "input_count": 300,
            "success_count": 300,
            "failure_count": 0,
            "noise_type_counts": expected_summary["noise_type_counts"],
            "cause_category_counts": expected_summary["cause_category_counts"],
            "event_chain_count": expected_summary["event_chain_counts"]["total"],
        }
        monitor = ConnectionMonitor()
        monitor.start()
        verify_smoke_cycle(smoke, expected_projection)
        for index in range(5):
            cycle_started = time.monotonic()
            actual = verify_smoke_cycle(smoke, expected_projection)
            summary["cycles"].append({
                "index": index + 1,
                "duration_seconds": round(time.monotonic() - cycle_started, 3),
                "summary_sha256": digest_json(actual),
            })
            time.sleep(2)
            summary["memory_mib"].append(memory_sample(f"smoke-{index + 1}"))
        assert_memory(summary["memory_mib"])
        for iteration in (1, 2, 3):
            summary["demo_20000"].append(verify_demo_20k(smoke, result_dir, iteration))
            time.sleep(2)
            summary["memory_mib"].append(memory_sample(f"demo-20000-{iteration}"))
        assert_demo_performance(summary["demo_20000"])
        assert_memory(summary["memory_mib"])
        assert_heavy_memory(summary["memory_mib"])
        summary["connections"] = monitor.stop()
        monitor = None
        assert_no_external_connections(summary["connections"])
        summary["service_logs"] = assert_logs_clean(
            result_dir, log_baseline, service_started_at
        )
        assert_cleanup(owned_pids)
        cleanup_complete = True
        summary["cleanup"] = "PASS"
        assert_summary_text_clean(summary)
        summary["status"] = "PASS"
        return 0
    except Exception as exc:
        summary["failure"] = f"{type(exc).__name__}: {exc}"
        print(summary["failure"], file=sys.stderr)
        return 1
    finally:
        if monitor is not None:
            try:
                summary["connections"] = monitor.stop()
            except Exception as exc:
                summary["connection_monitor_cleanup_failure"] = f"{type(exc).__name__}: {exc}"
        if stack_attempted and not cleanup_complete:
            try:
                assert_cleanup(owned_pids)
                summary["cleanup"] = "PASS_AFTER_FAILURE"
            except Exception as exc:
                summary["cleanup"] = "FAIL"
                summary["cleanup_failure"] = f"{type(exc).__name__}: {exc}"
        summary["finished_at"] = datetime.now(timezone.utc).isoformat()
        summary["duration_seconds"] = round(time.monotonic() - monotonic_started, 3)
        (result_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"M12 可靠性证据：{result_dir / 'summary.json'}")


if __name__ == "__main__":
    raise SystemExit(main())
