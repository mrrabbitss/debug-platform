import codecs
from pathlib import Path

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


def read_text_file(path: Path) -> str | None:
    if path.stat().st_size > get_settings().parser_max_text_bytes:
        return None
    return decode_text_bytes(path.read_bytes())
