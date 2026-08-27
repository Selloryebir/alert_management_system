from __future__ import annotations

import io
import re
import zipfile
from pathlib import PurePosixPath
from typing import Any
from xml.etree import ElementTree

from docx import Document
from docx.table import Table as DocxTable
from pypdf import PdfReader

from markdown_model import SourceDocument, normalized_text


A4_WIDTH = 595.2756
A4_HEIGHT = 841.8898
RELATIONSHIP_NAMESPACE = "http://schemas.openxmlformats.org/package/2006/relationships"
FORBIDDEN_DOCX_PARTS = ("vbaproject.bin", "activex/", "embeddings/")
FORBIDDEN_PDF_ACTIONS = {"/JavaScript", "/Launch", "/GoToR", "/SubmitForm", "/ImportData"}


class ArtifactError(ValueError):
    pass


def _deref(value: Any) -> Any:
    return value.get_object() if hasattr(value, "get_object") else value


def _assert_core_text(source: SourceDocument, extracted: str, kind: str) -> None:
    normalized_output = normalized_text(extracted)
    missing: list[str] = []
    for atom in source.core_atoms:
        normalized_atom = normalized_text(atom)
        if normalized_atom and normalized_atom not in normalized_output:
            missing.append(atom[:80])
            if len(missing) == 5:
                break
    if missing:
        raise ArtifactError(f"{source.source_path} 的 {kind} 缺少核心正文：{missing}")


def verify_docx(data: bytes, source: SourceDocument) -> str:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data), "r")
    except zipfile.BadZipFile as error:
        raise ArtifactError(f"{source.source_path} 的 DOCX 不是有效 ZIP") from error
    with archive:
        names = archive.namelist()
        required = {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}
        missing = sorted(required - set(names))
        if missing:
            raise ArtifactError(f"{source.source_path} 的 DOCX 缺少 OOXML 部件：{missing}")
        if archive.testzip() is not None:
            raise ArtifactError(f"{source.source_path} 的 DOCX ZIP 校验失败")
        for name in names:
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts:
                raise ArtifactError(f"{source.source_path} 的 DOCX 含越界部件：{name}")
            lowered = name.lower()
            if any(part in lowered for part in FORBIDDEN_DOCX_PARTS):
                raise ArtifactError(f"{source.source_path} 的 DOCX 含主动或嵌入内容：{name}")
            if name.endswith((".xml", ".rels")):
                try:
                    root = ElementTree.fromstring(archive.read(name))
                except ElementTree.ParseError as error:
                    raise ArtifactError(f"{source.source_path} 的 DOCX XML 无法解析：{name}") from error
                if name.endswith(".rels"):
                    for relation in root.findall(f"{{{RELATIONSHIP_NAMESPACE}}}Relationship"):
                        if relation.attrib.get("TargetMode") == "External":
                            raise ArtifactError(
                                f"{source.source_path} 的 DOCX 含外部关系：{relation.attrib.get('Target', '')}"
                            )
        content_types = archive.read("[Content_Types].xml").lower()
        if b"macroenabled" in content_types or b"vba" in content_types:
            raise ArtifactError(f"{source.source_path} 的 DOCX 含宏内容类型")
        footer_xml = b"\n".join(archive.read(name) for name in names if name.startswith("word/footer") and name.endswith(".xml"))
        if b"PAGE" not in footer_xml or b"NUMPAGES" not in footer_xml:
            raise ArtifactError(f"{source.source_path} 的 DOCX 页脚缺少当前页或总页数字段")

    try:
        document = Document(io.BytesIO(data))
    except Exception as error:
        raise ArtifactError(f"{source.source_path} 的 DOCX 无法由 python-docx 重新打开") from error
    parts: list[str] = []
    for block in document.iter_inner_content():
        if isinstance(block, DocxTable):
            for row in block.rows:
                parts.extend(cell.text for cell in row.cells)
        else:
            parts.append(block.text)
    extracted = "\n".join(parts)
    if source.title not in extracted:
        raise ArtifactError(f"{source.source_path} 的 DOCX 缺少正式标题")
    _assert_core_text(source, extracted, "DOCX")
    return extracted


def _font_is_embedded(font_reference: Any) -> bool:
    font = _deref(font_reference)
    descriptors: list[Any] = []
    descriptor = font.get("/FontDescriptor") if hasattr(font, "get") else None
    if descriptor is not None:
        descriptors.append(_deref(descriptor))
    descendants = font.get("/DescendantFonts", []) if hasattr(font, "get") else []
    for descendant_reference in descendants:
        descendant = _deref(descendant_reference)
        descriptor = descendant.get("/FontDescriptor") if hasattr(descendant, "get") else None
        if descriptor is not None:
            descriptors.append(_deref(descriptor))
    return any(
        any(descriptor.get(key) is not None for key in ("/FontFile", "/FontFile2", "/FontFile3"))
        for descriptor in descriptors
    )


def _validate_pdf_actions(reader: PdfReader, source: SourceDocument) -> None:
    root = _deref(reader.trailer["/Root"])
    if root.get("/OpenAction") is not None or root.get("/AA") is not None or root.get("/AcroForm") is not None:
        raise ArtifactError(f"{source.source_path} 的 PDF 含自动动作或表单")
    names = _deref(root.get("/Names", {}))
    if names and (names.get("/JavaScript") is not None or names.get("/EmbeddedFiles") is not None):
        raise ArtifactError(f"{source.source_path} 的 PDF 含脚本或附件")
    for page in reader.pages:
        page_object = _deref(page)
        if page_object.get("/AA") is not None:
            raise ArtifactError(f"{source.source_path} 的 PDF 页面含自动动作")
        for annotation_reference in page_object.get("/Annots", []):
            annotation = _deref(annotation_reference)
            action = _deref(annotation.get("/A", {}))
            if action and str(action.get("/S")) in FORBIDDEN_PDF_ACTIONS:
                raise ArtifactError(f"{source.source_path} 的 PDF 含主动动作：{action.get('/S')}")


def verify_pdf(data: bytes, source: SourceDocument) -> str:
    if not data.startswith(b"%PDF-") or b"%%EOF" not in data[-1024:]:
        raise ArtifactError(f"{source.source_path} 的 PDF 头尾结构无效")
    try:
        reader = PdfReader(io.BytesIO(data), strict=True)
    except Exception as error:
        raise ArtifactError(f"{source.source_path} 的 PDF 无法由 pypdf 打开") from error
    if reader.is_encrypted:
        raise ArtifactError(f"{source.source_path} 的 PDF 不得加密")
    if not reader.pages:
        raise ArtifactError(f"{source.source_path} 的 PDF 没有页面")
    metadata = reader.metadata
    if not metadata or metadata.title != source.title:
        raise ArtifactError(f"{source.source_path} 的 PDF 元数据标题不正确")
    if metadata.author != "报警管理系统项目组" or metadata.subject != "报警管理系统正式项目交付物":
        raise ArtifactError(f"{source.source_path} 的 PDF 元数据项目身份不正确")
    if not metadata.creation_date or not metadata.modification_date:
        raise ArtifactError(f"{source.source_path} 的 PDF 元数据缺少创建或修改时间")
    _validate_pdf_actions(reader, source)

    embedded_font = False
    extracted_pages: list[str] = []
    for index, page in enumerate(reader.pages, start=1):
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)
        if abs(width - A4_WIDTH) > 2 or abs(height - A4_HEIGHT) > 2:
            raise ArtifactError(f"{source.source_path} 的 PDF 第 {index} 页不是 A4")
        resources = _deref(page.get("/Resources", {}))
        fonts = _deref(resources.get("/Font", {})) if resources else {}
        embedded_font = embedded_font or any(_font_is_embedded(value) for value in fonts.values())
        try:
            extracted_pages.append(page.extract_text() or "")
        except Exception as error:
            raise ArtifactError(f"{source.source_path} 的 PDF 第 {index} 页正文无法提取") from error
    if not embedded_font:
        raise ArtifactError(f"{source.source_path} 的 PDF 没有嵌入字体")
    extracted = "\n".join(extracted_pages)
    if source.title not in extracted:
        raise ArtifactError(f"{source.source_path} 的 PDF 缺少正式标题")
    _assert_core_text(source, extracted, "PDF")
    total = len(reader.pages)
    for index, page_text in enumerate(extracted_pages, start=1):
        if not re.search(rf"第\s*{index}\s*/\s*{total}\s*页", page_text):
            raise ArtifactError(f"{source.source_path} 的 PDF 第 {index} 页页码或总页数不正确")
    return extracted
