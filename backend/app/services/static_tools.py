import os
import shutil
import subprocess
from pathlib import Path

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models import Repository
from app.services.jobs import JobContext
from app.services.storage import storage


ALLOWED_TOOLS = {"cppcheck", "clang-tidy"}


def _run(command: list[str], cwd: Path) -> dict:
    timeout = get_settings().tool_timeout_seconds
    completed = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8"},
        check=False,
    )
    return {
        "command": command,
        "return_code": completed.returncode,
        "stdout": completed.stdout[-200000:],
        "stderr": completed.stderr[-200000:],
    }


def static_analysis_job(ctx: JobContext, repository_id: str, tools: list[str]) -> dict:
    with SessionLocal() as db:
        repository = db.get(Repository, repository_id)
        if not repository:
            raise ValueError("Repository not found")
        root = storage.resolve_path(repository.root_path)
    results = []
    selected = [tool for tool in tools if tool in ALLOWED_TOOLS]
    for idx, tool in enumerate(selected):
        binary = shutil.which(tool)
        if not binary:
            results.append({"tool": tool, "available": False, "message": f"{tool} is not installed in the backend environment"})
            continue
        ctx.update(10 + int(75 * idx / max(len(selected), 1)), f"Running {tool}")
        if tool == "cppcheck":
            command = [binary, "--enable=warning,style,performance,portability", "--inconclusive", "--xml", "--xml-version=2", "."]
        else:
            compile_db = root / "compile_commands.json"
            if not compile_db.exists():
                results.append({"tool": tool, "available": True, "skipped": True, "message": "compile_commands.json is required for clang-tidy"})
                continue
            files = [str(path.relative_to(root)) for path in root.rglob("*.c")][:100]
            if not files:
                results.append({"tool": tool, "available": True, "skipped": True, "message": "No C source files found"})
                continue
            command = [binary, *files, "-p", str(root)]
        try:
            result = _run(command, root)
            result.update({"tool": tool, "available": True})
            results.append(result)
        except subprocess.TimeoutExpired:
            results.append({"tool": tool, "available": True, "timeout": True, "message": "Tool execution timed out"})
    return {"repository_id": repository_id, "results": results}
