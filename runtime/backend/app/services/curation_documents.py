from __future__ import annotations

import hashlib
import os
import re
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PdfReader

from app.core.config import get_settings
from app.services.text_files import open_text_lines


HTML_SUFFIXES = {".html", ".htm", ".xhtml"}
WORD_SUFFIXES = {".docx"}
LEGACY_WORD_SUFFIXES = {".doc"}
PDF_SUFFIXES = {".pdf"}
_HTML_BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "caption",
    "dd",
    "div",
    "dl",
    "dt",
    "figcaption",
    "figure",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "ul",
}
_HTML_IGNORED_TAGS = {"script", "style", "noscript", "template", "svg", "canvas"}


class DocumentExtractionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PreparedCurationDocument:
    path: Path
    method: str
    sha256: str
    line_count: int
    page_count: int | None = None
    truncated: bool = False


def _normalize_visible_text(value: str) -> str:
    return re.sub(r"[\t\v\f\r ]+", " ", value.replace("\x00", "")).strip()


class _VisibleHTMLTextParser(HTMLParser):
    def __init__(self, max_chars: int) -> None:
        super().__init__(convert_charrefs=True)
        self.max_chars = max_chars
        self.lines: list[str] = []
        self._parts: list[str] = []
        self._ignored_stack: list[str] = []
        self._style_depth = 0
        self._style_parts: list[str] = []
        self._hidden_classes: set[str] = set()
        self._hidden_ids: set[str] = set()
        self.char_count = 0
        self.truncated = False

    def _register_hidden_styles(self) -> None:
        stylesheet = re.sub(r"/\*.*?\*/", "", " ".join(self._style_parts), flags=re.S)
        self._style_parts.clear()
        for selector_group, declarations in re.findall(r"([^{}]+)\{([^{}]*)\}", stylesheet):
            normalized = re.sub(r"\s+", "", declarations.casefold())
            if "display:none" not in normalized and "visibility:hidden" not in normalized:
                continue
            for selector in selector_group.split(","):
                cleaned = selector.strip().casefold()
                if re.fullmatch(r"\.[a-z_][\w-]*", cleaned):
                    self._hidden_classes.add(cleaned[1:])
                elif re.fullmatch(r"#[a-z_][\w-]*", cleaned):
                    self._hidden_ids.add(cleaned[1:])

    def _is_hidden(self, tag: str, attrs: list[tuple[str, str | None]]) -> bool:
        attributes = {name.casefold(): (value or "").casefold() for name, value in attrs}
        style = attributes.get("style", "").replace(" ", "")
        class_names = set(attributes.get("class", "").split())
        return bool(
            tag in _HTML_IGNORED_TAGS
            or "hidden" in attributes
            or attributes.get("aria-hidden") == "true"
            or "display:none" in style
            or "visibility:hidden" in style
            or class_names.intersection(self._hidden_classes)
            or attributes.get("id") in self._hidden_ids
        )

    def _flush(self) -> None:
        if not self._parts or self.truncated:
            self._parts.clear()
            return
        line = _normalize_visible_text(" ".join(self._parts))
        self._parts.clear()
        if not line:
            return
        remaining = self.max_chars - self.char_count
        if remaining <= 0:
            self.truncated = True
            return
        if len(line) > remaining:
            line = line[:remaining].rstrip()
            self.truncated = True
        if line:
            self.lines.append(line)
            self.char_count += len(line) + 1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if tag == "style":
            self._style_depth += 1
            return
        if self._style_depth:
            return
        if self._is_hidden(tag, attrs):
            self._ignored_stack.append(tag)
            return
        if self._ignored_stack:
            return
        if tag in _HTML_BLOCK_TAGS or tag == "br":
            self._flush()
        attributes = {name.casefold(): value or "" for name, value in attrs}
        if tag == "img" and attributes.get("alt"):
            self._parts.append(attributes["alt"])

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag == "style" and self._style_depth:
            self._style_depth -= 1
            if self._style_depth == 0:
                self._register_hidden_styles()
            return
        if self._style_depth:
            return
        if self._ignored_stack:
            if tag in self._ignored_stack:
                reverse_index = self._ignored_stack[::-1].index(tag)
                del self._ignored_stack[len(self._ignored_stack) - reverse_index - 1:]
            return
        if tag in _HTML_BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._style_depth:
            self._style_parts.append(data)
            return
        if self._ignored_stack or self.truncated:
            return
        value = _normalize_visible_text(data)
        if value:
            self._parts.append(value)
            if sum(len(part) for part in self._parts) >= 4000:
                self._flush()

    def close(self) -> None:
        super().close()
        self._flush()


def _html_lines(path: Path, max_chars: int) -> tuple[list[str], bool]:
    opened = open_text_lines(path)
    if opened is None:
        raise DocumentExtractionError(
            "html_not_readable_text",
            "HTML file is not readable text",
        )
    _, source_lines = opened
    parser = _VisibleHTMLTextParser(max_chars)
    try:
        for line in source_lines:
            parser.feed(line + "\n")
            if parser.truncated:
                break
        parser.close()
    except Exception as exc:
        raise DocumentExtractionError(
            "html_extraction_failed",
            "HTML visible-text extraction failed",
        ) from exc
    return parser.lines, parser.truncated


def _validate_openxml_package(path: Path) -> None:
    settings = get_settings()
    if not zipfile.is_zipfile(path):
        raise DocumentExtractionError("invalid_docx", "Word file is not a valid DOCX package")
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > settings.max_archive_files:
                raise DocumentExtractionError(
                    "docx_too_many_parts",
                    "Word document contains too many package parts",
                )
            total = 0
            for member in members:
                if member.flag_bits & 0x1:
                    raise DocumentExtractionError(
                        "encrypted_docx",
                        "Encrypted Word documents are not supported",
                    )
                if member.file_size > settings.curation_max_document_uncompressed_bytes:
                    raise DocumentExtractionError(
                        "docx_part_too_large",
                        "Word document contains an oversized package part",
                    )
                total += member.file_size
                if total > settings.curation_max_document_uncompressed_bytes:
                    raise DocumentExtractionError(
                        "docx_uncompressed_too_large",
                        "Word document exceeds the uncompressed extraction limit",
                    )
    except DocumentExtractionError:
        raise
    except (OSError, zipfile.BadZipFile) as exc:
        raise DocumentExtractionError(
            "invalid_docx",
            "Word file is not a valid DOCX package",
        ) from exc


def _table_lines(table: Table) -> Iterable[str]:
    for row in table.rows:
        cells = [_normalize_visible_text(cell.text) for cell in row.cells]
        if any(cells):
            yield " | ".join(cells)


def _docx_lines(path: Path) -> Iterable[str]:
    _validate_openxml_package(path)
    try:
        document = Document(str(path))
        for block in document.iter_inner_content():
            if isinstance(block, Paragraph):
                text = _normalize_visible_text(block.text)
                if text:
                    yield text
            elif isinstance(block, Table):
                yield from _table_lines(block)
    except DocumentExtractionError:
        raise
    except Exception as exc:
        raise DocumentExtractionError(
            "docx_extraction_failed",
            "Word document text extraction failed",
        ) from exc


def _pdf_lines(path: Path) -> tuple[Iterable[str], int, dict[str, bool]]:
    settings = get_settings()
    try:
        reader = PdfReader(str(path), strict=False)
        if reader.is_encrypted:
            try:
                unlocked = reader.decrypt("")
            except Exception as exc:
                raise DocumentExtractionError(
                    "encrypted_pdf",
                    "Encrypted PDF documents are not supported",
                ) from exc
            if unlocked == 0:
                raise DocumentExtractionError(
                    "encrypted_pdf",
                    "Encrypted PDF documents are not supported",
                )
        page_count = len(reader.pages)
    except DocumentExtractionError:
        raise
    except Exception as exc:
        raise DocumentExtractionError(
            "invalid_pdf",
            "PDF file could not be opened",
        ) from exc

    state = {
        "truncated": page_count > settings.curation_pdf_max_pages,
        "has_text": False,
    }

    def lines() -> Iterable[str]:
        total_content_stream_bytes = 0
        pages_to_read = min(page_count, settings.curation_pdf_max_pages)
        for page_index in range(pages_to_read):
            page_number = page_index + 1
            page = reader.pages[page_index]
            try:
                contents = page.get_contents()
                if contents is not None:
                    content_size = len(contents.get_data())
                    total_content_stream_bytes += content_size
                    if (
                        content_size > settings.curation_pdf_max_content_stream_bytes
                        or total_content_stream_bytes
                        > settings.curation_pdf_max_content_stream_bytes
                    ):
                        state["truncated"] = True
                        continue
                text = page.extract_text() or ""
            except Exception:
                state["truncated"] = True
                continue
            text_lines = [
                normalized
                for line in text.replace("\x00", "").splitlines()
                if (normalized := _normalize_visible_text(line))
            ]
            if not text_lines:
                continue
            state["has_text"] = True
            yield f"[PDF page {page_number}]"
            yield from text_lines
        if page_count > pages_to_read and state["has_text"]:
            yield f"[PDF truncated after {pages_to_read} of {page_count} pages]"

    return lines(), page_count, state


def _write_extracted_lines(
    destination: Path,
    lines: Iterable[str],
    *,
    max_chars: int,
) -> tuple[str, int, bool]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    digest = hashlib.sha256()
    line_count = 0
    char_count = 0
    truncated = False
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            for raw_line in lines:
                line = _normalize_visible_text(str(raw_line))
                if not line:
                    continue
                remaining = max_chars - char_count
                if remaining <= 0:
                    truncated = True
                    break
                if len(line) > remaining:
                    line = line[:remaining].rstrip()
                    truncated = True
                if line:
                    encoded = (line + "\n").encode("utf-8")
                    handle.write(line + "\n")
                    digest.update(encoded)
                    char_count += len(line) + 1
                    line_count += 1
                if truncated:
                    break
        if line_count == 0:
            raise DocumentExtractionError(
                "document_has_no_extractable_text",
                "Document contains no extractable text",
            )
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return digest.hexdigest(), line_count, truncated


def prepare_curation_document(
    source_path: Path,
    relative_path: str,
    destination: Path,
) -> PreparedCurationDocument | None:
    settings = get_settings()
    suffix = Path(relative_path).suffix.casefold()
    max_chars = settings.curation_max_extracted_text_chars
    page_count: int | None = None
    extraction_truncated = False

    if suffix in LEGACY_WORD_SUFFIXES:
        raise DocumentExtractionError(
            "legacy_doc_requires_conversion",
            "Legacy .doc files must be converted to .docx or text",
        )
    if suffix in HTML_SUFFIXES:
        lines, extraction_truncated = _html_lines(source_path, max_chars)
        method = "html_visible_text"
    elif suffix in WORD_SUFFIXES:
        lines = _docx_lines(source_path)
        method = "docx_paragraphs_tables"
    elif suffix in PDF_SUFFIXES:
        lines, page_count, state = _pdf_lines(source_path)
        method = "pdf_text_layer"
    else:
        return None

    digest, line_count, writer_truncated = _write_extracted_lines(
        destination,
        lines,
        max_chars=max_chars,
    )
    if suffix in PDF_SUFFIXES:
        extraction_truncated = extraction_truncated or state["truncated"]
    return PreparedCurationDocument(
        path=destination,
        method=method,
        sha256=digest,
        line_count=line_count,
        page_count=page_count,
        truncated=extraction_truncated or writer_truncated,
    )
