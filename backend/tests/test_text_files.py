from pathlib import Path

from app.services.text_files import open_text_lines, read_text_file, read_text_range, search_text_lines


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


def test_sparse_line_index_supports_fast_range_and_search(tmp_path: Path):
    path = tmp_path / "large.log"
    path.write_text("".join(f"line {index}\n" for index in range(1, 1001)), encoding="utf-8")
    line_index: list[list[int]] = []

    opened = open_text_lines(path, index_stride=100, line_index=line_index)

    assert opened is not None
    encoding, lines = opened
    assert len(list(lines)) == 1000
    assert [entry[0] for entry in line_index] == list(range(1, 1001, 100))

    selected = read_text_range(
        path,
        901,
        3,
        line_index=line_index,
        encoding_hint=encoding,
    )
    assert selected is not None
    assert selected.text == "line 901\nline 902\nline 903"
    assert selected.has_more is True

    result = search_text_lines(
        path,
        "line 997",
        start_line=850,
        line_index=line_index,
        encoding_hint=encoding,
    )
    assert result is not None
    assert [(match.line_number, match.text) for match in result.matches] == [(997, "line 997")]


def test_sparse_line_index_works_with_utf16_notepad_file(tmp_path: Path):
    path = tmp_path / "utf16.log"
    path.write_bytes("".join(f"日志 {index}\n" for index in range(1, 301)).encode("utf-16"))
    line_index: list[list[int]] = []
    opened = open_text_lines(path, index_stride=50, line_index=line_index)

    assert opened is not None
    encoding, lines = opened
    assert len(list(lines)) == 300
    selected = read_text_range(path, 251, 2, line_index=line_index, encoding_hint=encoding)
    assert selected is not None
    assert selected.text == "日志 251\n日志 252"
