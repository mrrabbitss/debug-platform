from pathlib import Path
from contextlib import nullcontext
import re
from typing import Any
from xml.sax.saxutils import escape

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.utils import json_loads, new_id, sha256_file
from app.models import AnalysisRun, Case, Report
from app.services.evidence_display import (
    build_evidence_label_map,
    labels_for_evidence_ids,
    replace_evidence_ids,
)
from app.services.storage import storage
from app.services.category_report import html_report, inline_runs, markdown_blocks, report_markdown


def get_report_context(case_id: str, analysis_id: str, db=None) -> dict[str, Any]:
    """Read the immutable run snapshot; never commit or close a caller's session."""
    from app.services import workbench
    from app.services.report_contract import resolve_report_snapshot
    from app.services.report_contract import structured_template
    from app.services.diagnostic_fault_tree_baseline import is_case_log_evidence

    with (nullcontext(db) if db is not None else SessionLocal()) as db:
        case = db.get(Case, case_id)
        analysis = db.get(AnalysisRun, analysis_id)
        if not case or not analysis or analysis.case_id != case_id:
            raise ValueError("Case or analysis not found")
        result = json_loads(analysis.result_json, {})
        evidence_items = json_loads(analysis.evidence_json, [])
        evidence = {
            item.get("evidence_id"): item
            for item in evidence_items
            if isinstance(item, dict) and item.get("evidence_id")
        }
        configuration = workbench.resolve_configuration(db, json_loads(analysis.model_config_json, {}))
        snapshot = resolve_report_snapshot(configuration) if configuration.get("report_template") else None
    evidence_labels = build_evidence_label_map(evidence.values())
    if snapshot and structured_template(snapshot["report_template"]):
        evidence_labels = {key: re.sub(r" - 第 (\d+)(?:-(\d+))? 行", lambda match:
            f":L{match[1]}" + (f"-L{match[2]}" if match[2] else ""), value) for key, value in evidence_labels.items()}
    return {
        "title": get_settings().report_title,
        "case": case,
        "analysis": analysis,
        "result": result,
        "evidence": evidence,
        "evidence_labels": evidence_labels,
        "configuration": configuration,
        "report_template": snapshot["report_template"] if snapshot else None,
        "case_evidence_ids": {key for key, item in evidence.items() if is_case_log_evidence(item)},
        "display_text": lambda value: replace_evidence_ids(
            str(value or ""), evidence_labels,
        ),
    }


def _render_evidence_labels(
    evidence_ids: list[Any] | None,
    evidence_labels: dict[str, str],
) -> str:
    labels = labels_for_evidence_ids(evidence_ids or [], evidence_labels)
    return "、".join(labels) if labels else "未关联到可定位证据"


def render_html(case_id: str, analysis_id: str) -> str:
    context = get_report_context(case_id, analysis_id)
    return html_report(context)


def _reserve_report(case_id: str, analysis_id: str, fmt: str) -> Report:
    for _attempt in range(10):
        with SessionLocal() as db:
            current = db.scalar(select(func.max(Report.version)).where(
                Report.case_id == case_id,
                Report.analysis_run_id == analysis_id,
                Report.format == fmt,
            ))
            report = Report(
                id=new_id("RPT"),
                case_id=case_id,
                analysis_run_id=analysis_id,
                format=fmt,
                version=int(current or 0) + 1,
                stored_path="",
                sha256="",
            )
            db.add(report)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                continue
            db.refresh(report)
            return report
    raise RuntimeError("Unable to reserve a unique report version")


def _discard_report(report_id: str) -> None:
    with SessionLocal() as db:
        report = db.get(Report, report_id)
        if report:
            db.delete(report)
            db.commit()


def _publish_report(report: Report, temporary: Path, target: Path) -> Report:
    try:
        temporary.replace(target)
        with SessionLocal() as db:
            persisted = db.get(Report, report.id)
            if not persisted:
                raise RuntimeError("Report reservation was lost")
            persisted.stored_path = storage.storage_key(target)
            persisted.sha256 = sha256_file(target)
            db.commit()
            db.refresh(persisted)
            return persisted
    except Exception:
        temporary.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        _discard_report(report.id)
        raise


def generate_html_file(case_id: str, analysis_id: str) -> Report:
    rendered = render_html(case_id, analysis_id)
    report = _reserve_report(case_id, analysis_id, "html")
    path = storage.report_dir(case_id) / f"{analysis_id}_v{report.version}.html"
    temporary = path.with_name(f".{path.name}.{report.id}.tmp")
    try:
        temporary.write_text(rendered, encoding="utf-8")
        return _publish_report(report, temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        _discard_report(report.id)
        raise


def generate_markdown(case_id: str, analysis_id: str) -> Report:
    rendered = report_markdown(get_report_context(case_id, analysis_id))
    return _export(case_id, analysis_id, "md", lambda path: path.write_text(rendered, encoding="utf-8"))


def _export(case_id, analysis_id, fmt, writer):
    report = _reserve_report(case_id, analysis_id, fmt)
    path = storage.report_dir(case_id) / f"{analysis_id}_v{report.version}.{fmt}"
    temporary = path.with_name(f".{path.name}.{report.id}.tmp")
    try:
        writer(temporary)
        return _publish_report(report, temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        _discard_report(report.id)
        raise


def _word_runs(paragraph, text, size=None):
    for value, bold, italic, code in inline_runs(text):
        run = paragraph.add_run(value)
        run.bold, run.italic = bold, italic
        if size:
            run.font.size = Pt(size)
        if code:
            run.font.name = "Consolas"
        if code:
            run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")


def _word_table(document, block):
    table = document.add_table(rows=1, cols=len(block["header"]), style="Table Grid")
    table.autofit = False
    width = Mm(267) / len(block["header"])
    for column in table.columns:
        column.width = int(width)
    for values in [block["header"], *block["rows"]]:
        cells = table.rows[0].cells if values is block["header"] else table.add_row().cells
        for cell, value in zip(cells, values):
            cell.width = int(width)
            paragraph = cell.paragraphs[0]
            paragraph.paragraph_format.space_after = Pt(3)
            _word_runs(paragraph, value, 9)
            if values is block["header"]:
                for run in paragraph.runs:
                    run.bold = True
    header = OxmlElement("w:tblHeader")
    table.rows[0]._tr.get_or_add_trPr().append(header)
    document.add_paragraph().paragraph_format.space_after = Pt(0)


def _word_document(blocks):
    document = Document()
    section = document.sections[0]
    section.orientation = WD_ORIENT.LANDSCAPE
    section.page_width, section.page_height = Mm(297), Mm(210)
    section.left_margin = section.right_margin = Mm(15)
    section.top_margin = section.bottom_margin = Mm(15)
    for name in ("Normal", "Title", "Heading 1", "Heading 2", "Heading 3", "Heading 4", "Heading 5", "Heading 6", "List Bullet", "List Number", "Quote"):
        style = document.styles[name]
        style.font.name = "Microsoft YaHei"
        style._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
        style.font.size = Pt(10.5 if name not in {"Title", "Heading 1", "Heading 2"} else 20 if name == "Title" else 14)
    for block in blocks:
        kind = block["kind"]
        if kind == "table":
            _word_table(document, block)
        elif kind == "list":
            for index, item in enumerate(block["items"], block["start"]):
                # Explicit numbers preserve Markdown list starts/restarts across
                # Word installations with different numbering style definitions.
                paragraph = document.add_paragraph(style="Normal" if block["ordered"] else "List Bullet")
                if block["ordered"]:
                    paragraph.paragraph_format.left_indent = Mm(5)
                    item = f"{index}. " + item
                _word_runs(paragraph, item)
        elif kind == "rule":
            document.add_paragraph()
        elif kind == "code":
            for line in block["text"].splitlines():
                paragraph = document.add_paragraph()
                run = paragraph.add_run(line)
                run.font.name = "Consolas"
                run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
                run.font.size = Pt(9)
        else:
            style = "Title" if kind == "heading" and block["level"] == 1 else f"Heading {block['level']-1}" if kind == "heading" else "Quote" if kind == "quote" else "Normal"
            paragraph = document.add_paragraph(style=style)
            _word_runs(paragraph, block["text"])
    return document


def generate_docx(case_id: str, analysis_id: str) -> Report:
    blocks = markdown_blocks(report_markdown(get_report_context(case_id, analysis_id)))
    document = _word_document(blocks)
    return _export(case_id, analysis_id, "docx", document.save)


def _pdf_font():
    name = "WorkbenchCJK"
    if name in pdfmetrics.getRegisteredFontNames():
        return name
    # Embed the installed Windows CJK font when available, so exported Chinese
    # does not depend on the receiving PDF reader's font substitution policy.
    candidates = [Path("C:/Windows/Fonts/msyh.ttc"), Path("C:/Windows/Fonts/simsun.ttc")]
    for path in candidates:
        if path.is_file():
            pdfmetrics.registerFont(TTFont(name, str(path), subfontIndex=0))
            bold_path = path.with_name("msyhbd.ttc")
            bold_name = name
            if bold_path.is_file():
                bold_name = name + "-Bold"
                pdfmetrics.registerFont(TTFont(bold_name, str(bold_path), subfontIndex=0))
            pdfmetrics.registerFontFamily(name, normal=name, bold=bold_name, italic=name, boldItalic=bold_name)
            return name
    name = "STSong-Light"
    pdfmetrics.registerFont(UnicodeCIDFont(name))
    pdfmetrics.registerFontFamily(name, normal=name, bold=name, italic=name, boldItalic=name)
    return name


def _pdf_symbol_text(value):
    value = escape(value)
    if not re.search("[✅❌✓✔✗✘]", value):
        return value
    name = "WorkbenchSymbols"
    path = Path("C:/Windows/Fonts/seguisym.ttf")
    if path.is_file():
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(path)))
            pdfmetrics.registerFontFamily(name, normal=name, bold=name, italic=name, boldItalic=name)
        return re.sub("[✅❌✓✔✗✘]\ufe0f?", lambda match: f'<font name="{name}">{match[0][0]}</font>', value)
    # CID fallback environments may have no symbol font; preserve the explicit
    # status meaning rather than producing missing-glyph boxes.
    return re.sub("[✅✓✔]", "[正常]", re.sub("[❌✗✘]", "[异常]", value))


def _pdf_inline(text):
    chunks = []
    for value, bold, italic, _code in inline_runs(text):
        value = _pdf_symbol_text(value)
        if bold:
            value = "<b>" + value + "</b>"
        if italic:
            value = "<i>" + value + "</i>"
        chunks.append(value)
    return "".join(chunks)


def _pdf_story(blocks):
    font = _pdf_font()
    styles = getSampleStyleSheet()
    body = ParagraphStyle("ReportBody", parent=styles["BodyText"], fontName=font, fontSize=10.5, leading=16, wordWrap="CJK", splitLongWords=True, spaceAfter=6)
    cell = ParagraphStyle("ReportCell", parent=body, fontSize=9, leading=13, spaceAfter=0)
    title = ParagraphStyle("ReportTitle", parent=body, fontSize=20, leading=28, alignment=TA_CENTER, spaceAfter=16, keepWithNext=True)
    heading = ParagraphStyle("ReportHeading", parent=body, fontSize=14, leading=20, spaceBefore=10, keepWithNext=True)
    quote = ParagraphStyle("ReportQuote", parent=body, leftIndent=10, textColor=colors.HexColor("#52647a"))
    story = []
    for block in blocks:
        kind = block["kind"]
        if kind == "table":
            rows = [[Paragraph(_pdf_inline(value), cell) for value in row] for row in [block["header"], *block["rows"]]]
            table = Table(rows, colWidths=[267 * mm / len(block["header"])] * len(block["header"]), repeatRows=1, splitByRow=1, splitInRow=1)
            table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d8e0e9")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#edf2f7")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
            story.extend([table, Spacer(1, 3 * mm)])
        elif kind == "list":
            for index, item in enumerate(block["items"], block["start"]):
                story.append(Paragraph(_pdf_inline(item), body, bulletText=f"{index}." if block["ordered"] else "•"))
        elif kind == "rule":
            story.append(Spacer(1, 5 * mm))
        elif kind == "code":
            story.extend(Paragraph(_pdf_symbol_text(line) or " ", cell) for line in block["text"].splitlines())
        else:
            style = title if kind == "heading" and block["level"] == 1 else heading if kind == "heading" else quote if kind == "quote" else body
            story.append(Paragraph(_pdf_inline(block["text"]), style))
    return story


def generate_pdf(case_id: str, analysis_id: str) -> Report:
    context = get_report_context(case_id, analysis_id)
    blocks = markdown_blocks(report_markdown(context))
    story = _pdf_story(blocks)
    def write(path):
        document = SimpleDocTemplate(str(path), pagesize=landscape(A4),
            rightMargin=15 * mm, leftMargin=15 * mm, topMargin=15 * mm, bottomMargin=15 * mm,
            title=context["title"], author="GW/AP Debug Platform")
        document.build(story)
    return _export(case_id, analysis_id, "pdf", write)
