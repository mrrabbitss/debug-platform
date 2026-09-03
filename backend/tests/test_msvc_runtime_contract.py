import copy
import json
import subprocess
import sys
from pathlib import Path

from app.core.config import PROJECT_ROOT


MANIFEST_PATH = PROJECT_ROOT / "scripts" / "model-runtime" / "model-assets.json"
VALIDATOR_PATH = PROJECT_ROOT / "scripts" / "model-runtime" / "validate_assets.py"


def _manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _run_validator(manifest: dict, path: Path) -> subprocess.CompletedProcess[str]:
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(VALIDATOR_PATH), "--manifest", str(path)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def test_msvc_app_local_manifest_is_immutable_complete_and_non_open_source() -> None:
    dependency = _manifest()["native_dependencies"][0]

    assert dependency["id"] == "microsoft-vc143-crt-x64-app-local"
    assert dependency["version"] == "14.44.35211.0"
    assert dependency["source_package"] == {
        "url": (
            "https://download.visualstudio.microsoft.com/download/pr/"
            "45d3b8dd-bced-4b37-9974-142f748d710c/"
            "4aaf54db0bfc9435f7c3660e1a00237a4b556042bfeea64bde44c2e0194e6ee5/"
            "Microsoft.VC.14.44.17.14.CRT.Redist.X64.base.vsix"
        ),
        "relative_path": (
            "downloads/Microsoft.VC.14.44.17.14.CRT.Redist.X64.base.vsix"
        ),
        "size_bytes": 3_224_191,
        "sha256": "4aaf54db0bfc9435f7c3660e1a00237a4b556042bfeea64bde44c2e0194e6ee5",
    }
    assert dependency["license"]["id"].startswith("LicenseRef-Microsoft-")
    assert "spdx" not in dependency["license"]
    assert dependency["license"]["redistribution"] == "app_local_unmodified"
    assert len(dependency["files"]) == 10
    assert {
        Path(item["install_path"]).name.lower() for item in dependency["files"]
    } == {
        "concrt140.dll",
        "msvcp140.dll",
        "msvcp140_1.dll",
        "msvcp140_2.dll",
        "msvcp140_atomic_wait.dll",
        "msvcp140_codecvt_ids.dll",
        "vccorlib140.dll",
        "vcruntime140.dll",
        "vcruntime140_1.dll",
        "vcruntime140_threads.dll",
    }
    assert all(
        "debug_nonredist" not in item["archive_path"].lower()
        and item["archive_path"].startswith(
            "Contents/VC/Redist/MSVC/14.44.35112/x64/Microsoft.VC143.CRT/"
        )
        and item["install_path"].startswith("runtime/llama/")
        for item in dependency["files"]
    )


def test_validator_rejects_missing_or_debug_nonredist_crt_files(tmp_path: Path) -> None:
    missing = copy.deepcopy(_manifest())
    missing["native_dependencies"][0]["files"].pop()
    missing_result = _run_validator(missing, tmp_path / "missing.json")
    assert missing_result.returncode == 1
    assert "release CRT file set is incomplete" in missing_result.stderr

    debug = copy.deepcopy(_manifest())
    debug["native_dependencies"][0]["files"][0]["archive_path"] = (
        "Contents/VC/Redist/MSVC/14.44.35112/debug_nonredist/x64/"
        "Microsoft.VC143.DebugCRT/concrt140.dll"
    )
    debug_result = _run_validator(debug, tmp_path / "debug.json")
    assert debug_result.returncode == 1
    assert "release CRT directory" in debug_result.stderr


def test_build_and_smoke_gate_verify_real_app_local_msvc_loading() -> None:
    builder = (PROJECT_ROOT / "scripts" / "build_windows_gguf_installer.ps1").read_text(
        encoding="utf-8"
    )
    smoke = (PROJECT_ROOT / "scripts" / "model-runtime" / "smoke_runtime.py").read_text(
        encoding="utf-8"
    )

    assert "Get-AuthenticodeSignature" in builder
    assert "Microsoft VC runtime Authenticode verification failed" in builder
    assert "CreateToolhelp32Snapshot" in smoke
    assert '{"msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll"}' in smoke
    assert "used system/global VC runtime DLLs instead of app-local files" in smoke

