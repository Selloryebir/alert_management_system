#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


TOOL_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOL_DIR.parents[1]
CONFIG_PATH = TOOL_DIR / "manifest.json"
OUTPUT_DIR = REPO_ROOT / "deliverables"
OUTPUT_MANIFEST = OUTPUT_DIR / "manifest.json"
EXPECTED_SOURCES = (
    "docs/guides/business-user-manual.md",
    "docs/guides/windows-deployment-operations.md",
    "docs/deliverables/project-proposal.md",
    "docs/deliverables/midterm-report.md",
    "docs/deliverables/test-report.md",
    "docs/deliverables/closure-report.md",
    "docs/deliverables/development-process.md",
    "docs/deliverables/source-gap-analysis.md",
)
EXPECTED_FONT = "src/backend/src/main/resources/fonts/NotoSansSC-VF.ttf"
EXPECTED_LICENSE = "src/backend/src/main/resources/fonts/OFL.txt"
OUTPUT_NAME_PATTERN = re.compile(r"[a-z][a-z0-9-]{2,80}\Z")


class BuildError(RuntimeError):
    pass


@dataclass(frozen=True)
class DocumentConfig:
    source: str
    output: str
    title: str


@dataclass(frozen=True)
class Config:
    document_date: str
    font_path: Path
    font_sha256: str
    license_path: Path
    license_sha256: str
    documents: tuple[DocumentConfig, ...]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise BuildError(f"缺少配置文件：{path.relative_to(REPO_ROOT)}") from error
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BuildError(f"配置文件不是有效 UTF-8 JSON：{path.relative_to(REPO_ROOT)}") from error


def _load_config() -> Config:
    raw = _load_json(CONFIG_PATH)
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise BuildError("tools/deliverables/manifest.json 的 schema_version 必须为 1")
    try:
        date.fromisoformat(raw["document_date"])
        font = raw["font"]
        documents = tuple(DocumentConfig(**item) for item in raw["documents"])
    except (KeyError, TypeError, ValueError) as error:
        raise BuildError("tools/deliverables/manifest.json 字段无效") from error
    if tuple(item.source for item in documents) != EXPECTED_SOURCES:
        raise BuildError("构建清单必须按冻结顺序登记八份正式 Markdown")
    if len({item.output for item in documents}) != len(documents):
        raise BuildError("构建清单的输出名称重复")
    for item in documents:
        if not OUTPUT_NAME_PATTERN.fullmatch(item.output):
            raise BuildError(f"输出名称不安全：{item.output}")
        if not item.title.strip() or "\n" in item.title:
            raise BuildError(f"文档标题无效：{item.source}")
    if font.get("path") != EXPECTED_FONT or font.get("license_path") != EXPECTED_LICENSE:
        raise BuildError("必须复用仓库现有 NotoSansSC 字体及 OFL，不得切换或复制字体")
    for field in ("sha256", "license_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(font.get(field, ""))):
            raise BuildError(f"字体配置 {field} 不是 SHA-256")
    return Config(
        document_date=raw["document_date"],
        font_path=REPO_ROOT / font["path"],
        font_sha256=font["sha256"],
        license_path=REPO_ROOT / font["license_path"],
        license_sha256=font["license_sha256"],
        documents=documents,
    )


def _check_fact_file(path: Path, expected_hash: str, label: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise BuildError(f"缺少或拒绝符号链接形式的{label}：{path.relative_to(REPO_ROOT)}")
    actual = _sha256(path.read_bytes())
    if actual != expected_hash:
        raise BuildError(f"{label}哈希不一致：期望 {expected_hash}，实际 {actual}")


def _load_modules() -> tuple[Any, ...]:
    try:
        from docx_renderer import render_docx
        from markdown_model import SourceError, load_source
        from pdf_renderer import render_pdf
        from verify import ArtifactError, verify_docx, verify_pdf
    except ModuleNotFoundError as error:
        missing = error.name or "未知依赖"
        raise BuildError(
            f"缺少 Python 依赖 {missing}。请执行：\n"
            "  python3 -m venv .runtime/deliverables-venv\n"
            "  . .runtime/deliverables-venv/bin/activate\n"
            "  python3 -m pip install --require-hashes --only-binary=:all: -r tools/deliverables/requirements.lock\n"
            "如果 WSL 无法创建虚拟环境，请先安装 python3-venv 和 python3-pip。"
        ) from error
    return load_source, render_docx, render_pdf, verify_docx, verify_pdf, SourceError, ArtifactError


def _render(config: Config, directory: Path) -> tuple[dict[str, bytes], list[dict[str, Any]]]:
    load_source, render_docx, render_pdf, verify_docx, verify_pdf, SourceError, ArtifactError = _load_modules()
    artifacts: dict[str, bytes] = {}
    records: list[dict[str, Any]] = []
    for item in config.documents:
        try:
            source = load_source(REPO_ROOT, item.source, item.title)
            docx_data = render_docx(source, config.document_date)
            pdf_data = render_pdf(source, config.document_date, config.font_path)
            verify_docx(docx_data, source)
            verify_pdf(pdf_data, source)
        except (SourceError, ArtifactError, ValueError) as error:
            raise BuildError(str(error)) from error
        docx_name = f"{item.output}.docx"
        pdf_name = f"{item.output}.pdf"
        artifacts[docx_name] = docx_data
        artifacts[pdf_name] = pdf_data
        (directory / docx_name).write_bytes(docx_data)
        (directory / pdf_name).write_bytes(pdf_data)
        records.append(
            {
                "source": item.source,
                "source_sha256": source.source_sha256,
                "title": item.title,
                "outputs": {
                    "docx": {"path": f"deliverables/{docx_name}", "sha256": _sha256(docx_data), "size": len(docx_data)},
                    "pdf": {"path": f"deliverables/{pdf_name}", "sha256": _sha256(pdf_data), "size": len(pdf_data)},
                },
            }
        )
    return artifacts, records


def _output_manifest(config: Config, records: list[dict[str, Any]]) -> bytes:
    value = {
        "schema_version": 1,
        "generator": "tools/deliverables/build.py",
        "document_date": config.document_date,
        "font": {
            "path": EXPECTED_FONT,
            "sha256": config.font_sha256,
            "license_path": EXPECTED_LICENSE,
            "license_sha256": config.license_sha256,
        },
        "documents": records,
    }
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _expected_output_names(config: Config) -> set[str]:
    names = {"manifest.json"}
    for item in config.documents:
        names.add(f"{item.output}.docx")
        names.add(f"{item.output}.pdf")
    return names


def _existing_output_files() -> set[str]:
    if not OUTPUT_DIR.exists():
        return set()
    if OUTPUT_DIR.is_symlink() or not OUTPUT_DIR.is_dir():
        raise BuildError("deliverables 必须是普通目录，不能是符号链接")
    result: set[str] = set()
    for path in OUTPUT_DIR.rglob("*"):
        if path.is_symlink():
            raise BuildError(f"交付目录内不得有符号链接：{path.relative_to(REPO_ROOT)}")
        if path.is_file():
            result.add(path.relative_to(OUTPUT_DIR).as_posix())
    return result


def _atomic_write(path: Path, data: bytes) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _check_mode(config: Config) -> None:
    _load_modules()
    expected_names = _expected_output_names(config)
    existing_names = _existing_output_files()
    missing = sorted(expected_names - existing_names)
    extra = sorted(existing_names - expected_names)
    if missing or extra:
        raise BuildError(f"交付目录文件集合不一致：缺失={missing}，多余={extra}")
    with tempfile.TemporaryDirectory(prefix="alert-deliverables-check-a-") as first_dir, tempfile.TemporaryDirectory(
        prefix="alert-deliverables-check-b-"
    ) as second_dir:
        first, first_records = _render(config, Path(first_dir))
        second, second_records = _render(config, Path(second_dir))
    if first.keys() != second.keys():
        raise BuildError("两次生成的文件集合不一致")
    unstable = sorted(name for name in first if first[name] != second[name])
    if unstable or first_records != second_records:
        raise BuildError(f"相同输入的生成物不确定：{unstable}")
    expected_manifest = _output_manifest(config, first_records)
    if OUTPUT_MANIFEST.read_bytes() != expected_manifest:
        raise BuildError("deliverables/manifest.json 已陈旧；请重新运行普通构建")
    stale = sorted(name for name, data in first.items() if (OUTPUT_DIR / name).read_bytes() != data)
    if stale:
        raise BuildError(f"交付物与 Markdown 事实源不一致：{stale}")


def _build_mode(config: Config) -> None:
    _load_modules()
    existing = _existing_output_files()
    extra = sorted(existing - _expected_output_names(config))
    if extra:
        raise BuildError(f"交付目录存在未登记文件，拒绝自动删除：{extra}")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="alert-deliverables-build-") as temporary_dir:
        artifacts, records = _render(config, Path(temporary_dir))
    manifest = _output_manifest(config, records)
    for name in sorted(artifacts):
        _atomic_write(OUTPUT_DIR / name, artifacts[name])
    _atomic_write(OUTPUT_MANIFEST, manifest)


def main() -> int:
    parser = argparse.ArgumentParser(description="从 Markdown 事实源生成并验证报警管理系统正式 DOCX/PDF")
    parser.add_argument("--check", action="store_true", help="只读验证生成物，不更新仓库")
    args = parser.parse_args()
    try:
        config = _load_config()
        _check_fact_file(config.font_path, config.font_sha256, "中文字体")
        _check_fact_file(config.license_path, config.license_sha256, "字体许可证")
        if args.check:
            _check_mode(config)
            print("PASS: 八份 Markdown 与 DOCX/PDF 交付物一致、结构安全且生成确定")
        else:
            _build_mode(config)
            print("PASS: 已从八份 Markdown 生成 16 个 DOCX/PDF 交付物")
        return 0
    except (BuildError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
