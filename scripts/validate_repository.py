#!/usr/bin/env python3
"""验证仓库基础结构、Markdown 相对链接和已跟踪的生成目录。"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_PATHS = (
    "AGENTS.md",
    "README.md",
    "CONTRIBUTING.md",
    ".gitignore",
    ".gitattributes",
    ".editorconfig",
    ".github/workflows/repository-check.yml",
    "docs/README.md",
    "docs/backgrounds",
    "docs/sources",
    "docs/product",
    "docs/architecture",
    "docs/decisions",
    "docs/planning",
    "docs/verification",
    "docs/automation",
    "docs/guides/business-user-manual.md",
    "docs/guides/windows-deployment-operations.md",
    "docs/deliverables",
    "deliverables",
    "tools/deliverables/build.py",
    "tools/deliverables/manifest.json",
    "automation/README.md",
    "automation/workflow.json",
    "automation/state.json",
    "src/README.md",
    "scripts/validate_repository.py",
    "scripts/validate_automation.py",
)
GENERATED_PARTS = {
    ".runtime",
    ".gradle",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".vite",
    "__pycache__",
    "artifacts",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "out",
    "release",
    "target",
    "venv",
}
NATIVE_RELEASE_TEMPLATE_PARTS = ("packaging", "native", "release")
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
MARKDOWN_FENCE = re.compile(r"^\s*(```|~~~)")


def validate_required_paths(errors: list[str]) -> None:
    for relative_path in REQUIRED_PATHS:
        if not (REPOSITORY_ROOT / relative_path).exists():
            errors.append(f"缺少必要路径：{relative_path}")


def extract_link_target(raw_target: str) -> str:
    target = raw_target.strip()
    if target.startswith("<"):
        closing = target.find(">")
        return target[1:closing] if closing >= 0 else target
    return target.split(maxsplit=1)[0]


def validate_markdown_links(errors: list[str]) -> None:
    markdown_paths: list[Path] = []
    for current_root, directories, files in os.walk(REPOSITORY_ROOT):
        directories[:] = sorted(
            directory
            for directory in directories
            if directory != ".git" and directory not in GENERATED_PARTS
        )
        markdown_paths.extend(
            Path(current_root) / file_name
            for file_name in files
            if file_name.endswith(".md")
        )

    for markdown_path in sorted(markdown_paths):
        relative_path = markdown_path.relative_to(REPOSITORY_ROOT)
        content = markdown_path.read_text(encoding="utf-8")
        active_fence: str | None = None
        for line_number, line in enumerate(content.splitlines(), start=1):
            fence_match = MARKDOWN_FENCE.match(line)
            if fence_match:
                marker = fence_match.group(1)
                if active_fence is None:
                    active_fence = marker
                elif marker == active_fence:
                    active_fence = None
                continue
            if active_fence is not None:
                continue
            for match in MARKDOWN_LINK.finditer(line):
                target = extract_link_target(match.group(1))
                parsed = urlsplit(target)
                if not target or target.startswith("#") or parsed.scheme or parsed.netloc:
                    continue
                decoded_path = unquote(parsed.path).replace("\\", "/")
                if not decoded_path:
                    continue
                resolved = (markdown_path.parent / decoded_path).resolve()
                try:
                    resolved.relative_to(REPOSITORY_ROOT)
                except ValueError:
                    errors.append(
                        f"Markdown 链接超出仓库：{markdown_path.relative_to(REPOSITORY_ROOT)}:"
                        f"{line_number} -> {target}"
                    )
                    continue
                if not resolved.exists():
                    errors.append(
                        f"Markdown 链接目标不存在：{markdown_path.relative_to(REPOSITORY_ROOT)}:"
                        f"{line_number} -> {target}"
                    )


def tracked_files(errors: list[str]) -> list[Path]:
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
        )
    except FileNotFoundError:
        errors.append("无法执行 Git，不能检查已跟踪文件。")
        return []
    if result.returncode != 0:
        errors.append("当前目录不是可读取的 Git 工作区。")
        return []
    return [Path(item.decode("utf-8")) for item in result.stdout.split(b"\0") if item]


def validate_tracked_files(errors: list[str]) -> None:
    for relative_path in tracked_files(errors):
        parts_to_check = relative_path.parts
        if parts_to_check[:3] == NATIVE_RELEASE_TEMPLATE_PARTS:
            parts_to_check = parts_to_check[3:]
        if any(part in GENERATED_PARTS for part in parts_to_check):
            errors.append(f"已跟踪文件位于生成或依赖目录：{relative_path.as_posix()}")


def main() -> int:
    errors: list[str] = []
    validate_required_paths(errors)
    validate_markdown_links(errors)
    validate_tracked_files(errors)

    if errors:
        print("仓库基础检查失败：", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print("仓库基础检查通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
