#!/usr/bin/env python3
"""Fail-closed validation for the M14 release candidate and post-main release."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
RC_VERSION = "1.0.0-rc.1"
RC_TAG = f"v{RC_VERSION}"
FINAL_TAG = "v1.0.0"
EXPECTED_DOCUMENT_SOURCES = {
    "docs/guides/business-user-manual.md",
    "docs/guides/windows-deployment-operations.md",
    "docs/deliverables/project-proposal.md",
    "docs/deliverables/midterm-report.md",
    "docs/deliverables/test-report.md",
    "docs/deliverables/closure-report.md",
    "docs/deliverables/development-process.md",
    "docs/deliverables/source-gap-analysis.md",
}


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, cwd=ROOT, text=True, capture_output=True, check=False)
    if check and result.returncode:
        raise ValueError(f"命令失败（{result.returncode}）：{' '.join(args)}\n{result.stderr.strip()}")
    return result


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_repository_file(relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        raise ValueError(f"交付物路径不安全：{relative}")
    path = ROOT.joinpath(*pure.parts)
    if path.is_symlink():
        raise ValueError(f"交付物路径不得是符号链接：{relative}")
    resolved = path.resolve(strict=True)
    if ROOT.resolve() not in resolved.parents:
        raise ValueError(f"交付物路径越界：{relative}")
    if not resolved.is_file():
        raise ValueError(f"交付物不是普通文件：{relative}")
    return resolved


def normalize_disposition(value: str) -> str:
    value = value.strip()
    if value.startswith("已实现"):
        return "已实现"
    if value in {"外部输入后才可重启", "外部条件后重启"}:
        return "外部条件后重启"
    if value == "明确拒绝" or re.fullmatch(r"M(?:8|9|10|11|12|13|14)", value):
        return value
    raise ValueError(f"未知来源处置：{value}")


def parse_matrix(path: Path, disposition_column: int) -> dict[str, str]:
    rows: dict[str, str] = {}
    try:
        display_path = str(path.relative_to(ROOT))
    except ValueError:
        display_path = str(path)
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| SC-"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) <= disposition_column or not re.fullmatch(r"SC-\d{3}", cells[0]):
            raise ValueError(f"来源矩阵行格式无效：{display_path} -> {line}")
        identifier = cells[0]
        if identifier in rows:
            raise ValueError(f"来源矩阵 ID 重复：{display_path} -> {identifier}")
        rows[identifier] = normalize_disposition(cells[disposition_column])
    expected = {f"SC-{number:03d}" for number in range(1, 58)}
    if set(rows) != expected:
        missing = sorted(expected - set(rows))
        extra = sorted(set(rows) - expected)
        raise ValueError(f"来源矩阵必须恰好包含 SC-001..SC-057；缺少 {missing}，多出 {extra}")
    return rows


def validate_git(mode: str, expected_commit: str | None) -> str:
    status = run("git", "status", "--porcelain", "--untracked-files=all").stdout
    if status:
        raise ValueError("发布候选校验拒绝脏工作区。")
    head = run("git", "rev-parse", "HEAD").stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise ValueError("当前提交不是完整 40 位 SHA。")
    github_sha = os.getenv("GITHUB_SHA")
    if github_sha and github_sha != head:
        raise ValueError(f"GITHUB_SHA 与 HEAD 不一致：{github_sha} != {head}")
    if expected_commit and expected_commit != head:
        raise ValueError(f"指定发布提交与 HEAD 不一致：{expected_commit} != {head}")
    m13 = json.loads((ROOT / "automation/state.json").read_text(encoding="utf-8"))["stages"]["M13"]["checkpoint_commit"]
    if run("git", "merge-base", "--is-ancestor", m13, head, check=False).returncode:
        raise ValueError("M13 检查点不是当前候选的祖先。")
    for tag in (RC_TAG, FINAL_TAG):
        exists = run("git", "rev-parse", "--verify", f"refs/tags/{tag}", check=False).returncode == 0
        if mode == "candidate" and exists:
            raise ValueError(f"候选阶段禁止提前存在标签：{tag}")
        if mode == "post-main" and tag == FINAL_TAG and exists:
            raise ValueError("RC 阶段禁止提前存在正式标签 v1.0.0。")
    return head


def validate_state(mode: str) -> None:
    state = json.loads((ROOT / "automation/state.json").read_text(encoding="utf-8"))
    for number in range(14):
        stage_id = f"M{number}"
        stage = state["stages"].get(stage_id, {})
        if stage.get("status") != "passed" or not stage.get("remote_verified"):
            raise ValueError(f"{stage_id} 尚未形成远端验证通过的检查点。")
        checkpoint = stage.get("checkpoint_commit")
        if not isinstance(checkpoint, str) or not re.fullmatch(r"[0-9a-f]{40}", checkpoint):
            raise ValueError(f"{stage_id} 检查点不是完整提交。")
        evidence = stage.get("evidence_files")
        if not evidence or any(not (ROOT / item).is_file() for item in evidence):
            raise ValueError(f"{stage_id} 缺少已提交证据文件。")
    m14 = state["stages"].get("M14", {})
    allowed = {
        "candidate": {"in_progress", "review", "awaiting_human"},
        "post-main": {"review", "awaiting_human"},
        "released": {"passed"},
    }[mode]
    if m14.get("status") not in allowed:
        raise ValueError(f"{mode} 模式下 M14 状态非法：{m14.get('status')}")


def validate_matrices(mode: str) -> None:
    coverage = parse_matrix(ROOT / "docs/product/source-coverage.md", 3)
    gap = parse_matrix(ROOT / "docs/deliverables/source-gap-analysis.md", 2)
    if coverage != gap:
        differences = [key for key in coverage if coverage[key] != gap[key]]
        raise ValueError(f"两份来源闭环矩阵处置不一致：{differences}")
    staged = {key: value for key, value in coverage.items() if re.fullmatch(r"M(?:8|9|10|11|12|13|14)", value)}
    expected = {"SC-042": "M14"} if mode in {"candidate", "post-main"} else {}
    if staged != expected:
        raise ValueError(f"来源能力仍有悬空阶段责任：实际 {staged}，期望 {expected}")
    for relative in ("README.md", "docs/deliverables/source-gap-analysis.md", "docs/deliverables/development-process.md"):
        content = (ROOT / relative).read_text(encoding="utf-8")
        for stale in ("M13 正在", "M13 当前阶段", "当前执行 M13", "M13 门槛通过前", "当前阻塞于 M13"):
            if stale in content:
                raise ValueError(f"正式说明仍包含失效的 M13 当前态：{relative} -> {stale}")


def validate_deliverables() -> None:
    manifest = json.loads((ROOT / "deliverables/manifest.json").read_text(encoding="utf-8"))
    documents = manifest.get("documents")
    if not isinstance(documents, list) or len(documents) != 8:
        raise ValueError("正式交付清单必须登记恰好 8 个文档源。")
    sources: set[str] = set()
    outputs: set[str] = set()
    for document in documents:
        source = document.get("source")
        if source in sources:
            raise ValueError(f"正式交付源重复：{source}")
        sources.add(source)
        source_path = safe_repository_file(source)
        if sha256(source_path) != document.get("source_sha256"):
            raise ValueError(f"正式交付源哈希不匹配：{source}")
        if set(document.get("outputs", {})) != {"docx", "pdf"}:
            raise ValueError(f"正式交付输出类型不完整：{source}")
        for output in document["outputs"].values():
            relative = output.get("path")
            if relative in outputs:
                raise ValueError(f"正式交付输出重复：{relative}")
            outputs.add(relative)
            path = safe_repository_file(relative)
            if path.stat().st_size != output.get("size") or sha256(path) != output.get("sha256"):
                raise ValueError(f"正式交付输出大小或哈希不匹配：{relative}")
    if sources != EXPECTED_DOCUMENT_SOURCES or len(outputs) != 16:
        raise ValueError("正式交付清单来源或 16 个输出不符合冻结集合。")
    font = manifest.get("font", {})
    for path_key, hash_key in (("path", "sha256"), ("license_path", "license_sha256")):
        path = safe_repository_file(font.get(path_key, ""))
        if sha256(path) != font.get(hash_key):
            raise ValueError(f"正式交付字体依赖哈希不匹配：{font.get(path_key)}")
    check = run(sys.executable, "tools/deliverables/build.py", "--check", check=False)
    if check.returncode:
        raise ValueError(f"正式 DOCX/PDF 生成物存在漂移：\n{check.stdout}{check.stderr}")


def validate_release_contract() -> None:
    for relative in (
        "scripts/native/build-release.ps1",
        "scripts/native/verify-release.ps1",
        "scripts/native/verify-release-as-standard-user.ps1",
        "scripts/release/verify-business-release.ps1",
    ):
        content = (ROOT / relative).read_text(encoding="utf-8-sig")
        if f'"{RC_VERSION}"' not in content:
            raise ValueError(f"发布入口未冻结 RC 版本：{relative}")
    workflow = json.loads((ROOT / "automation/workflow.json").read_text(encoding="utf-8"))
    commands = next(stage for stage in workflow["stages"] if stage["id"] == "M14")["acceptance"]["commands"]
    required = {
        "powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/release/verify-business-release.ps1",
        "python3 tests/smoke/run.py --target docker --fresh-volume",
        "python3 scripts/validate_release_candidate.py",
    }
    if set(commands) != required:
        raise ValueError("M14 固定验收命令发生漂移。")
    workflow_markers = {
        ".github/workflows/windows-native-check.yml": "scripts/release/verify-business-release.ps1",
        ".github/workflows/docker-compose-check.yml": "python3 tests/smoke/run.py --target docker --fresh-volume",
        ".github/workflows/repository-check.yml": "python3 scripts/validate_release_candidate.py",
    }
    for relative, marker in workflow_markers.items():
        if marker not in (ROOT / relative).read_text(encoding="utf-8"):
            raise ValueError(f"远端工作流缺少 M14 固定入口：{relative} -> {marker}")


def validate_published_tag(release_commit: str, require_head: bool) -> None:
    tag_type = run("git", "cat-file", "-t", RC_TAG).stdout.strip()
    target = run("git", "rev-parse", f"{RC_TAG}^{{}}").stdout.strip()
    if tag_type != "tag" or target != release_commit:
        raise ValueError("RC 标签必须是 annotated tag 并精确指向记录的 main 发布提交。")
    if require_head and run("git", "rev-parse", "HEAD").stdout.strip() != release_commit:
        raise ValueError("post-main 校验必须在精确 main 发布提交上执行。")
    remote = run("git", "ls-remote", "origin", "refs/heads/main", "refs/heads/dev", f"refs/tags/{RC_TAG}", f"refs/tags/{RC_TAG}^{{}}").stdout
    refs = {line.split("\t", 1)[1]: line.split("\t", 1)[0] for line in remote.splitlines() if "\t" in line}
    if refs.get("refs/heads/main") != release_commit or refs.get(f"refs/tags/{RC_TAG}^{{}}") != release_commit:
        raise ValueError("远端 main 或 RC peeled tag 未绑定记录的发布提交。")
    remote_dev = refs.get("refs/heads/dev")
    if not remote_dev or run("git", "merge-base", "--is-ancestor", release_commit, remote_dev, check=False).returncode:
        raise ValueError("远端 dev 尚未可达 main 发布提交。")


def main() -> int:
    parser = argparse.ArgumentParser(description="校验报警管理系统 M14 发布候选")
    parser.add_argument("--mode", choices=("auto", "candidate", "post-main", "released"), default="auto")
    parser.add_argument("--expected-commit")
    args = parser.parse_args()
    try:
        state = json.loads((ROOT / "automation/state.json").read_text(encoding="utf-8"))
        mode = args.mode
        if mode == "auto":
            mode = "released" if state["stages"]["M14"].get("status") == "passed" else "candidate"
        head = validate_git(mode, args.expected_commit)
        validate_state(mode)
        validate_matrices(mode)
        validate_deliverables()
        validate_release_contract()
        if mode == "post-main":
            validate_published_tag(head, require_head=True)
        elif mode == "released":
            release_commit = state["stages"]["M14"].get("checkpoint_commit")
            if not isinstance(release_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", release_commit):
                raise ValueError("M14 passed 状态缺少完整 main 发布检查点。")
            validate_published_tag(release_commit, require_head=False)
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"M14 发布候选校验失败：{exc}", file=sys.stderr)
        return 1
    print(f"M14 发布候选校验通过：mode={mode}，commit={head}，version={RC_VERSION}。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
