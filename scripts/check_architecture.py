"""Enforce architecture boundaries and ratcheted context-size limits."""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "harness" / "architecture_limits.json"
IGNORED_PYTHON_PARTS = {"migrations", "seed_knowledge", "__pycache__"}


def _module_name(path: Path) -> str:
    relative = path.relative_to(REPO_ROOT / "backend")
    return ".".join(relative.with_suffix("").parts)


def _complexity(node: ast.AST) -> int:
    score = 1
    for child in ast.walk(node):
        if isinstance(child, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.IfExp)):
            score += 1
        elif isinstance(child, ast.BoolOp):
            score += max(1, len(child.values) - 1)
        elif isinstance(child, ast.Try):
            score += len(child.handlers) + bool(child.orelse) + bool(child.finalbody)
        elif isinstance(child, ast.Match):
            score += len(child.cases)
        elif isinstance(child, ast.comprehension):
            score += 1 + len(child.ifs)
    return score


def _imports(tree: ast.AST) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def _python_files() -> list[Path]:
    return sorted(
        path
        for path in (REPO_ROOT / "backend" / "app").rglob("*.py")
        if not set(path.relative_to(REPO_ROOT).parts).intersection(IGNORED_PYTHON_PARTS)
    )


def check_architecture() -> dict[str, Any]:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    failures: list[str] = []
    measurements: dict[str, Any] = {
        "files": {},
        "max_complexity": {"value": 0, "symbol": ""},
    }
    explicit_limits = config["file_line_limits"]
    default_python = int(config["default_python_file_lines"])
    default_vue = int(config["default_vue_file_lines"])
    complexity_limit = int(config["default_function_complexity"])

    for required in config["required_modules"]:
        if not (REPO_ROOT / required).is_file():
            failures.append(f"required module is missing: {required}")

    for path in _python_files():
        relative = path.relative_to(REPO_ROOT).as_posix()
        content = path.read_text(encoding="utf-8")
        line_count = len(content.splitlines())
        limit = int(explicit_limits.get(relative, default_python))
        measurements["files"][relative] = {"lines": line_count, "limit": limit}
        if line_count > limit:
            failures.append(f"{relative} has {line_count} lines (limit {limit})")
        try:
            tree = ast.parse(content, filename=relative)
        except SyntaxError as exc:
            failures.append(f"{relative} cannot be parsed: {exc}")
            continue
        module = _module_name(path)
        for source_prefix, forbidden_prefixes in config["forbidden_imports"].items():
            if module == source_prefix or module.startswith(f"{source_prefix}."):
                for imported in _imports(tree):
                    if any(
                        imported == prefix or imported.startswith(f"{prefix}.")
                        for prefix in forbidden_prefixes
                    ):
                        failures.append(
                            f"{module} imports forbidden higher layer {imported}"
                        )
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            score = _complexity(node)
            symbol = f"{module}.{node.name}"
            if score > measurements["max_complexity"]["value"]:
                measurements["max_complexity"] = {"value": score, "symbol": symbol}
            if score > complexity_limit:
                failures.append(
                    f"{symbol} has complexity {score} (limit {complexity_limit})"
                )

    for path in sorted((REPO_ROOT / "frontend" / "src").rglob("*.vue")):
        relative = path.relative_to(REPO_ROOT).as_posix()
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        limit = int(explicit_limits.get(relative, default_vue))
        measurements["files"][relative] = {"lines": line_count, "limit": limit}
        if line_count > limit:
            failures.append(f"{relative} has {line_count} lines (limit {limit})")

    return {
        "status": "PASS" if not failures else "FAIL",
        "config": CONFIG_PATH.relative_to(REPO_ROOT).as_posix(),
        "checked_python_files": len(_python_files()),
        "checked_vue_files": len(list((REPO_ROOT / "frontend" / "src").rglob("*.vue"))),
        "measurements": measurements,
        "failures": failures,
    }


def main() -> int:
    result = check_architecture()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
