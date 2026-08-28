import codecs
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from charset_normalizer import from_bytes

from app.core.config import get_settings


_TEXT_BOMS = (
    codecs.BOM_UTF8,
    codecs.BOM_UTF16_LE,
    codecs.BOM_UTF16_BE,
    codecs.BOM_UTF32_LE,
    codecs.BOM_UTF32_BE,
)
_ALLOWED_CONTROL_BYTES = {8, 9, 10, 12, 13}


def looks_like_text_bytes(raw: bytes) -> bool:
    """Reject obvious binary data without relying on a file extension."""
    if not raw:
        return True
    if raw.startswith(_TEXT_BOMS):
        return True
    control_count = sum(byte < 32 and byte not in _ALLOWED_CONTROL_BYTES for byte in raw)
    return control_count / len(raw) <= 0.01


def looks_like_text_file(path: Path, sample_size: int = 64 * 1024) -> bool:
    with path.open("rb") as handle:
        return looks_like_text_bytes(handle.read(sample_size))


def decode_text_bytes(raw: bytes) -> str | None:
    if not looks_like_text_bytes(raw[:64 * 1024]):
        return None
    if raw.startswith(codecs.BOM_UTF8):
        text = raw.decode("utf-8-sig", errors="replace")
        return text.replace("\x00", "")
    if raw.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        text = raw.decode("utf-32", errors="replace")
        return text.replace("\x00", "")
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        text = raw.decode("utf-16", errors="replace")
        return text.replace("\x00", "")
    match = from_bytes(raw).best()
    if match is None:
        text = raw.decode("utf-8", errors="replace")
    else:
        text = str(match)
    return text.replace("\x00", "")


def detect_text_encoding(path: Path, sample_size: int = 256 * 1024) -> str | None:
    if path.stat().st_size > get_settings().parser_max_text_bytes:
        return None
    with path.open("rb") as handle:
        raw = handle.read(sample_size)
    if not looks_like_text_bytes(raw[:64 * 1024]):
        return None
    if raw.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if raw.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        return "utf-32"
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"
    try:
        raw.decode("utf-8", errors="strict")
        return "utf-8"
    except UnicodeDecodeError:
        match = from_bytes(raw).best()
        return str(match.encoding) if match and match.encoding else "utf-8"


def open_text_lines(
    path: Path,
    *,
    index_stride: int = 0,
    line_index: list[list[int]] | None = None,
) -> tuple[str, Iterator[str]] | None:
    """Return lazy text lines and optionally collect sparse seek cookies.

    TextIOWrapper positions are deliberately treated as opaque seek cookies.
    They remain valid for the same file, encoding and newline mode, including
    UTF-16/UTF-32 files where raw byte offsets are not safe line boundaries.
    """
    encoding = detect_text_encoding(path)
    if encoding is None:
        return None

    def lines() -> Iterator[str]:
        with path.open("r", encoding=encoding, errors="replace", newline=None) as handle:
            line_number = 1
            while True:
                cookie = handle.tell()
                line = handle.readline()
                if line == "":
                    break
                if line_index is not None and index_stride > 0 and (line_number - 1) % index_stride == 0:
                    line_index.append([line_number, cookie])
                yield line.replace("\x00", "").rstrip("\r\n")
                line_number += 1

    return encoding, lines()


def read_text_sample(path: Path, max_chars: int = 20000) -> tuple[str, str] | None:
    opened = open_text_lines(path)
    if opened is None:
        return None
    encoding, lines = opened
    parts: list[str] = []
    length = 0
    for line in lines:
        part = line + "\n"
        parts.append(part)
        length += len(part)
        if length >= max_chars:
            break
    return "".join(parts)[:max_chars], encoding


@dataclass(frozen=True)
class TextRange:
    text: str
    encoding: str
    returned_lines: int
    has_more: bool


@dataclass(frozen=True)
class TextMatch:
    line_number: int
    text: str


@dataclass(frozen=True)
class TextSearchResult:
    matches: list[TextMatch]
    encoding: str
    scanned_from_line: int
    scanned_to_line: int
    has_more: bool


def _validated_line_index(value: Any) -> list[tuple[int, int]]:
    entries: list[tuple[int, int]] = []
    if not isinstance(value, list):
        return entries
    for item in value:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        line_number, cookie = item
        if not isinstance(line_number, int) or not isinstance(cookie, int):
            continue
        if line_number < 1 or cookie < 0:
            continue
        if entries and (line_number <= entries[-1][0] or cookie < entries[-1][1]):
            continue
        entries.append((line_number, cookie))
    return entries


def _seek_to_line(handle, start_line: int, line_index: Any) -> int:
    base_line = 1
    cookie = 0
    for indexed_line, indexed_cookie in _validated_line_index(line_index):
        if indexed_line > start_line:
            break
        base_line = indexed_line
        cookie = indexed_cookie
    try:
        handle.seek(cookie)
    except (OSError, ValueError):
        handle.seek(0)
        base_line = 1
    while base_line < start_line:
        if handle.readline() == "":
            break
        base_line += 1
    return base_line


def _read_sanitized_line(handle) -> str | None:
    line = handle.readline()
    if line == "":
        return None
    return line.replace("\x00", "").rstrip("\r\n")


def read_text_range(
    path: Path,
    start_line: int,
    line_count: int,
    *,
    line_index: Any = None,
    encoding_hint: str | None = None,
) -> TextRange | None:
    encoding = encoding_hint or detect_text_encoding(path)
    if encoding is None:
        return None
    selected: list[str] = []
    with path.open("r", encoding=encoding, errors="replace", newline=None) as handle:
        _seek_to_line(handle, start_line, line_index)
        for _ in range(line_count):
            line = _read_sanitized_line(handle)
            if line is None:
                break
            selected.append(line)
        has_more = _read_sanitized_line(handle) is not None
    return TextRange(
        text="\n".join(selected),
        encoding=encoding,
        returned_lines=len(selected),
        has_more=has_more,
    )


def search_text_lines(
    path: Path,
    query: str,
    *,
    start_line: int = 1,
    max_matches: int = 100,
    max_scan_lines: int = 250000,
    line_index: Any = None,
    encoding_hint: str | None = None,
) -> TextSearchResult | None:
    encoding = encoding_hint or detect_text_encoding(path)
    if encoding is None:
        return None
    folded_query = query.casefold()
    if not folded_query:
        raise ValueError("Search query must not be empty")

    matches: list[TextMatch] = []
    scanned_to = start_line - 1
    has_more = False
    with path.open("r", encoding=encoding, errors="replace", newline=None) as handle:
        current_line = _seek_to_line(handle, start_line, line_index)
        while current_line < start_line:
            current_line += 1
        for _ in range(max_scan_lines):
            line = _read_sanitized_line(handle)
            if line is None:
                break
            scanned_to = current_line
            if folded_query in line.casefold():
                matches.append(TextMatch(line_number=current_line, text=line[:2000]))
                if len(matches) >= max_matches:
                    has_more = _read_sanitized_line(handle) is not None
                    break
            current_line += 1
        else:
            has_more = _read_sanitized_line(handle) is not None

    return TextSearchResult(
        matches=matches,
        encoding=encoding,
        scanned_from_line=start_line,
        scanned_to_line=scanned_to,
        has_more=has_more,
    )


def read_text_file(path: Path) -> str | None:
    if path.stat().st_size > get_settings().parser_max_text_bytes:
        return None
    return decode_text_bytes(path.read_bytes())
