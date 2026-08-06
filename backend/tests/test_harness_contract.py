import json
import subprocess
import sys

from app.core.config import PROJECT_ROOT


def test_repository_harness_contracts_pass() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/check_repo_harness.py"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads(result.stdout)
    assert summary["status"] == "PASS"
    assert summary["failures"] == []
    assert summary["check_count"] >= 10
