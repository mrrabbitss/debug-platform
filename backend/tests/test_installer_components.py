from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
INSTALLER_ROOT = PROJECT_ROOT / "deploy" / "windows-installer"
_SPEC = importlib.util.spec_from_file_location(
    "installer_component_selection_for_test", INSTALLER_ROOT / "component_selection.py",
)
assert _SPEC is not None and _SPEC.loader is not None
selection = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(selection)


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _manifest(root: Path) -> None:
    _write_json(root / "package-manifest.json", {
        "schema_version": 1,
        "files": [
            {
                "path": path.relative_to(root).as_posix(),
                "size": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in sorted(root.rglob("*"))
            if path.is_file() and path.name != "package-manifest.json"
        ],
    })


def _bundle(root: Path, *, real_python: bool = False) -> Path:
    root.mkdir(parents=True)
    for relative in (
        "runtime/python/python.exe", "runtime/llama/llama-server.exe",
        "runtime/llama/ggml.dll", "models/embedding/bge.gguf", "models/reranker/qwen.gguf",
        "licenses/models/bge/LICENSE", "licenses/models/qwen/LICENSE",
        "start.bat", "start_codeagent.bat", "web/index.html", ".env.example",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic installer test fixture")
    for filename in ("component_selection.py", "install_local.ps1", "Install.bat"):
        shutil.copy2(INSTALLER_ROOT / filename, root / filename)
    _write_json(root / "build-info.json", {"package_version": "test-1"})
    _write_json(root / "model-components.json", {
        "schema_version": 1, "bundle_id": "synthetic-test-bundle",
        "components": [
            {
                "id": task, "task_type": task,
                "executable": "runtime/llama/llama-server.exe",
                "model": f"models/{task}/{filename}.gguf",
            }
            for task, filename in (("embedding", "bge"), ("reranker", "qwen"))
        ],
    })
    (root / "portable_launcher.py").write_text(
        "import json\nfrom pathlib import Path\n"
        "from component_selection import _verified_entries\n"
        "root = Path(__file__).resolve().parent\n"
        "_verified_entries(root)\n"
        "info = json.loads((root / 'build-info.json').read_text(encoding='utf-8'))\n"
        "raise SystemExit(17 if info.get('test_fail_check') else 0)\n",
        encoding="utf-8",
    )
    if real_python:
        # Exercise the real PS 5.1 publisher using a small test-only Python shim.
        # Its pyvenv.cfg intentionally borrows this test host's stdlib; it is not
        # evidence of a distributable runtime or a real-model smoke test.
        runtime = root / "runtime" / "python"
        shutil.copy2(sys._base_executable, runtime / "python.exe")
        for path in Path(sys.base_prefix).glob("*.dll"):
            shutil.copy2(path, runtime / path.name)
        (runtime / "pyvenv.cfg").write_text(
            f"home = {sys.base_prefix}\ninclude-system-site-packages = false\n",
            encoding="utf-8",
        )
    _manifest(root)
    return root


@pytest.mark.parametrize("chosen,tasks", [
    ("Full", {"embedding", "reranker"}), ("Core", set()),
    ("Embedding", {"embedding"}), ("Reranker", {"reranker"}),
])
def test_projection_keeps_exact_selected_files_and_integrity(
    tmp_path: Path, chosen: str, tasks: set[str],
) -> None:
    root = _bundle(tmp_path / f"GWAPDebugPlatform.installing-{uuid.uuid4().hex}")
    source_manifest = (root / "package-manifest.json").read_bytes()
    core_before = (root / "start_codeagent.bat").read_bytes()
    result = selection.project_components(root, chosen)
    selection._verified_entries(root)
    components = json.loads((root / "model-components.json").read_text())
    assert {item["task_type"] for item in components["components"]} == tasks
    assert result["installed_tasks"] == sorted(tasks)
    assert result["selection"] == chosen
    assert result["core_required"] is True
    assert result["source_manifest_sha256"] == hashlib.sha256(source_manifest).hexdigest()
    assert (root / "source-package-manifest.json").read_bytes() == source_manifest
    assert (root / "start_codeagent.bat").read_bytes() == core_before
    assert (root / "runtime/python/python.exe").is_file()
    assert (root / "runtime/llama/llama-server.exe").exists() is bool(tasks)
    for task, filename in (("embedding", "bge"), ("reranker", "qwen")):
        assert (root / f"models/{task}/{filename}.gguf").exists() is (task in tasks)
    info = json.loads((root / "build-info.json").read_text())
    assert info["local_model_runtime_bundled"] is bool(tasks)
    assert info["default_embedding"] == ("bundled_gguf" if "embedding" in tasks else "hashing")
    assert info["default_reranker"] == ("bundled_gguf" if "reranker" in tasks else "disabled")


@pytest.mark.parametrize("fault", ["tamper", "unexpected", "duplicate", "traversal"])
def test_source_validation_precedes_all_component_removal(tmp_path: Path, fault: str) -> None:
    root = _bundle(tmp_path / f"GWAPDebugPlatform.installing-{uuid.uuid4().hex}")
    if fault == "tamper":
        (root / "models/reranker/qwen.gguf").write_bytes(b"tampered unselected model")
    elif fault == "unexpected":
        (root / "unexpected.txt").write_text("unlisted", encoding="utf-8")
    else:
        manifest = json.loads((root / "package-manifest.json").read_text())
        entry = dict(manifest["files"][0])
        entry["path"] = entry["path"].upper() if fault == "duplicate" else "../outside.txt"
        manifest["files"].append(entry)
        _write_json(root / "package-manifest.json", manifest)
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    with pytest.raises(ValueError):
        selection.project_components(root, "Core")
    assert before == {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_projector_cannot_modify_published_or_arbitrary_directories(tmp_path: Path) -> None:
    root = _bundle(tmp_path / "GWAPDebugPlatform")
    with pytest.raises(ValueError, match="unpublished installer staging"):
        selection.project_components(root, "Core")
    assert (root / "models/embedding/bge.gguf").exists()


def test_readding_omitted_components_requires_original_bundle(tmp_path: Path) -> None:
    root = _bundle(tmp_path / f"GWAPDebugPlatform.installing-{uuid.uuid4().hex}")
    selection.project_components(root, "Core")
    before = (root / "package-manifest.json").read_bytes()
    with pytest.raises(ValueError, match="original full Setup/ZIP"):
        selection.project_components(root, "Full")
    assert (root / "package-manifest.json").read_bytes() == before
    selection._verified_entries(root)


def test_setup_and_zip_share_optional_component_projection() -> None:
    inno = (INSTALLER_ROOT / "DebugPlatform.iss").read_text(encoding="utf-8")
    publisher = (INSTALLER_ROOT / "install_local.ps1").read_text(encoding="utf-8")
    bat = (INSTALLER_ROOT / "Install.bat").read_text(encoding="utf-8")
    components = inno.split("[Components]", 1)[1].split("[Tasks]", 1)[0]
    assert components.count("Flags: fixed") == 1
    for task in ("embedding", "reranker"):
        assert f"WizardIsComponentSelected('retrieval\\{task}')" in inno
    assert "-Components ' + ComponentSelection" in inno
    assert "start_codeagent.bat" in inno and "start_codeagent.bat" in publisher
    assert "-ChooseComponents" in bat
    assert '"Auto", "Full", "Core", "Embedding", "Reranker"' in publisher
    assert "Get-PreviousComponentSelection" in publisher
    assert publisher.index("& $python -B -s $componentProjector") < publisher.index(
        "& $python -B -s $launcher",
    ) < publisher.index("Move-Item -LiteralPath $staging -Destination $destination")


def test_real_package_verifier_uses_only_new_isolated_install_and_data_roots() -> None:
    verifier = (PROJECT_ROOT / "scripts" / "verify_windows_component_install.ps1").read_text(
        encoding="utf-8",
    )
    assert "The verification output directory already exists" in verifier
    assert "Verification output must not be inside the source package" in verifier
    assert '"artifacts\\installer"' in verifier
    assert '"-InstallRoot", $installedRoot, "-NoLaunch", "-NoShortcuts"' in verifier
    assert '"--data-root", $probeData, "--env-file", $probeEnv' in verifier
    assert "--check-models" not in verifier
    assert "Remove-Item" not in verifier
    assert "Start-Process" not in verifier
    for choice in ("Core", "Auto", "Embedding", "Reranker", "Full"):
        assert f'Requested = "{choice}"' in verifier
    assert "source-package-manifest.json" in verifier
    assert "external_marker_unchanged = $true" in verifier
    assert "no_staging_or_backup_left = $true" in verifier


def test_setup_lifecycle_verifier_requires_empty_state_and_waits_for_true_exit() -> None:
    verifier = (PROJECT_ROOT / "scripts" / "verify_windows_setup_install.ps1").read_text(
        encoding="utf-8",
    )
    assert "Default lifecycle test requires an empty initial state" in verifier
    assert verifier.index("Default lifecycle test requires an empty initial state") < verifier.index(
        "New-Item -ItemType Directory -Path $businessDataRoot",
    )
    assert "[Diagnostics.Process]::Start($startInfo)" in verifier
    assert "$process.WaitForExit(1000)" in verifier
    assert "$exitCode = $process.ExitCode" in verifier
    assert "$startInfo.CreateNoWindow = $true" in verifier
    assert "Assert-NoInstalledApplicationProcess" in verifier
    assert "Remove-Item" not in verifier
    assert "--check-models" not in verifier
    assert '--data-root (Join-Path $runRoot "probe-data")' in verifier
    assert '--env-file (Join-Path $runRoot "probe.env")' in verifier
    for choice in ("core", "embedding", "reranker", "full"):
        assert f'Type = "{choice}"' in verifier
    assert "business_data_marker_preserved_at = $businessMarker" in verifier


def _install(source: Path, target: Path, mode: str = "Auto") -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment.pop("PYTHONHOME", None)
    environment.pop("PYTHONPATH", None)
    return subprocess.run([
        "powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
        "-ExecutionPolicy", "Bypass", "-File", str(source / "install_local.ps1"),
        "-InstallRoot", str(target), "-NoLaunch", "-NoShortcuts", "-Components", mode,
    ], capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=environment, timeout=90, check=False)


@pytest.mark.skipif(sys.platform != "win32", reason="Actual Windows PowerShell 5.1 publisher")
def test_win11_publisher_selection_upgrade_and_data_boundary(tmp_path: Path) -> None:
    source = _bundle(tmp_path / "完整 bundle 中文 [fixture]", real_python=True)
    target = tmp_path / "安装 target 中文 [fixture]" / "GWAPDebugPlatform"
    external_data = tmp_path / "user-data"
    external_data.mkdir()
    (external_data / "keep.txt").write_text("user data must stay", encoding="utf-8")
    for chosen, expected in (
        ("Core", "Core"), ("Auto", "Core"), ("Embedding", "Embedding"),
        ("Reranker", "Reranker"), ("Full", "Full"),
    ):
        result = _install(source, target, chosen)
        assert result.returncode == 0, result.stdout + result.stderr
        installed = json.loads((target / "installation-selection.json").read_text())
        assert installed["selection"] == expected
        selection._verified_entries(target)
        assert (external_data / "keep.txt").read_text() == "user data must stay"
        assert not list(target.parent.glob("GWAPDebugPlatform.installing-*"))
        assert not list(target.parent.glob("GWAPDebugPlatform.backup-*"))
    result = _install(target, target, "Core")
    assert result.returncode != 0
    assert "original full Setup/ZIP" in result.stdout + result.stderr
    assert json.loads((target / "installation-selection.json").read_text())["selection"] == "Full"


@pytest.mark.skipif(sys.platform != "win32", reason="Actual Windows PowerShell 5.1 publisher")
def test_failed_component_check_preserves_previous_install(tmp_path: Path) -> None:
    source = _bundle(tmp_path / "source", real_python=True)
    target = tmp_path / "installed" / "GWAPDebugPlatform"
    result = _install(source, target, "Core")
    assert result.returncode == 0, result.stdout + result.stderr
    before = (target / "package-manifest.json").read_bytes()
    _write_json(source / "build-info.json", {"test_fail_check": True})
    _manifest(source)
    result = _install(source, target, "Full")
    assert result.returncode != 0
    assert "exit code 17" in result.stdout + result.stderr
    assert (target / "package-manifest.json").read_bytes() == before
    selection._verified_entries(target)
    assert not list(target.parent.glob("GWAPDebugPlatform.installing-*"))
