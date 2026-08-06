from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
QUALITY_GATES = REPO_ROOT / "harness" / "quality_gates.json"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the full backend regression with the shared coverage gate."
    )
    parser.add_argument("--junit", type=Path)
    parser.add_argument("--coverage-xml", type=Path)
    args = parser.parse_args()

    gates = json.loads(QUALITY_GATES.read_text(encoding="utf-8"))
    minimum = int(gates["backend_line_coverage_percent"])
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--cov=app",
        "--cov-report=term-missing:skip-covered",
        f"--cov-fail-under={minimum}",
    ]
    if args.junit:
        junit = args.junit if args.junit.is_absolute() else REPO_ROOT / args.junit
        junit.parent.mkdir(parents=True, exist_ok=True)
        command.append(f"--junitxml={junit}")
    if args.coverage_xml:
        coverage_xml = (
            args.coverage_xml
            if args.coverage_xml.is_absolute()
            else REPO_ROOT / args.coverage_xml
        )
        coverage_xml.parent.mkdir(parents=True, exist_ok=True)
        command.append(f"--cov-report=xml:{coverage_xml}")
    return subprocess.run(command, cwd=BACKEND_ROOT, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
