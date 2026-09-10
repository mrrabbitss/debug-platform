import os
from pathlib import Path
import subprocess

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_server_installer_writes_sanitized_publisher_diagnostics_to_user_log():
    installer = (PROJECT_ROOT / "deploy/windows-server/ServerInstaller.iss").read_text(
        encoding="utf-8"
    )
    wrapper = (
        PROJECT_ROOT / "deploy/windows-server/publish_server_payload.ps1"
    ).read_text(encoding="utf-8")

    assert "publish_server_payload.ps1" in installer
    assert "{localappdata}\\GWAPDebugServer\\install-logs" in installer
    assert "LoadStringFromFile(FailureSummary, FailureReasonUtf8)" in installer
    assert "UTF8Decode(FailureReasonUtf8)" in installer
    assert "if FileExists(FailureLog) then begin" in installer
    assert "The publisher diagnostic could not be saved." in installer
    assert "Publisher output was saved for this Windows account at:" in installer
    assert "The prior application state could not be verified." in installer
    assert "The previous program and business data were preserved" not in installer
    assert "publisher stdout (sanitized)" in wrapper
    assert "publisher stderr (sanitized)" in wrapper
    assert "Protect-InstallOutput" in wrapper
    assert "RedirectStandardOutput = $true" in wrapper
    assert "RedirectStandardError = $true" in wrapper
    assert "ReadToEndAsync()" in wrapper
    assert ".stdout.tmp" not in wrapper
    assert ".stderr.tmp" not in wrapper


def test_server_builder_copies_the_payload_diagnostic_wrapper():
    builder = (PROJECT_ROOT / "scripts/build_windows_server.py").read_text(
        encoding="utf-8"
    )

    assert 'SERVER_INSTALLER_HELPERS = ("publish_server_payload.ps1",)' in builder
    assert "for filename in SERVER_INSTALLER_HELPERS:" in builder
    assert 'target / filename' in builder


@pytest.mark.skipif(os.name != "nt", reason="requires Windows PowerShell 5.1")
def test_server_payload_wrapper_retains_redacted_streams_and_a_safe_reason(tmpdir):
    windows_root = Path(os.environ["WINDIR"])
    powershell = windows_root / "System32/WindowsPowerShell/v1.0/powershell.exe"
    if not powershell.is_file():
        pytest.skip("Windows PowerShell 5.1 is unavailable")

    temporary_root = Path(str(tmpdir))
    payload = temporary_root / "payload with spaces"
    payload.mkdir()
    installer_script = payload / "install_local.ps1"
    log_directory = Path(os.environ["LOCALAPPDATA"]) / "GWAPDebugServer/install-logs"
    failure_log = log_directory / f"pytest-{temporary_root.name}.log"
    failure_summary = Path(f"{failure_log}.summary.txt")
    success_log = log_directory / f"pytest-{temporary_root.name}.success.log"
    success_summary = Path(f"{success_log}.summary.txt")
    wrapper = PROJECT_ROOT / "deploy/windows-server/publish_server_payload.ps1"

    def run_wrapper(log, summary):
        return subprocess.run(
            [
                str(powershell),
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(wrapper),
                "-PayloadRoot",
                str(payload),
                "-InstallRoot",
                str(temporary_root / "app with spaces"),
                "-FailureLog",
                str(log),
                "-FailureSummary",
                str(summary),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

    try:
        installer_script.write_text(
            "\n".join(
                (
                    "Write-Output 'payload completed'",
                    "[Console]::Error.WriteLine('native warning')",
                    "exit 0",
                )
            ),
            encoding="utf-8",
        )
        assert run_wrapper(success_log, success_summary).returncode == 0
        assert not success_log.exists()
        assert not success_summary.exists()

        installer_script.write_text(
            "\n".join(
                (
                    "Write-Output 'payload stdout'",
                    "[Console]::Error.WriteLine('token=must-not-appear')",
                    "[Console]::Error.WriteLine('native warning')",
                    "[Console]::Error.WriteLine('Move-Item : locked application file')",
                    "exit 23",
                )
            ),
            encoding="utf-8",
        )
        result = run_wrapper(failure_log, failure_summary)
        assert result.returncode == 23
        log = failure_log.read_text(encoding="utf-8")
        assert "payload stdout" in log
        assert "native warning" in log
        assert "Move-Item : locked application file" in log
        assert "must-not-appear" not in log
        assert "token=[REDACTED]" in log
        assert failure_summary.read_text(encoding="utf-8-sig") == "Move-Item : locked application file"
    finally:
        for path in (failure_log, failure_summary, success_log, success_summary):
            if path.exists():
                path.unlink()
