from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import mistune


SUPPORTED_BLOCKS = {
    "blank_line",
    "block_code",
    "block_quote",
    "heading",
    "list",
    "list_item",
    "block_text",
    "paragraph",
    "table",
    "table_head",
    "table_body",
    "table_row",
    "table_cell",
    "thematic_break",
}
SUPPORTED_INLINES = {
    "codespan",
    "emphasis",
    "linebreak",
    "link",
    "softbreak",
    "strong",
    "text",
}
RISK_PATTERNS = (
    re.compile(r"98\s*%.*(?:准确率|正确率)|(?:准确率|正确率).*98\s*%", re.IGNORECASE),
    re.compile(r"7\s*[×x*]\s*24\s*(?:小时)?", re.IGNORECASE),
    re.compile(r"千万级.*(?:0[.]5\s*秒|30\s*秒)|(?:0[.]5\s*秒|30\s*秒).*千万级"),
    re.compile(r"(?:符合|通过|满足).{0,24}(?:PSM|HAZOP|SIL|化工过程安全管理导则)", re.IGNORECASE),
)
RISK_QUALIFIERS = (
    "历史",
    "未验证",
    "不宣称",
    "不能宣称",
    "不能用于宣称",
    "不得宣称",
    "不把",
    "拒绝",
    "外部条件",
    "不代表",
    "不构成",
    "尚未",
    "未达到",
    "未承诺",
)
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[opsu]_[A-Za-z0-9]{30,255}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,255}\b"),
    re.compile(r"\bBearer\s+eyJ[A-Za-z0-9_-]+[.]eyJ[A-Za-z0-9_-]+[.][A-Za-z0-9_-]+\b", re.IGNORECASE),
)
CREDENTIAL_ASSIGNMENT = re.compile(
    r"\b(?P<key>[A-Z][A-Z0-9_]*(?:PASSWORD|SECRET|TOKEN|API_KEY)[A-Z0-9_]*)\s*[:=]\s*(?P<value>\S+)",
    re.IGNORECASE,
)
SAFE_CREDENTIAL_KEY_SUFFIXES = ("_FILE", "_PATH", "_DIR")
SAFE_CREDENTIAL_VALUE_PREFIXES = ("$", "%", "<", "[", "{{")


@dataclass(frozen=True)
class SourceDocument:
    source_path: Path
    title: str
    source_sha256: str
    ast: list[dict[str, Any]]
    core_atoms: tuple[str, ...]


class SourceError(ValueError):
    pass


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def normalized_text(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split())


def inline_text(nodes: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for node in nodes:
        kind = node.get("type")
        if kind in {"text", "codespan"}:
            parts.append(str(node.get("raw", "")))
        elif kind in {"softbreak", "linebreak"}:
            parts.append("\n")
        elif kind in {"strong", "emphasis"}:
            parts.append(inline_text(node.get("children", [])))
        elif kind == "link":
            label = inline_text(node.get("children", []))
            target = str(node.get("attrs", {}).get("url", ""))
            parts.append(label)
            if target and normalized_text(target) != normalized_text(label):
                parts.append(f"（{target}）")
        else:
            raise SourceError(f"不支持的 Markdown 行内节点：{kind}")
    return "".join(parts)


def _validate_nodes(nodes: list[dict[str, Any]], *, inline: bool = False) -> None:
    allowed = SUPPORTED_INLINES if inline else SUPPORTED_BLOCKS
    for node in nodes:
        kind = node.get("type")
        if kind not in allowed:
            raise SourceError(f"不支持的 Markdown 节点：{kind}")
        children = node.get("children", [])
        if children:
            child_inline = inline or kind in {"heading", "paragraph", "block_text", "table_cell"}
            _validate_nodes(children, inline=child_inline)


def _collect_atoms(nodes: list[dict[str, Any]]) -> list[str]:
    atoms: list[str] = []
    for node in nodes:
        kind = node["type"]
        if kind in {"heading", "paragraph", "block_text", "table_cell"}:
            text = inline_text(node.get("children", [])).strip()
            if text:
                atoms.append(text)
        elif kind == "block_code":
            text = str(node.get("raw", "")).strip()
            if text:
                atoms.append(text)
        elif node.get("children"):
            atoms.extend(_collect_atoms(node["children"]))
    return atoms


def _validate_sensitive_content(source_path: Path, text: str) -> None:
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            raise SourceError(f"{source_path} 含疑似真实秘密：{pattern.pattern}")
    for line_number, line in enumerate(text.splitlines(), start=1):
        table_cells = [cell.strip() for cell in line.strip().strip("|").split("|")] if line.lstrip().startswith("|") else []
        segments = table_cells if table_cells else re.split(r"[，,。！？!?；;]", line)
        for index, segment in enumerate(segments):
            if any(pattern.search(segment) for pattern in RISK_PATTERNS) and not any(
                qualifier in segment for qualifier in RISK_QUALIFIERS
            ):
                is_scoped_capability_name = bool(
                    table_cells
                    and index == 1
                    and any(
                        cell in {"明确拒绝", "外部条件后重启", "外部输入后才可重启"}
                        for cell in table_cells
                    )
                )
                if is_scoped_capability_name:
                    continue
                raise SourceError(
                    f"{source_path}:{line_number} 将高风险历史指标写成当前声明；"
                    "限定词必须与指标位于同一短句或表格单元格"
                )
        for match in CREDENTIAL_ASSIGNMENT.finditer(line):
            key = match.group("key").upper()
            value = match.group("value").strip('"\'')
            if key.endswith(SAFE_CREDENTIAL_KEY_SUFFIXES) or value.startswith(SAFE_CREDENTIAL_VALUE_PREFIXES):
                continue
            raise SourceError(f"{source_path}:{line_number} 含疑似字面凭据赋值：{key}")


def _validate_gap_coverage(source_path: Path, text: str) -> None:
    if source_path.as_posix() != "docs/deliverables/source-gap-analysis.md":
        return
    found = re.findall(r"\bSC-(\d{3})\b", text)
    expected = {f"{number:03d}" for number in range(1, 58)}
    counts = {value: found.count(value) for value in set(found)}
    missing = sorted(expected - set(found))
    unexpected = sorted(set(found) - expected)
    repeated = sorted(value for value, count in counts.items() if count != 1)
    if missing or unexpected or repeated or len(found) != 57:
        raise SourceError(
            "source-gap-analysis.md 必须让 SC-001..SC-057 各出现且仅出现一次："
            f"缺失={missing}，越界={unexpected}，重复={repeated}，总数={len(found)}"
        )


def load_source(repo_root: Path, source: str, expected_title: str) -> SourceDocument:
    source_path = Path(source)
    if source_path.is_absolute() or ".." in source_path.parts:
        raise SourceError(f"来源路径必须是仓库内相对路径：{source}")
    if source_path.parts[:2] not in {("docs", "guides"), ("docs", "deliverables")}:
        raise SourceError(f"来源路径不在允许目录：{source}")
    absolute = repo_root / source_path
    try:
        raw = absolute.read_bytes()
    except FileNotFoundError as error:
        raise SourceError(f"缺少 Markdown 事实源：{source}") from error
    if raw.startswith(b"\xef\xbb\xbf"):
        raise SourceError(f"Markdown 必须是无 BOM 的 UTF-8：{source}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SourceError(f"Markdown 不是有效 UTF-8：{source}") from error
    if "\r" in text:
        raise SourceError(f"Markdown 必须使用 LF 换行：{source}")
    if not text.endswith("\n"):
        raise SourceError(f"Markdown 末尾必须有换行：{source}")

    parser = mistune.create_markdown(renderer="ast", plugins=["table"])
    ast = parser(text)
    _validate_nodes(ast)
    significant = [node for node in ast if node["type"] != "blank_line"]
    if not significant or significant[0]["type"] != "heading":
        raise SourceError(f"Markdown 首个内容必须是一级标题：{source}")
    first = significant[0]
    if first.get("attrs", {}).get("level") != 1 or inline_text(first.get("children", [])) != expected_title:
        raise SourceError(f"Markdown 一级标题必须精确为“{expected_title}”：{source}")
    h1_count = sum(
        1
        for node in ast
        if node["type"] == "heading" and node.get("attrs", {}).get("level") == 1
    )
    if h1_count != 1:
        raise SourceError(f"Markdown 必须恰有一个一级标题：{source}")

    _validate_sensitive_content(source_path, text)
    _validate_gap_coverage(source_path, text)
    atoms = tuple(_collect_atoms(ast))
    if len(atoms) < 3:
        raise SourceError(f"Markdown 正文内容不足：{source}")
    return SourceDocument(source_path, expected_title, sha256_bytes(raw), ast, atoms)
