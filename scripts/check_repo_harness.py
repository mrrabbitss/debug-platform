"""Validate repository-level engineering harness contracts.

The checks intentionally use repository files as data. They do not import the
application, access runtime databases, or inspect secrets.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_PATHS = (
    ".gitattributes",
    ".github/CODEOWNERS",
    ".github/dependabot.yml",
    ".github/pull_request_template.md",
    ".github/workflows/ci.yml",
    "AGENTS.md",
    "CAPABILITIES.md",
    "HARNESS_ENGINEERING.md",
    "harness/architecture_limits.json",
    "harness/quality_gates.json",
    "VALIDATION.md",
    "docs/README.md",
    "scripts/validate_all.bat",
    "scripts/validate_all.ps1",
    "scripts/validate_glm_chat_features.bat",
    "scripts/validate_glm_chat_features.py",
    "scripts/check_architecture.py",
    "scripts/run_backend_tests.py",
    "workflow/README.md",
    "workflow/openapi.yaml",
    "workflow/skill.yaml",
)
EXPECTED_DEPENDABOT_TARGETS = {
    ("pip", "/backend"),
    ("npm", "/frontend"),
    ("npm", "/vscode-extension"),
    ("github-actions", "/"),
    ("docker", "/"),
    ("docker", "/backend"),
    ("docker", "/frontend"),
}
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)\n]+)\)")


class HarnessChecks:
    def __init__(self) -> None:
        self.checks: list[dict[str, str]] = []
        self.failures: list[str] = []

    def check(self, name: str, condition: bool, detail: str) -> None:
        status = "PASS" if condition else "FAIL"
        self.checks.append({"name": name, "status": status, "detail": detail})
        if not condition:
            self.failures.append(f"{name}: {detail}")


def load_yaml(relative_path: str, *, base_loader: bool = False) -> object:
    loader = yaml.BaseLoader if base_loader else yaml.SafeLoader
    content = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    return yaml.load(content, Loader=loader)


def markdown_files() -> list[Path]:
    paths = list(REPO_ROOT.glob("*.md"))
    paths.extend((REPO_ROOT / "docs").rglob("*.md"))
    paths.extend((REPO_ROOT / "workflow").rglob("*.md"))
    paths.extend((REPO_ROOT / ".github").glob("*.md"))
    return sorted(set(paths))


def local_link_target(raw_target: str) -> str | None:
    target = raw_target.strip()
    if target.startswith("<") and ">" in target:
        target = target[1 : target.index(">")]
    else:
        target = target.split(maxsplit=1)[0]
    if not target or target.startswith("#"):
        return None
    lowered = target.lower()
    if lowered.startswith(("http://", "https://", "mailto:", "tel:")):
        return None
    target = target.split("#", 1)[0].split("?", 1)[0]
    return unquote(target) or None


def check_markdown_links(checks: HarnessChecks) -> None:
    broken: list[str] = []
    files = markdown_files()
    for document in files:
        content = document.read_text(encoding="utf-8")
        for match in MARKDOWN_LINK.finditer(content):
            target = local_link_target(match.group(1))
            if target is None:
                continue
            resolved = (document.parent / target).resolve()
            try:
                resolved.relative_to(REPO_ROOT)
            except ValueError:
                broken.append(
                    f"{document.relative_to(REPO_ROOT)} -> outside repository: {target}"
                )
                continue
            if not resolved.exists():
                line = content.count("\n", 0, match.start()) + 1
                broken.append(
                    f"{document.relative_to(REPO_ROOT)}:{line} -> {target}"
                )
    checks.check(
        "markdown-local-links",
        not broken,
        f"checked {len(files)} files" if not broken else "; ".join(broken),
    )


def check_document_index(checks: HarnessChecks) -> None:
    index = (REPO_ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    documents = sorted(
        path.name
        for path in (REPO_ROOT / "docs").glob("*.md")
        if path.name != "README.md"
    )
    missing = [name for name in documents if f"({name})" not in index]
    checks.check(
        "documentation-index",
        not missing,
        f"indexes {len(documents)} documents"
        if not missing
        else f"missing: {', '.join(missing)}",
    )
    capabilities = (REPO_ROOT / "CAPABILITIES.md").read_text(encoding="utf-8")
    checks.check(
        "capability-harness-cross-link",
        "HARNESS_ENGINEERING.md" in capabilities,
        "CAPABILITIES.md must link to HARNESS_ENGINEERING.md",
    )


def check_ci_contract(checks: HarnessChecks) -> None:
    workflow_path = ".github/workflows/ci.yml"
    workflow = load_yaml(workflow_path, base_loader=True)
    if not isinstance(workflow, dict):
        checks.check("ci-trigger-contract", False, "CI workflow must be a YAML mapping")
        return
    triggers = workflow.get("on")
    valid_triggers = isinstance(triggers, dict)
    if valid_triggers:
        push = triggers.get("push")
        pull_request = triggers.get("pull_request")
        dispatch = triggers.get("workflow_dispatch")
        valid_triggers = (
            isinstance(push, dict)
            and push.get("branches") == ["main"]
            and isinstance(pull_request, dict)
            and pull_request.get("branches") == ["main"]
            and dispatch is not None
        )
    checks.check(
        "ci-trigger-contract",
        valid_triggers,
        "CI must run for main pushes, PRs targeting main, and manual dispatch only",
    )
    content = (REPO_ROOT / workflow_path).read_text(encoding="utf-8")
    checks.check(
        "ci-concurrency-contract",
        "github.event.pull_request.number || github.ref" in content
        and "cancel-in-progress: true" in content,
        "CI concurrency must group PR updates and cancel superseded runs",
    )
    checks.check(
        "ci-harness-check",
        "python scripts/check_repo_harness.py" in content,
        "CI backend job must execute the repository harness checker",
    )


def check_dependabot(checks: HarnessChecks) -> None:
    config = load_yaml(".github/dependabot.yml")
    updates = config.get("updates", []) if isinstance(config, dict) else []
    targets = {
        (str(item.get("package-ecosystem")), str(item.get("directory")))
        for item in updates
        if isinstance(item, dict)
    }
    missing = sorted(EXPECTED_DEPENDABOT_TARGETS - targets)
    checks.check(
        "dependabot-coverage",
        not missing,
        "all Python, npm, Actions and Docker manifests are covered"
        if not missing
        else f"missing: {missing}",
    )


def normalize_api_path(path: str) -> str:
    prefix = "/api/v1"
    normalized = path[len(prefix) :] if path.startswith(prefix) else path
    return normalized or "/"


def check_workflow_contract(checks: HarnessChecks) -> None:
    skill = load_yaml("workflow/skill.yaml")
    openapi = load_yaml("workflow/openapi.yaml")
    if not isinstance(skill, dict) or not isinstance(openapi, dict):
        checks.check("workflow-api-contract", False, "workflow YAML must be mappings")
        return
    paths = openapi.get("paths", {})
    entrypoints = skill.get("entrypoints", {})
    missing: list[str] = []
    runtime_missing: list[str] = []
    from app.main import app

    runtime_paths = {
        normalize_api_path(path): item
        for path, item in app.openapi().get("paths", {}).items()
    }
    for name, entrypoint in entrypoints.items():
        if not isinstance(entrypoint, dict):
            missing.append(f"{name}: invalid entrypoint")
            continue
        method = str(entrypoint.get("method", "")).lower()
        path = normalize_api_path(str(entrypoint.get("path", "")))
        path_item = paths.get(path, {}) if isinstance(paths, dict) else {}
        if method not in path_item:
            missing.append(f"{name}: {method.upper()} {path}")
        runtime_path_item = runtime_paths.get(path, {})
        if method not in runtime_path_item:
            runtime_missing.append(f"{name}: {method.upper()} {path}")
    skill_version = str(skill.get("version"))
    info = openapi.get("info", {})
    api_version = str(info.get("version")) if isinstance(info, dict) else ""
    if skill_version != api_version:
        missing.append(
            f"version mismatch: skill={skill_version}, openapi={api_version}"
        )
    checks.check(
        "workflow-api-contract",
        not missing,
        f"validated {len(entrypoints)} allowlisted entrypoints"
        if not missing
        else "; ".join(missing),
    )
    checks.check(
        "workflow-runtime-openapi",
        not runtime_missing,
        f"matched {len(entrypoints)} allowlisted operations to FastAPI OpenAPI"
        if not runtime_missing
        else "; ".join(runtime_missing),
    )
    constraints = " ".join(str(item) for item in skill.get("constraints", [])).lower()
    safeguards = {
        "untrusted-input": "untrusted" in constraints,
        "evidence-gate": "evidence" in constraints,
        "draft-human-gate": "draft" in constraints
        and ("human" in constraints or "approval" in constraints),
        "read-only-default": "read-only" in constraints,
        "write-approval": "write" in constraints and "approval" in constraints,
        "bounded-diagnosis": "budget" in constraints and "stagnation" in constraints,
    }
    absent = [name for name, present in safeguards.items() if not present]
    checks.check(
        "workflow-safety-contract",
        not absent,
        "untrusted input, evidence, DRAFT, read-only, write-approval and diagnostic budget gates are declared"
        if not absent
        else f"missing: {', '.join(absent)}",
    )


def main() -> int:
    checks = HarnessChecks()
    missing = [path for path in REQUIRED_PATHS if not (REPO_ROOT / path).exists()]
    checks.check(
        "required-harness-files",
        not missing,
        f"all {len(REQUIRED_PATHS)} files present"
        if not missing
        else f"missing: {', '.join(missing)}",
    )

    agents = REPO_ROOT / "AGENTS.md"
    line_count = len(agents.read_text(encoding="utf-8").splitlines()) if agents.exists() else 0
    checks.check(
        "agent-guide-size",
        0 < line_count <= 120,
        f"AGENTS.md has {line_count} lines (limit: 120)",
    )

    check_markdown_links(checks)
    check_document_index(checks)
    check_ci_contract(checks)
    check_dependabot(checks)
    check_workflow_contract(checks)
    from check_architecture import check_architecture

    architecture = check_architecture()
    checks.check(
        "architecture-boundaries",
        architecture["status"] == "PASS",
        (
            f"checked {architecture['checked_python_files']} Python and "
            f"{architecture['checked_vue_files']} Vue files"
            if architecture["status"] == "PASS"
            else "; ".join(architecture["failures"])
        ),
    )

    summary = {
        "status": "PASS" if not checks.failures else "FAIL",
        "repository": REPO_ROOT.name,
        "check_count": len(checks.checks),
        "checks": checks.checks,
        "failures": checks.failures,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not checks.failures else 1


if __name__ == "__main__":
    sys.exit(main())
