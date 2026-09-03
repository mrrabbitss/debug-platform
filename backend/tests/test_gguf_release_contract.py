import re
import subprocess
import sys

import yaml

from app.core.config import PROJECT_ROOT


def test_pinned_gguf_asset_manifest_contract_is_valid() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/model-runtime/validate_assets.py"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Pinned manifest contract is valid" in result.stdout


def test_full_gguf_workflow_is_explicit_pinned_and_release_gated() -> None:
    workflow_path = (
        PROJECT_ROOT / ".github" / "workflows" / "windows-gguf-installer.yml"
    )
    content = workflow_path.read_text(encoding="utf-8")
    workflow = yaml.load(content, Loader=yaml.BaseLoader)

    assert set(workflow["on"]) == {"workflow_dispatch", "release"}
    assert workflow["on"]["release"]["types"] == ["published"]
    assert "runs-on: windows-2022" in content
    assert "windows-latest" not in content
    assert "ubuntu-latest" not in content

    revisions = re.findall(r"(?m)^\s*uses:\s*[^\s@]+@([^\s#]+)", content)
    assert revisions
    assert all(re.fullmatch(r"[0-9a-f]{40}", revision) for revision in revisions)
    assert "--strict-release" in content
    assert "scripts/model-runtime/prepare_assets.py" in content
    assert "scripts/build_windows_gguf_installer.ps1" in content
    assert "-RequireSetupExe" in content
    assert "-SkipModelSmoke" not in content
    assert "-SkipSmokeTest" not in content
    assert "provenance.json.sha256" in content


def test_installer_preserves_the_manifest_protected_application_tree() -> None:
    inno = (
        PROJECT_ROOT / "deploy" / "windows-installer" / "DebugPlatform.iss"
    ).read_text(encoding="utf-8")
    builder = (
        PROJECT_ROOT / "scripts" / "build_windows_gguf_installer.ps1"
    ).read_text(encoding="utf-8")

    assert (
        "UninstallFilesDir={localappdata}\\Programs\\GWAPDebugPlatform-Uninstall"
        in inno
    )
    assert "component-provenance.json" in builder
    assert "model-runtime\\smoke_runtime.py" in builder
    assert '"$provenanceOutput.sha256"' in builder
    assert "model-runtime\\validate_assets.py" in builder
    assert '"--component-lock", $lockPath' in builder


def test_inno_setup_uses_the_atomic_payload_publisher() -> None:
    inno = (
        PROJECT_ROOT / "deploy" / "windows-installer" / "DebugPlatform.iss"
    ).read_text(encoding="utf-8")
    installer = (
        PROJECT_ROOT / "deploy" / "windows-installer" / "install_local.ps1"
    ).read_text(encoding="utf-8")

    files_section = inno.split("[Files]", maxsplit=1)[1].split(
        "[Icons]", maxsplit=1
    )[0]
    assert 'DestDir: "{app}"' not in files_section
    assert 'DestDir: "{tmp}\\GWAPDebugPlatformPayload"' in files_section
    assert 'Excludes: "package-manifest.json"' in files_section
    assert files_section.index('Source: "{#SourceRoot}\\*"') < files_section.index(
        'Source: "{#SourceRoot}\\package-manifest.json"'
    )
    assert "AfterInstall: InstallPayloadAtomically" in files_section
    assert files_section.count("deleteafterinstall") == 2

    assert "ewWaitUntilTerminated" in inno
    assert "if ResultCode <> 0 then" in inno
    assert "RaiseException" in inno
    assert "-NoLaunch -NoShortcuts" in inno
    assert "RegisterExtraCloseApplicationsResource" in inno
    assert (
        'Type: filesandordirs; Name: "{app}"; Check: IsExpectedAppRoot' in inno
    )
    assert "{localappdata}\\GWAPDebugPlatform" not in inno

    assert "[switch]$NoShortcuts" in installer
    assert installer.count("if (-not $NoShortcuts)") == 2


def test_atomic_publisher_serializes_processes_and_recovers_one_backup() -> None:
    installer = (
        PROJECT_ROOT / "deploy" / "windows-installer" / "install_local.ps1"
    ).read_text(encoding="utf-8")

    assert '"Local\\GWAPDebugPlatform.Install"' in installer
    assert "[ValidateRange(1, 600)][int]$LockTimeoutSeconds = 120" in installer
    assert "$installMutex.WaitOne(" in installer
    assert "[System.Threading.AbandonedMutexException]" in installer
    assert "Timed out after $LockTimeoutSeconds seconds" in installer
    assert "$installMutex.ReleaseMutex()" in installer
    assert "$installMutex.Dispose()" in installer

    assert "function Assert-RestorableBackup" in installer
    assert "'^GWAPDebugPlatform\\.backup-[0-9a-f]{32}$'" in installer
    assert "$backupCandidates.Count -gt 1" in installer
    assert "refusing to guess which backup to restore" in installer
    assert "$backupCandidates.Count -eq 1" in installer
    assert "Assert-RestorableBackup -Path $recoveryBackup" in installer
    assert "package-manifest.json" in installer
    assert '"runtime\\python\\python.exe"' in installer
    assert "Move-Item -LiteralPath $recoveryBackup -Destination $destination" in installer


def test_generated_model_weights_are_excluded_from_git() -> None:
    gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "/models/" in gitignore
    assert "/artifacts/build-cache/" in gitignore
    assert "/artifacts/installer/" in gitignore

    result = subprocess.run(
        ["git", "ls-files", "--", "*.gguf", "*.safetensors", "*.bin"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert not result.stdout.strip(), result.stdout


def test_portable_release_bundles_and_smokes_the_recorded_glm_demo() -> None:
    builder = (
        PROJECT_ROOT / "scripts" / "build_windows_portable.ps1"
    ).read_text(encoding="utf-8")
    verifier = (
        PROJECT_ROOT / "scripts" / "verify_windows_portable.ps1"
    ).read_text(encoding="utf-8")

    assert "sample_data\\demo_ap_frequent_offline" in builder
    assert "demo_data\\ap_frequent_offline" in builder
    assert "synthetic_demo_bundled = $true" in builder
    assert "demo-cases/ap-frequent-offline" in verifier
    assert "Bundled recorded GLM-5.2 demo, source jumps, usage and diagnosis passed" in verifier
    for fixture in (
        "GW_collectDebuginfo_demo.txt",
        "AP_collectDebuginfo_demo.txt",
        "glm52_success_snapshot.json",
        "methods\\manifest.json",
        "methods\\ap-offline-log-analysis.md",
        "methods\\ap-offline-fault-tree.md",
    ):
        assert fixture in verifier
