#!/usr/bin/env python3
"""M12 静态质量、锁文件和依赖漏洞审计入口。"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
OSV_BATCH_URL = "https://api.osv.dev/v1/querybatch"
SOURCE_PREFIXES = (
    ".github/",
    "config/",
    "packaging/",
    "samples/",
    "scripts/",
    "src/",
    "tests/",
    "tools/",
)
SOURCE_SUFFIXES = {
    ".java",
    ".js",
    ".json",
    ".mjs",
    ".ps1",
    ".py",
    ".sh",
    ".toml",
    ".ts",
    ".vue",
    ".xml",
    ".yaml",
    ".yml",
}
ROOT_SOURCE_FILES = {"compose.yaml", "compose.network.yaml"}
NPM_PROJECTS = (
    Path("src/frontend"),
    Path("tests/e2e"),
)
EXACT_REQUIREMENT = re.compile(
    r"^([A-Za-z0-9_.-]+)(?:\[[^]]+\])?==([^\s;]+)(?:\s*;\s*(.+))?$"
)
VULNERABILITY_ID = re.compile(r"\b(?:CVE-\d{4}-\d+|GHSA-[0-9a-z-]+)\b", re.I)
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


class AuditFailure(RuntimeError):
    """表示质量门槛发现了必须阻断的问题。"""


@dataclass(frozen=True, order=True)
class Package:
    ecosystem: str
    name: str
    version: str

    @property
    def coordinate(self) -> str:
        return f"{self.ecosystem}:{self.name}@{self.version}"


def canonical_python_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def command_output(result: subprocess.CompletedProcess[bytes], limit: int = 80) -> str:
    output = (result.stdout or b"").decode("utf-8", errors="replace")
    lines = output.splitlines()
    return "\n".join(lines[-limit:])


def run_command(
    label: str,
    command: list[str],
    *,
    cwd: Path = REPOSITORY_ROOT,
    timeout: int,
    env: dict[str, str] | None = None,
    retry_windows_bridge: bool = False,
) -> None:
    print(f"[质量审计] {label}")
    attempts = 3 if retry_windows_bridge else 1
    for attempt in range(1, attempts + 1):
        try:
            result = subprocess.run(
                command,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise AuditFailure(f"{label}无法完成：{type(error).__name__}") from error
        detail = command_output(result)
        bridge_timeout = "UtilAcceptVsock" in detail and "failed 110" in detail
        if result.returncode == 0:
            break
        if bridge_timeout and attempt < attempts:
            print(f"[质量审计] WSL 到 Windows 通信超时，第 {attempt} 次有限重试")
            time.sleep(2)
            continue
        suffix = f"\n{detail}" if detail else ""
        raise AuditFailure(f"{label}失败（退出码 {result.returncode}）{suffix}")
    print(f"[质量审计] {label}通过")


def tracked_source_files(root: Path = REPOSITORY_ROOT) -> list[Path]:
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AuditFailure(f"无法读取 Git 跟踪文件：{type(error).__name__}") from error
    if result.returncode != 0:
        raise AuditFailure("无法读取 Git 跟踪文件")
    paths: list[Path] = []
    for raw_path in result.stdout.split(b"\0"):
        if not raw_path:
            continue
        relative = Path(raw_path.decode("utf-8"))
        relative_text = relative.as_posix()
        if relative.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        if relative_text in ROOT_SOURCE_FILES or relative_text.startswith(
            SOURCE_PREFIXES
        ):
            paths.append(root / relative)
    if not paths:
        raise AuditFailure("没有找到待检查的跟踪源码")
    return paths


def check_text_files(paths: Iterable[Path]) -> None:
    problems: list[str] = []
    for path in paths:
        try:
            data = path.read_bytes()
            text = data.decode("utf-8-sig")
        except (OSError, UnicodeDecodeError):
            problems.append(f"{path}: 不是可读取的 UTF-8 文本")
            continue
        if data and not data.endswith(b"\n"):
            problems.append(f"{path}: 缺少末尾换行")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if line.endswith((" ", "\t")):
                problems.append(f"{path}:{line_number}: 行尾空白")
        try:
            if path.suffix.lower() == ".json":
                json.loads(text)
            elif path.suffix.lower() == ".toml":
                tomllib.loads(text)
        except (json.JSONDecodeError, tomllib.TOMLDecodeError) as error:
            problems.append(f"{path}: 结构解析失败：{error}")
    if problems:
        preview = "\n".join(problems[:30])
        if len(problems) > 30:
            preview += f"\n另有 {len(problems) - 30} 项未显示"
        raise AuditFailure(f"源码格式或结构检查失败：\n{preview}")


def check_script_syntax(paths: Iterable[Path]) -> None:
    scripts = list(paths)
    shell_scripts = sorted(
        str(path) for path in scripts if path.suffix.lower() == ".sh"
    )
    if shell_scripts:
        run_command("Bash 脚本语法", ["bash", "-n", *shell_scripts], timeout=120)

    powershell_scripts = sorted(
        path for path in scripts if path.suffix.lower() == ".ps1"
    )
    if not powershell_scripts:
        return
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh")
    if not powershell:
        raise AuditFailure("未找到 powershell.exe 或 pwsh，无法解析 PowerShell 脚本")
    windows_powershell = Path(powershell).name.lower() == "powershell.exe"
    for path in powershell_scripts:
        parser_path = str(path)
        parser_command = (
            "$errors=$null;"
            "[void][System.Management.Automation.Language.Parser]::ParseFile("
            "$env:ALERT_SCRIPT_PATH,[ref]$null,[ref]$errors);"
            "if($errors.Count -gt 0){$errors|ForEach-Object{$_.ToString()};exit 1}"
        )
        environment = os.environ.copy()
        environment["ALERT_SCRIPT_PATH"] = parser_path
        if windows_powershell:
            wslenv = environment.get("WSLENV", "")
            entries = [entry for entry in wslenv.split(":") if entry]
            entries.append("ALERT_SCRIPT_PATH/p")
            environment["WSLENV"] = ":".join(entries)
        run_command(
            f"PowerShell 脚本语法：{path.relative_to(REPOSITORY_ROOT)}",
            [powershell, "-NoProfile", "-Command", parser_command],
            timeout=30,
            env=environment,
            retry_windows_bridge=windows_powershell,
        )


def check_npm_lock(project_dir: Path) -> dict[str, str]:
    manifest_path = project_dir / "package.json"
    lock_path = project_dir / "package-lock.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AuditFailure(f"{project_dir} 的 npm 清单无法解析") from error
    if lock.get("lockfileVersion") != 3:
        raise AuditFailure(f"{lock_path} 必须使用 lockfileVersion 3")
    root_package = lock.get("packages", {}).get("")
    if not isinstance(root_package, dict):
        raise AuditFailure(f"{lock_path} 缺少根包记录")
    for field in (
        "dependencies",
        "devDependencies",
        "optionalDependencies",
        "peerDependencies",
    ):
        expected = manifest.get(field, {})
        actual = root_package.get(field, {})
        if expected != actual:
            raise AuditFailure(f"{project_dir} 的 {field} 与 package-lock.json 漂移")
    versions: dict[str, str] = {}
    for key, value in lock.get("packages", {}).items():
        if not key or "node_modules/" not in key or not isinstance(value, dict):
            continue
        name = key.rsplit("node_modules/", 1)[-1]
        version = value.get("version")
        if isinstance(version, str):
            versions.setdefault(name, version)
    return versions


def parse_locked_requirements(lock_path: Path) -> list[Package]:
    packages: dict[str, Package] = {}
    try:
        lines = lock_path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise AuditFailure(f"无法读取 {lock_path}") from error
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = EXACT_REQUIREMENT.fullmatch(line)
        if not match:
            raise AuditFailure(
                f"{lock_path}:{line_number} 不是精确的 name==version 锁定项"
            )
        name, version, _marker = match.groups()
        canonical_name = canonical_python_name(name)
        package = Package("PyPI", canonical_name, version)
        if canonical_name in packages and packages[canonical_name] != package:
            raise AuditFailure(f"{lock_path} 对 {canonical_name} 存在冲突版本")
        packages[canonical_name] = package
    if not packages:
        raise AuditFailure(f"{lock_path} 没有已解析依赖")
    return sorted(packages.values())


def check_python_lock(pyproject_path: Path, lock_path: Path) -> list[Package]:
    packages = parse_locked_requirements(lock_path)
    locked_versions = {package.name: package.version for package in packages}
    try:
        pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise AuditFailure(f"无法解析 {pyproject_path}") from error
    project = pyproject.get("project", {})
    requirements = list(project.get("dependencies", []))
    for optional in project.get("optional-dependencies", {}).values():
        requirements.extend(optional)
    for requirement in requirements:
        match = EXACT_REQUIREMENT.fullmatch(requirement.strip())
        if not match:
            raise AuditFailure(f"{pyproject_path} 含非精确依赖：{requirement}")
        name, version, _marker = match.groups()
        canonical_name = canonical_python_name(name)
        if locked_versions.get(canonical_name) != version:
            raise AuditFailure(
                f"{canonical_name} 在 pyproject.toml 与 requirements.lock 之间漂移"
            )
    return packages


def find_python312() -> str:
    candidates = [REPOSITORY_ROOT / ".runtime/venv/bin/python"]
    python312 = shutil.which("python3.12")
    if python312:
        candidates.append(Path(python312))
    if sys.version_info[:2] == (3, 12):
        candidates.append(Path(sys.executable))
    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            result = subprocess.run(
                [
                    str(candidate),
                    "-c",
                    "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
                text=True,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0 and result.stdout.strip() == "3.12":
            return str(candidate)
    raise AuditFailure(
        "未找到项目要求的 Python 3.12；请先运行 scripts/dev/bootstrap.sh"
    )


def maven_command(arguments: list[str]) -> list[str]:
    if shutil.which("java"):
        return [str(REPOSITORY_ROOT / "mvnw"), *arguments]
    if shutil.which("cmd.exe") and (REPOSITORY_ROOT / "mvnw.cmd").exists():
        command = "set DEBUG=false&& mvnw.cmd " + " ".join(arguments).replace("/", "\\")
        return ["cmd.exe", "/d", "/c", command]
    raise AuditFailure("未找到可用的 Java 21/Maven wrapper 运行环境")


def resolve_maven_dependencies() -> list[Package]:
    output_path = REPOSITORY_ROOT / "src/backend/target/m12-dependencies.txt"
    output_path.unlink(missing_ok=True)
    arguments = [
        "-f",
        "src/backend/pom.xml",
        "-DskipTests",
        "test-compile",
        "dependency:list",
        "-DincludeScope=runtime",
        "-DexcludeTransitive=false",
        "-DoutputAbsoluteArtifactFilename=false",
        "-DoutputFile=target/m12-dependencies.txt",
    ]
    try:
        run_command(
            "Java test-compile 与运行时依赖解析", maven_command(arguments), timeout=600
        )
        if not output_path.is_file():
            raise AuditFailure("Maven dependency:list 未生成解析结果")
        packages: set[Package] = set()
        for raw_line in output_path.read_text(encoding="utf-8").splitlines():
            coordinate = ANSI_ESCAPE.sub("", raw_line).split(" -- ", 1)[0].strip()
            parts = coordinate.split(":")
            if len(parts) not in (5, 6) or parts[-1] not in ("compile", "runtime"):
                continue
            group_id, artifact_id = parts[0], parts[1]
            version = parts[-2]
            packages.add(Package("Maven", f"{group_id}:{artifact_id}", version))
        if not packages:
            raise AuditFailure("Maven 运行时依赖解析结果为空或格式不可识别")
        return sorted(packages)
    except OSError as error:
        raise AuditFailure("无法读取 Maven 运行时依赖解析结果") from error
    finally:
        output_path.unlink(missing_ok=True)


def run_static_checks() -> list[Package]:
    maven_packages = resolve_maven_dependencies()
    python312 = find_python312()
    with tempfile.TemporaryDirectory(prefix="alert-quality-pyc-") as pycache:
        environment = os.environ.copy()
        environment["PYTHONPYCACHEPREFIX"] = pycache
        run_command(
            "Python 3.12 compileall",
            [
                python312,
                "-m",
                "compileall",
                "-q",
                "-f",
                "src/algorithm",
                "samples",
                "scripts",
                "tests",
            ],
            timeout=300,
            env=environment,
        )
    npm = shutil.which("npm")
    if not npm:
        raise AuditFailure("未找到 Node.js 22/npm，无法执行 Vue 静态构建")
    run_command(
        "Vue/TypeScript 静态构建",
        [npm, "--prefix", "src/frontend", "run", "build"],
        timeout=600,
    )
    return maven_packages


def npm_vulnerability_ids(vulnerability: dict[str, object]) -> set[str]:
    identifiers: set[str] = set()
    for item in vulnerability.get("via", []):
        if not isinstance(item, dict):
            continue
        joined = " ".join(str(item.get(field, "")) for field in ("name", "url"))
        identifiers.update(match.upper() for match in VULNERABILITY_ID.findall(joined))
        if not identifiers and isinstance(item.get("source"), int):
            identifiers.add(f"NPM-{item['source']}")
    return identifiers or {"NPM-ADVISORY"}


def run_npm_audit(project_dir: Path, versions: dict[str, str]) -> None:
    npm = shutil.which("npm")
    if not npm:
        raise AuditFailure("未找到 npm，无法执行 npm audit")
    print(f"[质量审计] npm 漏洞审计：{project_dir}")
    last_error: BaseException | None = None
    for attempt in range(1, 4):
        try:
            result = subprocess.run(
                [
                    npm,
                    "--prefix",
                    str(project_dir),
                    "audit",
                    "--audit-level=high",
                    "--json",
                ],
                cwd=REPOSITORY_ROOT,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=180,
                check=False,
            )
            payload = json.loads(result.stdout.decode("utf-8"))
        except (
            OSError,
            subprocess.TimeoutExpired,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as error:
            last_error = error
            if attempt < 3:
                time.sleep(attempt)
                continue
            raise AuditFailure(
                f"{project_dir} 的 npm audit 网络或响应解析失败"
            ) from error
        findings: list[tuple[str, str]] = []
        for name, vulnerability in payload.get("vulnerabilities", {}).items():
            if not isinstance(vulnerability, dict):
                continue
            if vulnerability.get("severity") not in ("high", "critical"):
                continue
            coordinate = f"npm:{name}@{versions.get(name, 'unknown')}"
            for identifier in npm_vulnerability_ids(vulnerability):
                findings.append((coordinate, identifier))
        if findings:
            for coordinate, identifier in sorted(set(findings)):
                print(f"[依赖漏洞] {coordinate} {identifier}", file=sys.stderr)
            raise AuditFailure(f"{project_dir} 存在高危或严重 npm 漏洞")
        if not payload.get("error") and result.returncode == 0:
            break
        last_error = RuntimeError(f"npm audit 退出码 {result.returncode}")
        if attempt < 3:
            print(
                f"[质量审计] {project_dir} npm audit 外部服务异常，第 {attempt} 次有限重试"
            )
            time.sleep(attempt)
    else:
        raise AuditFailure(
            f"{project_dir} 的 npm audit 依赖源连续不可用"
        ) from last_error
    print(f"[质量审计] npm 漏洞审计通过：{project_dir}")


def parse_osv_results(
    packages: list[Package], payload: object
) -> list[tuple[Package, str]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise AuditFailure("OSV querybatch 响应结构无效")
    results = payload["results"]
    if len(results) != len(packages):
        raise AuditFailure("OSV querybatch 响应数量与请求不一致")
    findings: list[tuple[Package, str]] = []
    for package, result in zip(packages, results, strict=True):
        if not isinstance(result, dict):
            raise AuditFailure("OSV querybatch 单项响应结构无效")
        vulnerabilities = result.get("vulns", [])
        if not isinstance(vulnerabilities, list):
            raise AuditFailure("OSV querybatch 漏洞列表结构无效")
        for vulnerability in vulnerabilities:
            if not isinstance(vulnerability, dict) or not isinstance(
                vulnerability.get("id"), str
            ):
                raise AuditFailure("OSV querybatch 漏洞标识无效")
            findings.append((package, vulnerability["id"]))
    return findings


def query_osv(packages: list[Package]) -> None:
    unique_packages = sorted(set(packages))
    request_body = json.dumps(
        {
            "queries": [
                {
                    "package": {"ecosystem": package.ecosystem, "name": package.name},
                    "version": package.version,
                }
                for package in unique_packages
            ]
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        OSV_BATCH_URL,
        data=request_body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "alert-management-quality-audit/1",
        },
        method="POST",
    )
    payload: object | None = None
    last_error: BaseException | None = None
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                payload = json.loads(response.read().decode("utf-8"))
            break
        except (
            OSError,
            TimeoutError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            urllib.error.URLError,
        ) as error:
            last_error = error
            if attempt < 3:
                time.sleep(attempt)
    if payload is None:
        raise AuditFailure("OSV querybatch 网络或响应解析失败") from last_error
    findings = parse_osv_results(unique_packages, payload)
    if findings:
        for package, identifier in sorted(set(findings)):
            print(f"[依赖漏洞] {package.coordinate} {identifier}", file=sys.stderr)
        raise AuditFailure("OSV 检出当前锁定版本的已知漏洞")
    counts: dict[str, int] = {}
    for package in unique_packages:
        counts[package.ecosystem] = counts.get(package.ecosystem, 0) + 1
    summary = "，".join(
        f"{ecosystem} {count} 项" for ecosystem, count in sorted(counts.items())
    )
    print(f"[质量审计] OSV 漏洞审计通过：{summary}")


def expect_failure(label: str, action: Callable[[], object]) -> None:
    try:
        action()
    except AuditFailure:
        return
    raise AuditFailure(f"入口自校验未能拦截：{label}")


def run_self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="alert-quality-self-test-") as temporary:
        root = Path(temporary)
        good_text = root / "good.py"
        good_text.write_text("value = 1\n", encoding="utf-8")
        check_text_files([good_text])
        bad_text = root / "bad.py"
        bad_text.write_text("value = 1 \n", encoding="utf-8")
        expect_failure("行尾空白", lambda: check_text_files([bad_text]))

        npm_dir = root / "npm"
        npm_dir.mkdir()
        package = {
            "name": "fixture",
            "version": "1.0.0",
            "dependencies": {"vue": "3.5.41"},
        }
        lock = {
            "name": "fixture",
            "version": "1.0.0",
            "lockfileVersion": 3,
            "packages": {"": package, "node_modules/vue": {"version": "3.5.41"}},
        }
        (npm_dir / "package.json").write_text(
            json.dumps(package) + "\n", encoding="utf-8"
        )
        (npm_dir / "package-lock.json").write_text(
            json.dumps(lock) + "\n", encoding="utf-8"
        )
        check_npm_lock(npm_dir)
        package["dependencies"]["vue"] = "3.5.42"
        (npm_dir / "package.json").write_text(
            json.dumps(package) + "\n", encoding="utf-8"
        )
        expect_failure("npm 锁文件漂移", lambda: check_npm_lock(npm_dir))

        pyproject = root / "pyproject.toml"
        requirements = root / "requirements.lock"
        pyproject.write_text(
            '[project]\nname = "fixture"\nversion = "1"\ndependencies = ["demo==1.0"]\n',
            encoding="utf-8",
        )
        requirements.write_text("demo==1.0\n", encoding="utf-8")
        check_python_lock(pyproject, requirements)
        requirements.write_text("demo==2.0\n", encoding="utf-8")
        expect_failure(
            "Python 锁文件漂移", lambda: check_python_lock(pyproject, requirements)
        )

        fixture_package = Package("PyPI", "demo", "1.0")
        findings = parse_osv_results(
            [fixture_package], {"results": [{"vulns": [{"id": "OSV-SELF-TEST"}]}]}
        )
        if findings != [(fixture_package, "OSV-SELF-TEST")]:
            raise AuditFailure("入口自校验未能识别 OSV 漏洞标识")
    print("[质量审计] 入口自校验通过")


def run_audit() -> None:
    run_self_test()
    source_files = tracked_source_files()
    check_text_files(source_files)
    print("[质量审计] UTF-8、行尾空白、末尾换行及 JSON/TOML 结构检查通过")
    check_script_syntax(source_files)
    print("[质量审计] Bash 与 PowerShell 脚本语法检查通过")

    npm_versions: dict[Path, dict[str, str]] = {}
    for relative in NPM_PROJECTS:
        npm_versions[relative] = check_npm_lock(REPOSITORY_ROOT / relative)
    python_packages = check_python_lock(
        REPOSITORY_ROOT / "src/algorithm/pyproject.toml",
        REPOSITORY_ROOT / "src/algorithm/requirements.lock",
    )
    print("[质量审计] npm 与 Python 锁文件一致性检查通过")

    maven_packages = run_static_checks()
    for relative in NPM_PROJECTS:
        run_npm_audit(relative, npm_versions[relative])
    query_osv([*python_packages, *maven_packages])
    print("[质量审计] 静态质量、锁文件和三生态依赖漏洞审计全部通过")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="只运行入口自身的负控校验，不执行构建或联网审计",
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    try:
        if arguments.self_test:
            run_self_test()
        else:
            run_audit()
    except AuditFailure as error:
        print(f"质量审计失败：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
