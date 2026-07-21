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
