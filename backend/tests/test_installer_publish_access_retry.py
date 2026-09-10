"""New cases for the company log's staging-directory rename denial."""
import os
from pathlib import Path
import subprocess
import uuid

import pytest

from tests.test_installer_components import _bundle, selection
from tests.test_installer_atomic_upgrade_failures import _old_tree, _run_wrapper

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell rename semantics")
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("release_after_ms,success", [(1200, True), (25000, False)])
def test_new_staging_lock_retries_or_restores_old_application(tmp_path, release_after_ms, success):
    source = _bundle(tmp_path / "source", real_python=True)
    target, data, original = _old_tree(tmp_path)
    script = r'''
Add-Type -TypeDefinition @'
using System.IO;
using System.Threading;
public static class SyntheticInstallLock {
    public static void Hold(string path, int milliseconds) {
        var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
        var thread = new Thread(() => { Thread.Sleep(milliseconds); stream.Dispose(); });
        thread.IsBackground = true;
        thread.Start();
    }
}
'@
function Write-Host {
    param([object]$Object)
    if ([string]$Object -eq '[INFO] Publishing the verified application directory...') {
        $candidate = Get-ChildItem -LiteralPath (Split-Path -Parent $InstallRoot) -Directory |
            Where-Object { $_.Name.StartsWith('GWAPDebugPlatform.installing-') }
        [SyntheticInstallLock]::Hold((Join-Path $candidate.FullName 'web\index.html'), MILLISECONDS)
    }
    Microsoft.PowerShell.Utility\Write-Host $Object
}
'''.replace("MILLISECONDS", str(release_after_ms))
    result = _run_wrapper(source, target, script)
    assert "retrying the atomic rename" in result.stdout, result.stdout + result.stderr
    assert data.read_bytes() == b"approved user data stays untouched"
    if success:
        assert result.returncode == 0, result.stdout + result.stderr
        assert "Publish verified application succeeded after" in result.stdout
        selection._verified_entries(target)
    else:
        assert result.returncode != 0
        assert "Publish verified application failed after" in result.stderr
        assert "Windows error" in result.stderr
        assert "complete previous application directory was restored" in result.stdout
        assert {p.name: p.read_bytes() for p in target.iterdir()} == original


def test_real_powershell_exception_summary_uses_cause_not_error_identifier(tmp_path):
    payload = tmp_path / "synthetic payload"
    payload.mkdir()
    (payload / "install_server_release.ps1").write_text(
        "throw [System.IO.IOException]::new('Synthetic publication denied; token=placeholder')",
        encoding="utf-8-sig")
    log = Path(os.environ["LOCALAPPDATA"]) / "GWAPDebugServer/install-logs" / ("test-publish-" + uuid.uuid4().hex + ".log")
    summary = Path(str(log) + ".summary.txt")
    try:
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(ROOT / "deploy/windows-server/publish_server_payload.ps1"),
            "-PayloadRoot", str(payload), "-InstallRoot", str(tmp_path / "app"),
            "-FailureLog", str(log), "-FailureSummary", str(summary)], capture_output=True, timeout=30)
        assert result.returncode == 1
        text = summary.read_text(encoding="utf-8")
        assert "Synthetic publication denied" in text
        assert "FullyQualifiedErrorId" not in text
        assert "GWAP_INSTALL_LOCATION" in log.read_text(encoding="utf-8")
    finally:
        log.unlink(missing_ok=True)
        summary.unlink(missing_ok=True)
