from pathlib import Path
import zipfile

import pytest

from app.services.archive import UnsafeArchiveError, extract_archive


def test_rejects_zip_slip(tmp_path: Path):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../../escape.txt", "bad")
    with pytest.raises(UnsafeArchiveError):
        extract_archive(archive, tmp_path / "out")


def test_accepts_extensionless_text_log(tmp_path: Path):
    source = tmp_path / "001122334455_GW_collectDebuginfo_2026_03_02_11_22_490"
    source.write_text(
        "Start run collect command:WAP:get wlan basic\n"
        "NOTICE 2026-03-02 03:29:17.483[90][DC]ready\n",
        encoding="utf-8",
    )

    manifest = extract_archive(source, tmp_path / "out")

    assert manifest.files == [{"path": source.name, "size": source.stat().st_size}]
    assert (tmp_path / "out" / source.name).read_bytes() == source.read_bytes()


def test_rejects_extensionless_binary_file(tmp_path: Path):
    source = tmp_path / "binary_dump"
    source.write_bytes(b"\x00\x01\x02not-a-text-log")

    with pytest.raises(UnsafeArchiveError, match="Unsupported archive or text log file type"):
        extract_archive(source, tmp_path / "out")
