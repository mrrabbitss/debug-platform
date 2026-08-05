import json
import os
import subprocess
from pathlib import Path

import pytest

from app.core.config import PROJECT_ROOT


def test_agent_workspace_script_declares_isolated_resources() -> None:
    script = (PROJECT_ROOT / "scripts" / "new_agent_workspace.ps1").read_text(
        encoding="utf-8-sig"
    )
    for required_contract in (
        '"codex/task-$Slug"',
        '".agent-runtime\\$Slug"',
        "backend_port",
        "frontend_port",
        "fake_model_port",
        "DATABASE_URL",
        "STORAGE_ROOT",
        "AGENT_LOG_ROOT",
        "git -C $RepoRoot worktree add",
    ):
        assert required_contract in script


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell worktree helper")
def test_agent_workspace_plan_has_isolated_runtime_resources() -> None:
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(PROJECT_ROOT / "scripts" / "new_agent_workspace.ps1"),
            "-TaskName",
            "golden-eval",
            "-PlanOnly",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8-sig",
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    plan = json.loads(result.stdout)
    assert plan["branch"] == "codex/task-golden-eval"
    assert plan["backend_port"] != plan["frontend_port"]
    assert plan["database"].endswith("agent.db")
    assert ".agent-runtime" in plan["storage"]
    assert ".agent-runtime" in plan["logs"]
    assert not Path(plan["worktree"]).exists()
