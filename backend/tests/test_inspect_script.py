import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell diagnostic tool")
def test_inspect_script_reports_sparse_nul_text_as_supported(tmp_path: Path):
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "scripts" / "inspect_log_file.ps1"
    log_path = tmp_path / "device_collectDebuginfo"
    report_path = tmp_path / "report.txt"
    log_path.write_bytes(
        b"Wait for collection\n"
        + b"A" * 2048
        + b"\x00"
        + b"\nNOTICE 2026-03-02 03:29:17.483[90][DC]ready\n"
    )

    result = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-LogPath",
            str(log_path),
            "-OutputPath",
            str(report_path),
            "-SkipLineCount",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = report_path.read_text(encoding="utf-8-sig")
    assert "EncodingHint             : 8-bit text with sparse NUL bytes" in report
    assert "AppTextContentProbe      : PASS" in report
