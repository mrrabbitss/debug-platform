"""Exercise setup sequencing and publication with synthetic build stages."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
POWERSHELL = shutil.which("powershell.exe")
pytestmark = pytest.mark.skipif(os.name != "nt" or not POWERSHELL, reason="Windows setup entrypoint")


def execute(tmp_path, body):
    script = tmp_path / "driver.ps1"
    script.write_text(". $env:SETUP_SOURCE\n" + body, encoding="utf-8-sig")
    return subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        env={**os.environ, "SETUP_SOURCE": str(ROOT / "scripts/setup_lan_server.ps1"),
             "SETUP_ROOT": str(tmp_path / "repo space 中文"), "SETUP_PYTHON": sys.executable},
        capture_output=True, text=True, encoding="utf-8-sig", errors="replace", timeout=25)


def test_plan_does_not_create_repository_or_start_build(tmp_path):
    result = execute(tmp_path, "Invoke-ServerSetup -RepositoryRoot $env:SETUP_ROOT -DryRun")
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(json.loads(result.stdout)["steps"]) == 4
    assert not (tmp_path / "repo space 中文").exists()


def test_existing_partial_directory_is_preserved(tmp_path):
    target = tmp_path / "repo space 中文/artifacts/lan/server-pilot-20260907"
    target.mkdir(parents=True)
    sentinel = target / "keep.txt"
    sentinel.write_text("existing deployment")
    result = execute(tmp_path, "Invoke-ServerSetup -RepositoryRoot $env:SETUP_ROOT")
    assert result.returncode != 0 and "not overwritten" in result.stderr
    assert sentinel.read_text() == "existing deployment"


def test_python_selection_checks_real_interpreter_version(tmp_path):
    result = execute(tmp_path, "Find-SetupPython $env:SETUP_PYTHON")
    if sys.version_info[:2] == (3, 12):
        assert result.returncode == 0, result.stdout + result.stderr
        assert Path(result.stdout.strip()) == Path(sys.executable)
    else:
        assert result.returncode != 0 and "Python 3.12" in result.stderr


@pytest.mark.parametrize("failure", [0, 1, 2, 3, 4, 5])
def test_build_failure_stops_and_only_verified_output_is_published(tmp_path, failure):
    # Substitute costly build commands, while running the real orchestration,
    # locking, argument construction, error propagation and directory publication.
    result = execute(tmp_path, r'''
function Find-SetupPython { return $env:SETUP_PYTHON }
$script:calls = @()
$script:validationFailed = $false
function Invoke-SetupCommand {
    param([string]$FilePath, [string[]]$Arguments)
    if ($FilePath -eq 'node.exe') { return }
    $script:calls += @{ file=$FilePath; arguments=$Arguments }
    if ($script:calls.Count -eq FAILURE) { throw 'synthetic stage failure' }
    if ($script:calls.Count -eq 4) {
        $staging = $Arguments[$Arguments.IndexOf('--output') + 1]
        [IO.Directory]::CreateDirectory($staging) | Out-Null
        [IO.File]::WriteAllText((Join-Path $staging 'verified.txt'), 'synthetic package')
    }
}
function Test-SetupPackage {
    param([string]$Package)
    if (FAILURE -eq 5) { throw 'synthetic verification failure' }
    if (-not (Test-Path (Join-Path $Package 'verified.txt'))) { throw 'Missing staged output' }
}
try { Invoke-ServerSetup -RepositoryRoot $env:SETUP_ROOT }
catch { $script:validationFailed = $true }
[ordered]@{ failed=$script:validationFailed; calls=$script:calls } | ConvertTo-Json -Depth 5 -Compress |
    Set-Content -Encoding UTF8 (Join-Path $env:SETUP_ROOT 'trace.json')
'''.replace("FAILURE", str(failure)))
    assert result.returncode == 0, result.stdout + result.stderr
    root = tmp_path / "repo space 中文"
    trace = json.loads((root / "trace.json").read_text(encoding="utf-8-sig"))
    assert trace["failed"] == (failure != 0)
    assert len(trace["calls"]) == (failure if 1 <= failure <= 4 else 4)
    target = root / "artifacts/lan/server-pilot-20260907"
    assert target.exists() == (failure == 0)
    if failure == 0:
        calls = trace["calls"]
        assert calls[0]["arguments"] == ["--no-pause"]
        assert "--all" in calls[1]["arguments"]
        assert "-SkipSetupExe" in calls[2]["arguments"]
        assert "--portable" in calls[3]["arguments"]
        assert (target / "verified.txt").read_text() == "synthetic package"
    # The file handle must be released even after a failed stage.
    (root / "artifacts/lan/server-setup.lock").unlink()
