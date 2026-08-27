#!/usr/bin/env python3
"""从干净提交生成可复现、最小披露的正式源码 ZIP。"""

from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import re
import subprocess
from pathlib import Path, PurePosixPath
from urllib.parse import unquote
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE_ROOT = "alert-management-system-source"
MANIFEST_NAME = "SOURCE-MANIFEST.json"
FIXED_TIME = (2026, 1, 1, 0, 0, 0)
EXCLUDED_PREFIXES = (
    ".git/",
    ".runtime/",
    "automation/",
    "docs/automation/",
    "docs/backgrounds/",
    "docs/planning/",
    "docs/sources/",
    "docs/verification/",
    "tools/document-extraction/",
)
EXCLUDED_FILES = {
    "AGENTS.md",
    "docs/planning/agent-collaboration.md",
    "scripts/release/export_clean_source.py",
    "scripts/validate_automation.py",
    "scripts/validate_formal_baseline.py",
    "scripts/validate_release_candidate.py",
}
PUBLIC_NARRATIVE_FORBIDDEN = (
    "救急",
    "源码遗失",
    "智能体",
    "Codex",
    "研发事故",
    "PDF 占位代码",
    "提取清单",
)
MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
CACHE_PARTS = {
    ".gradle",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".vite",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "venv",
}


def git(*arguments: str, text: bool = True) -> str | bytes:
    result = subprocess.run(
        ["git", *arguments], cwd=ROOT, check=False, capture_output=True, text=text
    )
    if result.returncode != 0:
        reason = (
            result.stderr.strip()
            if text
            else result.stderr.decode(errors="replace").strip()
        )
        raise SystemExit(f"Git 命令失败：git {' '.join(arguments)}：{reason}")
    return result.stdout


def excluded(path: str) -> bool:
    pure = PurePosixPath(path)
    return (
        path in EXCLUDED_FILES
        or any(path.startswith(prefix) for prefix in EXCLUDED_PREFIXES)
        or any(part in CACHE_PARTS for part in pure.parts)
    )


def index_paths() -> list[str]:
    raw = git("ls-files", "-z", text=False)
    assert isinstance(raw, bytes)
    return sorted(item.decode("utf-8") for item in raw.split(b"\0") if item)


def commit_paths(commit: str) -> list[str]:
    raw = git("ls-tree", "-r", "--name-only", "-z", commit, text=False)
    assert isinstance(raw, bytes)
    return sorted(item.decode("utf-8") for item in raw.split(b"\0") if item)


def commit_modes(commit: str) -> dict[str, int]:
    raw = git("ls-tree", "-r", "-z", commit, text=False)
    assert isinstance(raw, bytes)
    modes: dict[str, int] = {}
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        metadata, path_bytes = entry.split(b"\t", 1)
        mode_text, object_type, _object_id = metadata.decode("ascii").split(" ")
        if object_type != "blob" or mode_text not in {"100644", "100755"}:
            raise SystemExit(f"正式源码包不支持 Git 对象模式：{mode_text} {object_type}")
        modes[path_bytes.decode("utf-8")] = int(mode_text[-3:], 8)
    return modes


def validate_selection(paths: list[str]) -> list[str]:
    selected = [path for path in paths if not excluded(path)]
    if not selected:
        raise SystemExit("正式源码选择为空。")
    leaked = [path for path in selected if excluded(path)]
    if leaked:
        raise SystemExit(f"正式源码选择包含禁止路径：{leaked[0]}")
    for forbidden in (
        "docs/backgrounds/",
        "docs/sources/",
        "tools/document-extraction/",
    ):
        tracked = [path for path in paths if path.startswith(forbidden)]
        if tracked:
            raise SystemExit(f"当前 Git 树仍跟踪禁止来源路径：{tracked[0]}")
    return selected


def validate_public_documents(contents: dict[str, bytes]) -> None:
    selected = set(contents)
    for path, content in contents.items():
        if not path.endswith(".md"):
            continue
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SystemExit(f"公开 Markdown 不是 UTF-8：{path}") from exc
        for forbidden in PUBLIC_NARRATIVE_FORBIDDEN:
            if forbidden.casefold() in text.casefold():
                raise SystemExit(f"公开 Markdown 包含禁止叙事词：{path}：{forbidden}")
        for raw_target in MARKDOWN_LINK.findall(text):
            target = raw_target.strip().strip("<>").split("#", 1)[0].strip()
            if not target or re.match(
                r"^[a-z][a-z0-9+.-]*:", target, re.IGNORECASE
            ):
                continue
            normalized = posixpath.normpath(
                posixpath.join(posixpath.dirname(path), unquote(target))
            )
            if normalized.startswith("../") or normalized == "..":
                raise SystemExit(f"公开 Markdown 链接越出源码根目录：{path} -> {raw_target}")
            if normalized in selected:
                continue
            directory = normalized.rstrip("/") + "/"
            if any(candidate.startswith(directory) for candidate in selected):
                continue
            raise SystemExit(f"公开 Markdown 存在断链：{path} -> {raw_target}")


def clean_head() -> str:
    status = git("status", "--porcelain=v1", "--untracked-files=all")
    assert isinstance(status, str)
    if status:
        raise SystemExit("工作树或索引不干净，拒绝生成正式源码包。")
    head = git("rev-parse", "HEAD")
    assert isinstance(head, str)
    return head.strip()


def blob(commit: str, path: str) -> bytes:
    value = git("show", f"{commit}:{path}", text=False)
    assert isinstance(value, bytes)
    return value


def zip_info(name: str, mode: int = 0o644) -> ZipInfo:
    info = ZipInfo(name, date_time=FIXED_TIME)
    info.compress_type = ZIP_DEFLATED
    info.external_attr = (0o100000 | mode) << 16
    return info


def build_manifest(commit: str, contents: dict[str, bytes], modes: dict[str, int]) -> bytes:
    manifest = {
        "schema_version": 1,
        "product": "报警管理系统",
        "source_commit": commit,
        "archive_root": ARCHIVE_ROOT,
        "files": [
            {
                "path": path,
                "mode": f"{modes[path]:04o}",
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
            for path, content in sorted(contents.items())
        ],
        "disclosure_profile": "formal-source-v1",
    }
    return (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def validate_archive(path: Path, manifest: dict[str, object]) -> None:
    with ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise SystemExit("源码包包含重复条目。")
        prefix = f"{ARCHIVE_ROOT}/"
        if any(not name.startswith(prefix) for name in names):
            raise SystemExit("源码包存在根目录之外的条目。")
        relative_names = [name.removeprefix(prefix) for name in names]
        leaked = [name for name in relative_names if excluded(name)]
        if leaked:
            raise SystemExit(f"源码包包含禁止路径：{leaked[0]}")
        declared = manifest.get("files")
        if not isinstance(declared, list):
            raise SystemExit("源码清单 files 无效。")
        for item in declared:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                raise SystemExit("源码清单文件项无效。")
            content = archive.read(prefix + item["path"])
            expected_mode = item.get("mode")
            actual_mode = (archive.getinfo(prefix + item["path"]).external_attr >> 16) & 0o777
            if expected_mode not in {"0644", "0755"} or actual_mode != int(expected_mode, 8):
                raise SystemExit(f"源码包文件权限不一致：{item['path']}")
            if len(content) != item.get("size"):
                raise SystemExit(f"源码包文件大小不一致：{item['path']}")
            if hashlib.sha256(content).hexdigest() != item.get("sha256"):
                raise SystemExit(f"源码包文件摘要不一致：{item['path']}")
        expected = sorted([item["path"] for item in declared] + [MANIFEST_NAME])
        if sorted(relative_names) != expected:
            raise SystemExit("源码包实际文件与精确清单不一致。")


def export(output: Path) -> None:
    commit = clean_head()
    paths = validate_selection(commit_paths(commit))
    output = output.resolve()
    if output.suffix.lower() != ".zip":
        raise SystemExit("--output 必须是 .zip 文件。")
    if output.exists():
        raise SystemExit("输出文件已存在，拒绝覆盖。")
    if not output.parent.is_dir() or output.parent.is_symlink():
        raise SystemExit("输出父目录必须是已存在的普通目录。")
    contents = {path: blob(commit, path) for path in paths}
    validate_public_documents(contents)
    tree_modes = commit_modes(commit)
    modes = {path: tree_modes[path] for path in paths}
    manifest_bytes = build_manifest(commit, contents, modes)
    manifest = json.loads(manifest_bytes)
    with ZipFile(output, "x") as archive:
        for path, content in sorted(contents.items()):
            archive.writestr(zip_info(f"{ARCHIVE_ROOT}/{path}", modes[path]), content)
        archive.writestr(zip_info(f"{ARCHIVE_ROOT}/{MANIFEST_NAME}"), manifest_bytes)
    try:
        validate_archive(output, manifest)
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    print(f"正式源码包已生成：{output}")
    print(f"来源提交：{commit}")
    print(f"文件数：{len(contents)}")
    print(f"ZIP SHA-256：{hashlib.sha256(output.read_bytes()).hexdigest()}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只检查当前索引的选择边界")
    parser.add_argument("--output", type=Path, help="新的正式源码 ZIP 路径")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.check:
        if args.output is not None:
            raise SystemExit("--check 与 --output 不能同时使用。")
        selected = validate_selection(index_paths())
        contents = {path: git("show", f":{path}", text=False) for path in selected}
        assert all(isinstance(content, bytes) for content in contents.values())
        validate_public_documents(contents)  # type: ignore[arg-type]
        print(f"正式源码导出边界检查通过：将包含 {len(selected)} 个跟踪文件。")
        return 0
    if args.output is None:
        raise SystemExit("必须提供 --check 或 --output。")
    export(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
