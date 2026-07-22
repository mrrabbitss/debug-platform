from pathlib import Path

import pytest

from app.services.storage import StorageService, normalize_debug_log_filename


def test_normalize_debug_log_filename_adds_txt_to_extensionless_name():
    original, normalized = normalize_debug_log_filename(
        "001122334455_GW_collectDebuginfo_2026_03_02_11_22_490"
    )

    assert original == "001122334455_GW_collectDebuginfo_2026_03_02_11_22_490"
    assert normalized == "001122334455_GW_collectDebuginfo_2026_03_02_11_22_490.txt"


def test_normalize_debug_log_filename_preserves_existing_suffix_and_removes_path():
    original, normalized = normalize_debug_log_filename(r"C:\fakepath\device.log")

    assert original == "device.log"
    assert normalized == "device.log"


def test_storage_keys_are_portable_and_cleanup_stays_inside_root(tmp_path: Path):
    service = StorageService(tmp_path / "storage")
    artifact_file = service.artifact_dir("ART-test") / "device.log"
    artifact_file.write_text("test", encoding="utf-8")

    key = service.storage_key(artifact_file)
    assert key == "artifacts/ART-test/device.log"
    assert service.resolve_path(key) == artifact_file.resolve()
    assert service.remove_artifact("ART-test") is True
    assert not artifact_file.exists()


def test_storage_cleanup_rejects_escape_attempt(tmp_path: Path):
    service = StorageService(tmp_path / "storage")
    outside = tmp_path / "outside"
    outside.mkdir()

    with pytest.raises(ValueError, match="outside"):
        service._remove_managed_tree(outside)
    assert outside.exists()
