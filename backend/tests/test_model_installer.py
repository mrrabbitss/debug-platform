import importlib.util
import json
import sys
from pathlib import Path

import pytest

from app.core.config import PROJECT_ROOT


SCRIPT_PATH = PROJECT_ROOT / "scripts" / "validate_local_models.py"


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


def test_model_installer_writes_hf_cli_manifest(tmp_path: Path):
    installer = load_installer_module()
    installer.ensure_layout(tmp_path)
    models = []
    for spec in installer.MODEL_SPECS:
        target = tmp_path / spec.relative_dir
        target.mkdir(parents=True, exist_ok=True)
        (target / "config.json").write_text("{}", encoding="utf-8")
        (target / "tokenizer.json").write_text("{}", encoding="utf-8")
        (target / "model.safetensors").write_bytes(b"safe-model-placeholder")
        models.append(installer.verify_model_dir(spec, target))

    manifest_path = installer.write_manifest(
        tmp_path,
        endpoint="https://hf-mirror.com",
        models=models,
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["download_method"] == "hf download --local-dir"
    assert [model["repo_id"] for model in manifest["models"]] == [
        "BAAI/bge-base-zh-v1.5",
        "Qwen/Qwen3-Reranker-0.6B",
    ]
