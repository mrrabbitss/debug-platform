from pathlib import Path

from app.services.text_files import read_text_file


def test_read_text_file_supports_utf16_notepad_files(tmp_path: Path):
    path = tmp_path / "collectDebuginfo"
    expected = "NOTICE 2026-03-02 03:29:17.483[90][DC]设备正常\n"
    path.write_bytes(expected.encode("utf-16"))

    assert read_text_file(path) == expected


def test_read_text_file_rejects_binary_data(tmp_path: Path):
    path = tmp_path / "binary"
    path.write_bytes(b"header\x00payload")

    assert read_text_file(path) is None


def test_read_text_file_accepts_and_removes_sparse_nul_bytes(tmp_path: Path):
    path = tmp_path / "collectDebuginfo"
    raw = (
        b"Wait for collection\n"
        + b"A" * 2048
        + b"\x00"
        + b"\nNOTICE 2026-03-02 03:29:17.483[90][DC]ready\n"
    )
    path.write_bytes(raw)

    text = read_text_file(path)

    assert text is not None
    assert "\x00" not in text
    assert "NOTICE 2026-03-02 03:29:17.483" in text
