import sys
from pathlib import Path

import pytest

from app.services.process_sandbox import SandboxTimeout, run_sandboxed


def test_process_sandbox_enforces_wall_clock_timeout(tmp_path: Path) -> None:
    with pytest.raises(SandboxTimeout):
        run_sandboxed(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            cwd=tmp_path,
            timeout_seconds=1,
            memory_bytes=256 * 1024 * 1024,
            cpu_seconds=3,
        )


def test_process_sandbox_enforces_memory_limit(tmp_path: Path) -> None:
    result = run_sandboxed(
        [
            sys.executable,
            "-c",
            "value = bytearray(512 * 1024 * 1024); print(len(value))",
        ],
        cwd=tmp_path,
        timeout_seconds=10,
        memory_bytes=128 * 1024 * 1024,
        cpu_seconds=5,
    )

    assert result.returncode != 0
    assert "536870912" not in result.stdout
