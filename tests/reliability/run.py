#!/usr/bin/env python3
"""M12 恢复后运行质量、有限资源趋势和清理边界验收。"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import socket
import subprocess
import sys
import time
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


def verify_demo_20k(smoke: Any, result_dir: Path) -> dict[str, Any]:
    sample = result_dir / "synthetic_demo_20000.csv"
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
    return {
        "rows": 20_000,
        "import_seconds": round(imported_seconds, 3),
        "analysis_seconds": round(analysis_seconds, 3),
        "summary_sha256": digest_json(summary),
    }


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
    docker = shutil.which("docker") or shutil.which("docker.exe")
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


def memory_sample() -> dict[str, float]:
    sample: dict[str, float] = {}
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


def assert_memory(samples: list[dict[str, float]]) -> None:
    if len(samples) < 5:
        raise ReliabilityError("资源样本不足，无法判断持续增长。")
    for component, limit in MEMORY_LIMIT_MIB.items():
        values = [sample[component] for sample in samples]
        if max(values) > limit:
            raise ReliabilityError(f"{component} 内存超过演示保护上限 {limit}MiB：{values}")
        tail = values[-4:]
        deltas = [right - left for left, right in zip(tail, tail[1:])]
        if all(delta > 8 for delta in deltas) and tail[-1] - tail[0] > 64:
            raise ReliabilityError(f"{component} 预热后仍持续增长：{values}")


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
        for line in table.read_text().splitlines()[1:]:
            fields = line.split()
            if len(fields) < 10 or fields[9] not in inodes or fields[3] != "01":
                continue
            raw = fields[2].split(":", 1)[0]
            packed = bytes.fromhex(raw)
            if ipv6:
                chunks = [packed[index:index + 4][::-1] for index in range(0, 16, 4)]
                address = socket.inet_ntop(socket.AF_INET6, b"".join(chunks))
            else:
                address = socket.inet_ntoa(packed[::-1])
            remotes.add(address)
    return remotes


def windows_remote_addresses(pid: int) -> set[str]:
    result = run_command(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            f"Get-NetTCPConnection -OwningProcess {pid} -State Established -ErrorAction SilentlyContinue | Select-Object -ExpandProperty RemoteAddress -Unique",
        ],
        timeout=20,
        check=False,
    )
    return {line.strip().replace("\r", "") for line in result.stdout.splitlines() if line.strip()}


def assert_no_external_connections() -> dict[str, list[str]]:
    observed: dict[str, list[str]] = {}
    allowed = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
    for name in ("backend", "algorithm"):
        owned = read_pid(name)
        if owned is None:
            raise ReliabilityError(f"无法审计外联，缺少 PID：{name}")
        pid, is_windows = owned
        addresses = windows_remote_addresses(pid) if is_windows else linux_remote_addresses(pid)
        observed[name] = sorted(addresses)
        unexpected = addresses - allowed
        if unexpected:
            raise ReliabilityError(f"{name} 存在未声明的非回环连接：{sorted(unexpected)}")
    return observed


def assert_logs_clean() -> dict[str, str]:
    patterns = re.compile(r"\ufffd|锟斤拷|Ã|Â|\?\?\?|OutOfMemoryError|Traceback|Unhandled")
    hashes: dict[str, str] = {}
    for path in sorted(LOG_ROOT.glob("*.log")):
        content = path.read_text(encoding="utf-8", errors="strict")
        match = patterns.search(content)
        if match:
            raise ReliabilityError(f"服务日志出现乱码或未处理异常：{path.name}: {match.group(0)}")
        hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    if not hashes:
        raise ReliabilityError("没有可审计的服务日志。")
    return hashes


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


def main() -> int:
    monotonic_started = time.monotonic()
    started_at = datetime.now(timezone.utc)
    result_dir = RESULT_ROOT / started_at.strftime("%Y%m%d-%H%M%S-%f")
    result_dir.mkdir(parents=True, exist_ok=False)
    summary: dict[str, Any] = {
        "status": "FAIL",
        "started_at": started_at.isoformat(),
        "source_commit": None,
        "quality": None,
        "cycles": [],
        "memory_mib": [],
        "connections": {},
        "demo_20000": None,
        "log_sha256": {},
        "cleanup": "NOT_RUN",
    }
    started = False
    try:
        summary["source_commit"] = assert_clean_source()
        quality = run_command(
            [sys.executable, str(ROOT / "scripts/reliability/quality_audit.py")], timeout=1800
        )
        summary["quality"] = {"status": "PASS", "output_sha256": hashlib.sha256(
            (quality.stdout + quality.stderr).encode("utf-8")
        ).hexdigest()}
        ensure_stopped()
        run_command([str(ROOT / "scripts/dev/start.sh")], timeout=900)
        started = True
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
            summary["memory_mib"].append(memory_sample())
        assert_memory(summary["memory_mib"])
        summary["connections"] = assert_no_external_connections()
        summary["demo_20000"] = verify_demo_20k(smoke, result_dir)
        time.sleep(2)
        summary["memory_mib"].append(memory_sample())
        assert_memory(summary["memory_mib"])
        summary["log_sha256"] = assert_logs_clean()
        previous_pids = {
            name: owned for name in ("backend", "algorithm") if (owned := read_pid(name))
        }
        assert_cleanup(previous_pids)
        started = False
        summary["cleanup"] = "PASS"
        summary["status"] = "PASS"
        return 0
    except Exception as exc:
        summary["failure"] = f"{type(exc).__name__}: {exc}"
        print(summary["failure"], file=sys.stderr)
        return 1
    finally:
        if started:
            cleanup = run_command([str(ROOT / "scripts/dev/stop.sh")], timeout=120, check=False)
            summary["cleanup"] = "PASS_AFTER_FAILURE" if cleanup.returncode == 0 else "FAIL"
        summary["finished_at"] = datetime.now(timezone.utc).isoformat()
        summary["duration_seconds"] = round(time.monotonic() - monotonic_started, 3)
        (result_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"M12 可靠性证据：{result_dir / 'summary.json'}")


if __name__ == "__main__":
    raise SystemExit(main())
