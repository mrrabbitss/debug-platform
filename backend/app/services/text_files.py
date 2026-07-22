import codecs
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from collections.abc import Iterator

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


def open_text_lines(path: Path) -> tuple[str, Iterator[str]] | None:
    """Return a lazy, NUL-sanitized text-line iterator and detected encoding."""
    encoding = detect_text_encoding(path)
    if encoding is None:
        return None

    def lines() -> Iterator[str]:
        with path.open("r", encoding=encoding, errors="replace", newline=None) as handle:
            for line in handle:
                yield line.replace("\x00", "").rstrip("\r\n")

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


def read_text_range(path: Path, start_line: int, line_count: int) -> TextRange | None:
    opened = open_text_lines(path)
    if opened is None:
        return None
    encoding, lines = opened
    selected = list(islice(lines, start_line - 1, start_line - 1 + line_count + 1))
    has_more = len(selected) > line_count
    selected = selected[:line_count]
    return TextRange(
        text="\n".join(selected),
        encoding=encoding,
        returned_lines=len(selected),
        has_more=has_more,
    )


def read_text_file(path: Path) -> str | None:
    if path.stat().st_size > get_settings().parser_max_text_bytes:
        return None
    return decode_text_bytes(path.read_bytes())
