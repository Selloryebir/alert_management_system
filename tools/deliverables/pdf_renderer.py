from __future__ import annotations

import html
import io
from pathlib import Path
from typing import Any, Iterable

from reportlab import rl_config

rl_config.invariant = 1

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import HRFlowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from markdown_model import SourceDocument, inline_text


FONT_NAME = "NotoSansSC"


def _register_font(font_path: Path) -> None:
    if FONT_NAME not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(FONT_NAME, str(font_path)))
        pdfmetrics.registerFontFamily(
            FONT_NAME,
            normal=FONT_NAME,
            bold=FONT_NAME,
            italic=FONT_NAME,
            boldItalic=FONT_NAME,
        )


def _inline_markup(nodes: list[dict[str, Any]], bold: bool = False, italic: bool = False) -> str:
    parts: list[str] = []
    for node in nodes:
        kind = node["type"]
        if kind == "text":
            value = html.escape(str(node.get("raw", "")))
        elif kind == "codespan":
            value = f'<font color="#404040">{html.escape(str(node.get("raw", "")))}</font>'
        elif kind in {"softbreak", "linebreak"}:
            value = "<br/>"
        elif kind == "strong":
            value = _inline_markup(node.get("children", []), True, italic)
        elif kind == "emphasis":
            value = _inline_markup(node.get("children", []), bold, True)
        elif kind == "link":
            label = inline_text(node.get("children", []))
            target = str(node.get("attrs", {}).get("url", ""))
            visible = label if not target or target == label else f"{label}（{target}）"
            value = f'<font color="#1F4E78">{html.escape(visible)}</font>'
        else:
            raise ValueError(f"不支持的 PDF 行内节点：{kind}")
        if bold:
            value = f"<b>{value}</b>"
        if italic:
            value = f"<i>{value}</i>"
        parts.append(value)
    return "".join(parts)


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    normal = ParagraphStyle(
        "ChineseNormal",
        parent=base["Normal"],
        fontName=FONT_NAME,
        fontSize=10.5,
        leading=16,
        spaceAfter=6,
        textColor=colors.HexColor("#202020"),
        wordWrap="CJK",
    )
    return {
        "normal": normal,
        "title": ParagraphStyle(
            "ChineseTitle",
            parent=normal,
            fontSize=22,
            leading=30,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#17365D"),
            spaceBefore=8,
            spaceAfter=18,
        ),
        "h2": ParagraphStyle(
            "ChineseH2",
            parent=normal,
            fontSize=16,
            leading=23,
            textColor=colors.HexColor("#17365D"),
            spaceBefore=12,
            spaceAfter=7,
        ),
        "h3": ParagraphStyle(
            "ChineseH3",
            parent=normal,
            fontSize=13,
            leading=20,
            textColor=colors.HexColor("#1F4E78"),
            spaceBefore=9,
            spaceAfter=5,
        ),
        "h4": ParagraphStyle(
            "ChineseH4",
            parent=normal,
            fontSize=11,
            leading=17,
            textColor=colors.HexColor("#2F5597"),
            spaceBefore=7,
            spaceAfter=4,
        ),
        "quote": ParagraphStyle(
            "ChineseQuote",
            parent=normal,
            leftIndent=8 * mm,
            rightIndent=5 * mm,
            textColor=colors.HexColor("#555555"),
            borderColor=colors.HexColor("#B4C6E7"),
            borderWidth=1,
            borderPadding=5,
        ),
        "code": ParagraphStyle(
            "ChineseCode",
            parent=normal,
            fontSize=9,
            leading=13,
            leftIndent=5 * mm,
            rightIndent=3 * mm,
            backColor=colors.HexColor("#F2F2F2"),
            borderPadding=5,
        ),
        "list": ParagraphStyle(
            "ChineseList",
            parent=normal,
            leftIndent=8 * mm,
            firstLineIndent=-4 * mm,
        ),
        "cell": ParagraphStyle(
            "ChineseCell",
            parent=normal,
            fontSize=8.2,
            leading=11,
            spaceAfter=0,
        ),
    }


def _flatten_list_item(node: dict[str, Any]) -> str:
    parts: list[str] = []
    for child in node.get("children", []):
        if child["type"] in {"block_text", "paragraph"}:
            parts.append(_inline_markup(child.get("children", [])))
        elif child["type"] == "list":
            for nested in child.get("children", []):
                parts.append(_flatten_list_item(nested))
    return "<br/>".join(parts)


def _table_rows(node: dict[str, Any], style: ParagraphStyle) -> list[list[Paragraph]]:
    rows: list[list[Paragraph]] = []
    for child in node.get("children", []):
        if child["type"] == "table_head":
            rows.append([Paragraph(_inline_markup(cell.get("children", [])), style) for cell in child.get("children", [])])
        elif child["type"] == "table_body":
            for row in child.get("children", []):
                rows.append([Paragraph(_inline_markup(cell.get("children", [])), style) for cell in row.get("children", [])])
    return rows


def _render_blocks(story: list[Any], nodes: Iterable[dict[str, Any]], styles: dict[str, ParagraphStyle], quote: bool = False) -> None:
    for node in nodes:
        kind = node["type"]
        if kind == "blank_line":
            continue
        if kind == "heading":
            level = int(node.get("attrs", {}).get("level", 1))
            style = styles["title"] if level == 1 else styles[f"h{min(level, 4)}"]
            story.append(Paragraph(_inline_markup(node.get("children", [])), style))
            if level == 1:
                story.append(Spacer(1, 3 * mm))
            continue
        if kind in {"paragraph", "block_text"}:
            story.append(KeepTogether([
                Paragraph(_inline_markup(node.get("children", [])), styles["quote"] if quote else styles["normal"]),
            ]))
            continue
        if kind == "block_code":
            value = html.escape(str(node.get("raw", "")).rstrip("\n")).replace("\n", "<br/>")
            story.append(KeepTogether([Paragraph(value, styles["code"])]))
            continue
        if kind == "list":
            ordered = bool(node.get("attrs", {}).get("ordered", False))
            start = int(node.get("attrs", {}).get("start", 1) or 1)
            for index, item in enumerate(node.get("children", []), start=start):
                marker = f"{index}." if ordered else "•"
                story.append(Paragraph(f"{marker}&nbsp;&nbsp;{_flatten_list_item(item)}", styles["list"]))
            continue
        if kind == "block_quote":
            _render_blocks(story, node.get("children", []), styles, quote=True)
            continue
        if kind == "thematic_break":
            story.append(HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#B4C6E7"), spaceBefore=5, spaceAfter=7))
            continue
        if kind == "table":
            rows = _table_rows(node, styles["cell"])
            if not rows:
                continue
            columns = max(len(row) for row in rows)
            available = A4[0] - 42 * mm
            table = Table(rows, colWidths=[available / columns] * columns, repeatRows=1, hAlign="LEFT")
            table.setStyle(
                TableStyle(
                    [
                        ("FONTNAME", (0, 0), (-1, -1), FONT_NAME),
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#D9EAF7")),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#17365D")),
                        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#A6A6A6")),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 4),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                        ("TOPPADDING", (0, 0), (-1, -1), 4),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ]
                )
            )
            story.extend((table, Spacer(1, 3 * mm)))
            continue
        raise ValueError(f"不支持的 PDF 块节点：{kind}")


class _NumberedCanvas(canvas.Canvas):
    def __init__(self, *args: Any, document_date: str, **kwargs: Any) -> None:
        kwargs["invariant"] = 1
        kwargs["pageCompression"] = 0
        super().__init__(*args, **kwargs)
        self._saved_page_states: list[dict[str, Any]] = []
        pdf_date = document_date.replace("-", "")
        self._doc.info._dateFormatter = lambda *_: f"D:{pdf_date}000000+00'00'"

    def showPage(self) -> None:
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        total = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.setFont(FONT_NAME, 8)
            self.setFillColor(colors.HexColor("#666666"))
            self.drawRightString(A4[0] - 21 * mm, 9 * mm, f"第 {self._pageNumber} / {total} 页")
            canvas.Canvas.showPage(self)
        canvas.Canvas.save(self)


def render_pdf(source: SourceDocument, document_date: str, font_path: Path) -> bytes:
    _register_font(font_path)
    output = io.BytesIO()
    styles = _styles()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=21 * mm,
        leftMargin=21 * mm,
        topMargin=20 * mm,
        bottomMargin=18 * mm,
        title=source.title,
        author="报警管理系统项目组",
        subject="报警管理系统正式项目交付物",
    )
    story: list[Any] = []
    _render_blocks(story, source.ast, styles)

    def decorate_page(page_canvas: canvas.Canvas, _: Any) -> None:
        page_canvas.setTitle(source.title)
        page_canvas.setAuthor("报警管理系统项目组")
        page_canvas.setSubject("报警管理系统正式项目交付物")
        page_canvas.setFont(FONT_NAME, 8)
        page_canvas.setFillColor(colors.HexColor("#666666"))
        page_canvas.drawString(21 * mm, 9 * mm, f"报警管理系统 · 文档日期 {document_date}")

    def canvas_factory(*args: Any, **kwargs: Any) -> _NumberedCanvas:
        return _NumberedCanvas(*args, document_date=document_date, **kwargs)

    document.build(story, onFirstPage=decorate_page, onLaterPages=decorate_page, canvasmaker=canvas_factory)
    return output.getvalue()
