import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from app.core.config import PROJECT_ROOT


SCRIPT_PATH = PROJECT_ROOT / "scripts" / "download_local_models.py"


def load_installer_module():
    spec = importlib.util.spec_from_file_location("debug_platform_model_installer", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_model_installer_creates_expected_layout_and_validates_snapshots(tmp_path: Path):
    installer = load_installer_module()
    installer.ensure_layout(tmp_path)

    assert {path.name for path in tmp_path.iterdir()} == {
        "inference",
        "reranker",
        "embedding",
    }
    assert [spec.repo_id for spec in installer.MODEL_SPECS] == [
        "BAAI/bge-base-zh-v1.5",
        "Qwen/Qwen3-Reranker-0.6B",
    ]

    spec = installer.MODEL_SPECS[0]
    target = tmp_path / spec.relative_dir
    target.mkdir(parents=True, exist_ok=True)
    with pytest.raises(RuntimeError, match="incomplete"):
        installer.verify_model_dir(spec, target)

    (target / "config.json").write_text("{}", encoding="utf-8")
    (target / "tokenizer.json").write_text("{}", encoding="utf-8")
    (target / "model.safetensors").write_bytes(b"safe-model-placeholder")
    result = installer.verify_model_dir(spec, target)
    assert result["repo_id"] == "BAAI/bge-base-zh-v1.5"
    assert result["file_count"] == 3


def test_model_installer_rejects_unsafe_mirror_urls():
    installer = load_installer_module()

    assert installer.validate_endpoint("https://hf-mirror.com/") == "https://hf-mirror.com"
    with pytest.raises(ValueError):
        installer.validate_endpoint("file:///company/model")
    with pytest.raises(ValueError):
        installer.validate_endpoint("https://user:secret@hf-mirror.com")


def test_model_installer_pins_revision_skips_bin_and_retries(tmp_path: Path, monkeypatch):
    installer = load_installer_module()
    calls: list[dict] = []

    class FakeApi:
        def __init__(self, endpoint):
            assert endpoint == "https://hf-mirror.com"

        def model_info(self, repo_id, files_metadata):
            assert repo_id == "BAAI/bge-base-zh-v1.5"
            assert files_metadata is True
            return SimpleNamespace(
                sha="abc123def456",
                siblings=[
                    SimpleNamespace(rfilename="config.json", size=100),
                    SimpleNamespace(rfilename="model.safetensors", size=1000),
                    SimpleNamespace(rfilename="pytorch_model.bin", size=1000),
                ],
            )

    def fake_snapshot_download(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise OSError("temporary mirror failure")
        target = Path(kwargs["local_dir"])
        (target / "config.json").write_text("{}", encoding="utf-8")
        (target / "tokenizer.json").write_text("{}", encoding="utf-8")
        (target / "model.safetensors").write_bytes(b"safe-model-placeholder")

    fake_hub = ModuleType("huggingface_hub")
    fake_hub.HfApi = FakeApi
    fake_hub.snapshot_download = fake_snapshot_download
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hub)
    monkeypatch.setattr(installer.time, "sleep", lambda seconds: None)

    result = installer.download_model(
        installer.MODEL_SPECS[0],
        tmp_path,
        endpoint="https://hf-mirror.com",
        max_workers=4,
        retries=2,
    )

    assert len(calls) == 2
    assert calls[1]["revision"] == "abc123def456"
    assert "*.bin" in calls[1]["ignore_patterns"]
    assert calls[1]["max_workers"] == 4
    assert result["repo_id"] == "BAAI/bge-base-zh-v1.5"


def test_model_installer_does_not_swallow_disk_space_failure(tmp_path: Path, monkeypatch):
    installer = load_installer_module()

    class FakeApi:
        def __init__(self, endpoint):
            pass

        def model_info(self, repo_id, files_metadata):
            return SimpleNamespace(
                sha="abc123",
                siblings=[SimpleNamespace(rfilename="model.safetensors", size=1000)],
            )

    fake_hub = ModuleType("huggingface_hub")
    fake_hub.HfApi = FakeApi
    fake_hub.snapshot_download = lambda **kwargs: pytest.fail("download must not start")
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hub)

    def fail_disk_check(*args):
        raise RuntimeError("Insufficient disk space")

    monkeypatch.setattr(installer, "_check_disk_space", fail_disk_check)

    with pytest.raises(RuntimeError, match="Insufficient disk space"):
        installer.download_model(
            installer.MODEL_SPECS[0],
            tmp_path,
            endpoint="https://hf-mirror.com",
            max_workers=4,
            retries=1,
        )
