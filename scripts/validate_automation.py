#!/usr/bin/env python3
"""校验自动化工作流及状态，并可输出当前阶段摘要。"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = REPOSITORY_ROOT / "automation" / "workflow.json"
STATE_PATH = REPOSITORY_ROOT / "automation" / "state.json"
ALLOWED_STATUSES = {
    "blocked",
    "ready",
    "in_progress",
    "review",
    "passed",
    "failed",
    "awaiting_human",
    "blocked_external",
}
ACTIVE_STATUSES = {"ready", "in_progress", "review", "failed"}
PAUSED_STATUSES = {"awaiting_human", "blocked_external"}
CURRENT_STATUSES = ACTIVE_STATUSES | PAUSED_STATUSES
COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
REQUIREMENT_ID = re.compile(r"^PRD-\d{3}$")
EVIDENCE_PREFIX = "docs/verification/evidence/"


def load_json(path: Path, errors: list[str]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        errors.append(f"文件不存在：{path.relative_to(REPOSITORY_ROOT)}")
        return {}
    except json.JSONDecodeError as exc:
        errors.append(
            f"JSON 无效：{path.relative_to(REPOSITORY_ROOT)}:{exc.lineno}:{exc.colno}"
        )
        return {}
    if not isinstance(value, dict):
        errors.append(f"JSON 顶层必须是对象：{path.relative_to(REPOSITORY_ROOT)}")
        return {}
    return value


def require_existing_file(raw_path: Any, label: str, errors: list[str]) -> None:
    if not isinstance(raw_path, str) or not raw_path.strip():
        errors.append(f"{label} 必须是非空路径。")
        return
    candidate = (REPOSITORY_ROOT / raw_path).resolve()
    try:
        candidate.relative_to(REPOSITORY_ROOT)
    except ValueError:
        errors.append(f"{label} 超出仓库：{raw_path}")
        return
    if not candidate.is_file():
        errors.append(f"{label} 文件不存在：{raw_path}")


def validate_commit(commit: Any, label: str, errors: list[str]) -> None:
    if not isinstance(commit, str) or not COMMIT_SHA.fullmatch(commit):
        errors.append(f"{label} 必须为完整提交 SHA。")
        return
    exists = subprocess.run(
        ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
    )
    if exists.returncode != 0:
        errors.append(f"{label} 不是当前仓库中的提交：{commit}")
        return
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", commit, "HEAD"],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
    )
    if ancestor.returncode != 0:
        errors.append(f"{label} 不是当前 HEAD 的祖先：{commit}")


def validate_workflow(workflow: dict[str, Any], errors: list[str]) -> list[dict[str, Any]]:
    if workflow.get("schema_version") != 1:
        errors.append("workflow.schema_version 必须为 1。")
    if not isinstance(workflow.get("workflow_id"), str) or not workflow["workflow_id"]:
        errors.append("workflow.workflow_id 必须是非空字符串。")
    if not isinstance(workflow.get("name"), str) or not workflow["name"].strip():
        errors.append("workflow.name 必须是非空字符串。")

    prompt_train = workflow.get("prompt_train")
    required_prompts = {"start_goal", "controller", "stage_worker", "reviewer"}
    if not isinstance(prompt_train, dict):
        errors.append("workflow.prompt_train 必须是对象。")
    else:
        missing_prompts = sorted(required_prompts - set(prompt_train))
        if missing_prompts:
            errors.append(f"提示链缺少角色：{', '.join(missing_prompts)}")
        for name, prompt_path in prompt_train.items():
            require_existing_file(prompt_path, f"提示文件 {name}", errors)

    global_references = workflow.get("global_references")
    if not isinstance(global_references, list) or not global_references:
        errors.append("workflow.global_references 必须是非空数组。")
    else:
        for index, reference in enumerate(global_references):
            require_existing_file(reference, f"全局引用[{index}]", errors)

    stages = workflow.get("stages")
    if not isinstance(stages, list) or not stages:
        errors.append("workflow.stages 必须是非空数组。")
        return []

    known_ids: set[str] = set()
    for index, stage in enumerate(stages):
        if not isinstance(stage, dict):
            errors.append(f"stages[{index}] 必须是对象。")
            continue
        stage_id = stage.get("id")
        expected_id = f"M{index}"
        if stage_id != expected_id:
            errors.append(f"阶段顺序错误：stages[{index}].id 应为 {expected_id}。")
        if not isinstance(stage_id, str) or not stage_id:
            errors.append(f"stages[{index}].id 必须是非空字符串。")
            continue
        if stage_id in known_ids:
            errors.append(f"阶段 ID 重复：{stage_id}")
        if not isinstance(stage.get("name"), str) or not stage["name"].strip():
            errors.append(f"{stage_id}.name 必须是非空字符串。")

        dependencies = stage.get("depends_on")
        if not isinstance(dependencies, list):
            errors.append(f"{stage_id}.depends_on 必须是数组。")
            dependencies = []
        for dependency in dependencies:
            if dependency not in known_ids:
                errors.append(f"{stage_id} 依赖不存在或位于其后：{dependency}")
        if index == 0 and dependencies:
            errors.append("M0 不应依赖其他阶段。")
        if index > 0 and expected_id == stage_id and dependencies != [f"M{index - 1}"]:
            errors.append(f"{stage_id} 必须且只能直接依赖 M{index - 1}。")

        references = stage.get("references")
        if not isinstance(references, list) or not references:
            errors.append(f"{stage_id}.references 必须是非空数组。")
        else:
            for reference_index, reference in enumerate(references):
                require_existing_file(
                    reference, f"{stage_id} 引用[{reference_index}]", errors
                )

        acceptance = stage.get("acceptance")
        if not isinstance(acceptance, dict):
            errors.append(f"{stage_id}.acceptance 必须是对象。")
        else:
            criteria = acceptance.get("criteria")
            commands = acceptance.get("commands")
            if not isinstance(criteria, list) or not criteria or not all(
                isinstance(item, str) and item.strip() for item in criteria
            ):
                errors.append(f"{stage_id} 验收标准必须是非空字符串数组。")
            if not isinstance(commands, list) or not commands or not all(
                isinstance(item, str) and item.strip() for item in commands
            ):
                errors.append(f"{stage_id} 验收命令必须是非空字符串数组。")
        known_ids.add(stage_id)

    return [stage for stage in stages if isinstance(stage, dict)]


def validate_state(
    workflow: dict[str, Any],
    stages: list[dict[str, Any]],
    state: dict[str, Any],
    errors: list[str],
) -> None:
    if state.get("schema_version") != 1:
        errors.append("state.schema_version 必须为 1。")
    if state.get("workflow_id") != workflow.get("workflow_id"):
        errors.append("state.workflow_id 与工作流不一致。")

    stage_ids = [stage.get("id") for stage in stages if isinstance(stage.get("id"), str)]
    state_stages = state.get("stages")
    if not isinstance(state_stages, dict):
        errors.append("state.stages 必须是对象。")
        return
    if set(state_stages) != set(stage_ids):
        missing = sorted(set(stage_ids) - set(state_stages))
        extra = sorted(set(state_stages) - set(stage_ids))
        if missing:
            errors.append(f"state.stages 缺少阶段：{', '.join(missing)}")
        if extra:
            errors.append(f"state.stages 包含未知阶段：{', '.join(extra)}")

    current_stage = state.get("current_stage")
    if current_stage not in stage_ids:
        errors.append(f"current_stage 无效：{current_stage}")

    active_ids: list[str] = []
    stage_by_id = {stage["id"]: stage for stage in stages if "id" in stage}
    for stage_id in stage_ids:
        stage_state = state_stages.get(stage_id)
        if not isinstance(stage_state, dict):
            errors.append(f"{stage_id} 状态必须是对象。")
            continue
        status = stage_state.get("status")
        if status not in ALLOWED_STATUSES:
            errors.append(f"{stage_id} 状态非法：{status}")
            continue
        if status in CURRENT_STATUSES:
            active_ids.append(stage_id)
        attempt = stage_state.get("attempt")
        if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 0:
            errors.append(f"{stage_id}.attempt 必须是非负整数。")
        evidence_files = stage_state.get("evidence_files")
        if not isinstance(evidence_files, list):
            errors.append(f"{stage_id}.evidence_files 必须是数组。")
        else:
            if status == "passed" and not evidence_files:
                errors.append(f"{stage_id} 已通过但没有证据文件。")
            for index, evidence_file in enumerate(evidence_files):
                require_existing_file(
                    evidence_file, f"{stage_id} 证据[{index}]", errors
                )
                if status == "passed" and (
                    not isinstance(evidence_file, str)
                    or not evidence_file.startswith(EVIDENCE_PREFIX)
                    or not evidence_file.endswith(".md")
                ):
                    errors.append(
                        f"{stage_id} 证据必须是 {EVIDENCE_PREFIX} 下的 Markdown："
                        f"{evidence_file}"
                    )
        checkpoint_commit = stage_state.get("checkpoint_commit")
        remote_verified = stage_state.get("remote_verified")
        next_action = stage_state.get("next_action")
        if checkpoint_commit is not None and not (
            isinstance(checkpoint_commit, str) and COMMIT_SHA.fullmatch(checkpoint_commit)
        ):
            errors.append(f"{stage_id}.checkpoint_commit 必须为完整提交 SHA 或 null。")
        if not isinstance(remote_verified, bool):
            errors.append(f"{stage_id}.remote_verified 必须是布尔值。")
        if not isinstance(next_action, str) or not next_action.strip():
            errors.append(f"{stage_id}.next_action 必须是非空字符串。")
        if status == "passed":
            if not checkpoint_commit:
                errors.append(f"{stage_id} 已通过但没有检查点提交。")
            else:
                validate_commit(checkpoint_commit, f"{stage_id}.checkpoint_commit", errors)
                for evidence_file in evidence_files:
                    if not isinstance(evidence_file, str):
                        continue
                    evidence_path = REPOSITORY_ROOT / evidence_file
                    if evidence_path.is_file() and evidence_path.suffix == ".md":
                        content = evidence_path.read_text(encoding="utf-8")
                        if checkpoint_commit not in content:
                            errors.append(
                                f"{stage_id} 证据未引用检查点提交：{evidence_file}"
                            )
            if remote_verified is not True:
                errors.append(f"{stage_id} 已通过但未核验远端检查点。")
        elif checkpoint_commit is not None or remote_verified is not False:
            errors.append(f"{stage_id} 未通过时不得声明已核验检查点。")
        dependencies = stage_by_id[stage_id].get("depends_on", [])
        if status in CURRENT_STATUSES or status == "passed":
            for dependency in dependencies:
                dependency_state = state_stages.get(dependency, {})
                if dependency_state.get("status") != "passed":
                    errors.append(f"{stage_id} 已解除阻塞，但依赖 {dependency} 未通过。")

    all_passed = bool(stage_ids) and all(
        isinstance(state_stages.get(stage_id), dict)
        and state_stages[stage_id].get("status") == "passed"
        for stage_id in stage_ids
    )
    if all_passed:
        if active_ids:
            errors.append("工作流已全部通过，但仍存在可处理阶段。")
        if current_stage != stage_ids[-1]:
            errors.append("工作流终态的 current_stage 必须保留最后阶段。")
    else:
        if len(active_ids) != 1:
            errors.append(
                "必须恰有一个可处理或暂停阶段，当前为："
                f"{', '.join(active_ids) or '无'}"
            )
        elif current_stage != active_ids[0]:
            errors.append(
                f"current_stage={current_stage}，但唯一当前阶段为 {active_ids[0]}。"
            )

        if current_stage in state_stages and isinstance(state_stages[current_stage], dict):
            if state_stages[current_stage].get("status") not in CURRENT_STATUSES:
                errors.append("current_stage 必须处于可处理或暂停状态。")

    active_run = state.get("active_run")
    current_state = state_stages.get(current_stage, {})
    current_status = (
        current_state.get("status") if isinstance(current_state, dict) else None
    )
    if current_status == "ready" or all_passed:
        if active_run is not None:
            errors.append("ready 或工作流终态不得保留 active_run。")
        return
    if current_status not in CURRENT_STATUSES:
        return
    if not isinstance(active_run, dict):
        errors.append("活动或暂停阶段必须提供 active_run 恢复记录。")
        return

    if active_run.get("stage") != current_stage:
        errors.append("active_run.stage 必须等于 current_stage。")
    validate_commit(active_run.get("base_commit"), "active_run.base_commit", errors)
    requirements = active_run.get("requirements")
    if not isinstance(requirements, list) or not requirements or not all(
        isinstance(item, str) and REQUIREMENT_ID.fullmatch(item)
        for item in requirements
    ):
        errors.append("active_run.requirements 必须是非空 PRD-* 数组。")
    allowed_paths = active_run.get("allowed_paths")
    if not isinstance(allowed_paths, list) or not allowed_paths or not all(
        isinstance(item, str) and item.strip() for item in allowed_paths
    ):
        errors.append("active_run.allowed_paths 必须是非空路径数组。")
    for field in ("checks", "failures"):
        if not isinstance(active_run.get(field), list):
            errors.append(f"active_run.{field} 必须是数组。")
    for field in ("decision", "next_action"):
        value = active_run.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"active_run.{field} 必须是非空字符串。")
    resume_status = active_run.get("resume_status")
    if current_status in PAUSED_STATUSES:
        if resume_status not in ACTIVE_STATUSES:
            errors.append("暂停状态必须记录合法的 active_run.resume_status。")
    elif resume_status is not None:
        errors.append("非暂停状态的 active_run.resume_status 必须为 null。")


def print_status(workflow: dict[str, Any], state: dict[str, Any]) -> None:
    current_id = state["current_stage"]
    current_stage = next(stage for stage in workflow["stages"] if stage["id"] == current_id)
    current_state = state["stages"][current_id]
    print(f"工作流：{workflow['name']} ({workflow['workflow_id']})")
    print(f"当前阶段：{current_id} {current_stage['name']}")
    print(f"状态：{current_state['status']}，尝试次数：{current_state['attempt']}")
    print("依赖：" + (", ".join(current_stage["depends_on"]) or "无"))
    print(f"下一动作：{current_state['next_action']}")
    print("验收命令：")
    for command in current_stage["acceptance"]["commands"]:
        print(f"- {command}")
    print("阶段概览：")
    for stage in workflow["stages"]:
        print(f"- {stage['id']} {state['stages'][stage['id']]['status']}: {stage['name']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", action="store_true", help="输出当前阶段摘要")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    errors: list[str] = []
    workflow = load_json(WORKFLOW_PATH, errors)
    state = load_json(STATE_PATH, errors)
    stages = validate_workflow(workflow, errors) if workflow else []
    if workflow and state and stages:
        validate_state(workflow, stages, state, errors)

    if errors:
        print("自动化工作流校验失败：", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print("自动化工作流校验通过。")
    if args.status:
        print_status(workflow, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
