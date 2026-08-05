import json
import subprocess
from pathlib import Path

from app.core.config import PROJECT_ROOT


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
