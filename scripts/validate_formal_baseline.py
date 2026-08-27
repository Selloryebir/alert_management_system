#!/usr/bin/env python3
"""校验正式产品身份、来源闭环和产品化阶段契约。"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FORMAL_FILES = (
    "README.md",
    "compose.yaml",
    ".github/workflows/windows-native-check.yml",
    "docs/architecture/README.md",
    "docs/automation/git-workflow.md",
    "docs/guides/business-user-manual.md",
    "docs/guides/windows-deployment-operations.md",
    "docs/deliverables/project-proposal.md",
    "docs/deliverables/midterm-report.md",
    "docs/deliverables/test-report.md",
    "docs/deliverables/closure-report.md",
    "docs/deliverables/development-process.md",
    "docs/deliverables/source-gap-analysis.md",
    "docs/deliverables/model-technical-brochure.md",
    "docs/releases/versioning.md",
    "packaging/native/release/THIRD-PARTY-NOTICES.txt",
    "packaging/native/release/README.txt",
    "packaging/native/release/config/runtime.json",
    "packaging/native/release/scripts/common.ps1",
    "scripts/native/build-release.ps1",
    "src/README.md",
    "src/algorithm/README.md",
    "src/algorithm/pyproject.toml",
    "src/backend/README.md",
    "src/backend/pom.xml",
    "src/backend/src/main/java/com/alertmanagement/backend/analysis/ReportService.java",
    "src/backend/src/main/resources/application.yml",
    "src/frontend/README.md",
    "src/frontend/index.html",
    "src/frontend/src/App.vue",
    "src/frontend/src/ReviewOperations.vue",
)
BANNED_PRODUCT_PHRASES = (
    "灾后重建",
    "重建 Demo",
    "灾后",
    "alert-management-demo",
    "源码遗失",
    "源码丢失",
    "救急",
    "抢救",
)
README_BANNED_PHRASES = (*BANNED_PRODUCT_PHRASES, "智能体", "Codex")
EXPECTED_IDENTITY_FILES = (
    "README.md",
    "compose.yaml",
    "packaging/native/release/README.txt",
    "packaging/native/release/THIRD-PARTY-NOTICES.txt",
    "packaging/native/release/config/runtime.json",
    "packaging/native/release/scripts/common.ps1",
    "src/backend/src/main/java/com/alertmanagement/backend/analysis/ReportService.java",
    "src/backend/src/main/resources/application.yml",
    "src/frontend/index.html",
    "src/frontend/src/App.vue",
)
EXPECTED_CONSTRUCT_FILES = (
    ".github/workflows/windows-native-check.yml",
    "docs/architecture/README.md",
    "scripts/native/build-release.ps1",
)
ALLOWED_DISPOSITIONS = {
    "已实现",
    "明确拒绝",
    "外部输入后才可重启",
    *(f"M{number}" for number in range(8, 15)),
}


def read(relative: str, errors: list[str]) -> str:
    path = ROOT / relative
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        errors.append(f"缺少正式基线文件：{relative}")
        return ""


def validate_identity(errors: list[str]) -> None:
    for relative in FORMAL_FILES:
        content = read(relative, errors)
        for phrase in BANNED_PRODUCT_PHRASES:
            if phrase in content:
                errors.append(f"正式路径仍包含旧产品身份：{relative} -> {phrase}")
    for relative in EXPECTED_IDENTITY_FILES:
        if "报警管理系统" not in read(relative, errors):
            errors.append(f"正式身份未覆盖：{relative}")
    for relative in EXPECTED_CONSTRUCT_FILES:
        if "alert-management-system" not in read(relative, errors):
            errors.append(f"正式构件标识未覆盖：{relative}")

    readme = read("README.md", errors)
    for phrase in README_BANNED_PHRASES:
        if phrase in readme:
            errors.append(f"根说明包含非正式产品叙事：{phrase}")
    for marker in ("v0.8.0", "M14", "docs/releases/versioning.md", "docs/guides/"):
        if marker not in readme:
            errors.append(f"根说明缺少当前基线标记：{marker}")


def validate_source_coverage(errors: list[str]) -> None:
    relative = "docs/product/source-coverage.md"
    content = read(relative, errors)
    rows: list[tuple[int, str, str]] = []
    for line in content.splitlines():
        match = re.match(r"^\| SC-(\d{3}) \|.*?\| (已实现|明确拒绝|外部输入后才可重启|M(?:8|9|10|11|12|13|14)) \|", line)
        if match:
            rows.append((int(match.group(1)), match.group(2), line))
    expected_ids = list(range(1, 58))
    actual_ids = [identifier for identifier, _, _ in rows]
    if actual_ids != expected_ids:
        errors.append(
            "来源闭环 ID 必须为 SC-001..SC-057 且各出现一次："
            f"实际 {len(actual_ids)} 项"
        )
    for identifier, disposition, line in rows:
        if disposition not in ALLOWED_DISPOSITIONS:
            errors.append(f"SC-{identifier:03d} 处置非法：{disposition}")
        if disposition == "外部输入后才可重启" and "重启条件" not in line:
            errors.append(f"SC-{identifier:03d} 缺少明确重启条件。")
    for forbidden in ("| 延期 |", "| 待定 |", "| 待补充 |", "| 待人工裁决 |"):
        if forbidden in content:
            errors.append(f"来源闭环仍包含无负责阶段状态：{forbidden.strip()}")
    for forbidden_path in (
        "docs/backgrounds",
        "docs/sources",
        "tools/document-extraction",
    ):
        if forbidden_path in content:
            errors.append(f"能力闭环仍依赖已摘除路径：{forbidden_path}")


def validate_clean_source_boundary(errors: list[str]) -> None:
    result = subprocess.run(
        [sys.executable, "scripts/release/export_clean_source.py", "--check"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        reason = (result.stderr or result.stdout).strip()
        errors.append(f"正式源码导出边界检查失败：{reason}")


def validate_workflow(errors: list[str]) -> None:
    workflow_path = ROOT / "automation/workflow.json"
    state_path = ROOT / "automation/state.json"
    try:
        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        errors.append(f"无法读取自动化契约：{exc}")
        return
    stage_ids = [stage.get("id") for stage in workflow.get("stages", [])]
    if stage_ids != [f"M{number}" for number in range(15)]:
        errors.append("产品化工作流必须完整定义 M0..M14。")
    current_stage = state.get("current_stage")
    productization_stages = {f"M{number}" for number in range(8, 15)}
    if current_stage not in productization_stages:
        errors.append("正式产品基线校验仅适用于 M8..M14 产品化阶段。")
    for number in range(8, 15):
        stage_id = f"M{number}"
        if stage_id not in state.get("stages", {}):
            errors.append(f"状态文件缺少产品化阶段：{stage_id}")
    requirements = read("docs/product/requirements.md", errors)
    for number in range(28, 37):
        if f"PRD-{number:03d}" not in requirements:
            errors.append(f"需求矩阵缺少产品化需求：PRD-{number:03d}")


def main() -> int:
    errors: list[str] = []
    validate_identity(errors)
    validate_source_coverage(errors)
    validate_workflow(errors)
    validate_clean_source_boundary(errors)
    if errors:
        print("正式产品基线校验失败：", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("正式产品基线校验通过：产品身份、57 项需求能力和 M8–M14 阶段均已闭环。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
