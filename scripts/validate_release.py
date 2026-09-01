#!/usr/bin/env python3
"""Validate the Skill-only release envelope without third-party packages."""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
EXPECTED_VERSION = "0.6.0"
EXPECTED_RUNTIME_HASH = "2c391be3b8f284727836d9607a8863f62e200fdd57c0ceaeede6ac91d3f1291c"
EXPECTED_SOURCE_MAPPING_SCHEMA = "gw-ap-debug-source-mapping/v2"
EXPECTED_VALIDATION_TEST_COUNT = 82
EXPECTED_RELEASE_VALIDATION = [
    "python -B scripts/check_provenance.py",
    "python -B scripts/validate_release.py",
    "python -B scripts/ci_deterministic_smoke.py --check-only",
]
EXPECTED_SOURCE_MAPPING_VALIDATION = {
    "command": "python -B scripts/check_source_mapping.py --source-repo <debugplatform-main-checkout>",
    "requires_source_commit": "181dae7b26863accd02e8206895d3cfb670739ac",
    "ci_command": "python -B scripts/check_source_mapping.py --source-repo .",
}
EXPECTED_CI_VALIDATION = ["python -B scripts/ci_deterministic_smoke.py"]
EXPECTED_LOCAL_PATCHES = {
    "external DATA_ROOT support",
    "external method root with immutable generations, case bindings, and runtime probe",
    "dependency-only bootstrap without editable source writes",
    "deterministic Triage when backend model egress is not approved",
    "Mako 1.4.1 non-yanked dependency lock",
}


def frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        return {}
    try:
        body = text.split("---\n", 2)[1]
    except IndexError:
        return {}
    result: dict[str, str] = {}
    in_metadata = False
    for line in body.splitlines():
        if line == "metadata:":
            in_metadata = True
            continue
        match = re.match(r"^(\s*)([A-Za-z_][\w-]*):\s*[\"']?(.*?)[\"']?\s*$", line)
        if not match:
            continue
        indent, key, value = match.groups()
        if in_metadata and indent:
            result[f"metadata.{key}"] = value
        elif not indent:
            in_metadata = False
            result[key] = value
    return result


def local_markdown_links(path: Path) -> list[Path]:
    missing: list[Path] = []
    for raw_target in LINK_RE.findall(path.read_text(encoding="utf-8")):
        target = raw_target.strip().strip("<>").split("#", 1)[0].split("?", 1)[0]
        if not target or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target):
            continue
        resolved = (path.parent / unquote(target)).resolve()
        if not resolved.exists():
            missing.append(resolved)
    return missing


def optional_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8") if path.is_file() else ""
    except (OSError, UnicodeError):
        return ""


def main() -> int:
    checks: list[str] = []
    failures: list[str] = []

    def require(condition: bool, success: str, failure: str) -> None:
        if condition:
            checks.append(success)
        else:
            failures.append(failure)

    skill_path = ROOT / "SKILL.md"
    manifest_path = ROOT / "skill-manifest.json"
    require(skill_path.is_file(), "SKILL.md exists", "SKILL.md is missing")
    require(manifest_path.is_file(), "skill-manifest.json exists", "skill-manifest.json is missing")
    if not skill_path.is_file() or not manifest_path.is_file():
        print(json.dumps({"ok": False, "checks": checks, "failures": failures}, indent=2))
        return 2

    skill_text = skill_path.read_text(encoding="utf-8")
    metadata = frontmatter(skill_text)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    version = str(manifest.get("version", ""))
    description = metadata.get("description", "")

    require(metadata.get("name") == "gw-ap-debug", "Skill name is canonical", "Skill name must be gw-ap-debug")
    require(
        len(description) >= 100 and "GW/AP" in description and "generic" in description,
        "Skill description is discriminating",
        "Skill description must clearly distinguish GW/AP use from generic debugging",
    )
    require(bool(re.fullmatch(r"\d+\.\d+\.\d+", version)), "Manifest version is semantic", "Manifest version is not x.y.z")
    require(version == EXPECTED_VERSION, "Release version is 0.6.0", f"Manifest version must be {EXPECTED_VERSION}")
    require(metadata.get("metadata.version") == version, "Skill and manifest versions match", "SKILL.md and manifest versions differ")
    require(
        metadata.get("metadata.primary_host") == "Claude Code CLI on Windows 11",
        "Claude Code on Windows 11 is the recorded primary host",
        "Primary host metadata must be Claude Code CLI on Windows 11",
    )
    require(
        "Windows 11 primary" in metadata.get("metadata.compatibility", "") and "Python >=3.11,<3.15" in metadata.get("metadata.compatibility", ""),
        "Portable compatibility is explicit",
        "Compatibility metadata must record Windows priority and Python range",
    )
    require(manifest.get("name") == "gw-ap-debug", "Manifest name is canonical", "Manifest name must be gw-ap-debug")
    require(
        manifest.get("host_cli", {}).get("primary") == "Claude Code CLI on Windows 11",
        "Manifest host priority matches the Skill",
        "Manifest primary host differs from the Skill metadata",
    )
    require(manifest.get("python") == ">=3.11,<3.15", "Python compatibility is explicit", "Python compatibility drifted")
    require(
        manifest.get("distribution_status") == "PUBLIC_RELEASE_WITH_BUNDLED_METHODS_BY_OWNER_DIRECTION",
        "Distribution decision is preserved",
        "Distribution decision is missing or changed",
    )
    source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
    runtime = manifest.get("runtime") if isinstance(manifest.get("runtime"), dict) else {}
    source_commit = str(source.get("commit") or "")
    runtime_hash = str(runtime.get("tree_sha256") or "")
    runtime_count = runtime.get("file_count")
    local_patches = runtime.get("local_patches")
    require(
        source.get("repository") == "debugplatform"
        and source.get("branch") == "main"
        and bool(re.fullmatch(r"[0-9a-f]{40}", source_commit)),
        "Source snapshot identity is complete",
        "Manifest source must name debugplatform/main and a full SHA-1 commit",
    )
    require(
        bool(re.fullmatch(r"[0-9a-f]{64}", runtime_hash))
        and runtime_hash == EXPECTED_RUNTIME_HASH
        and isinstance(runtime_count, int)
        and not isinstance(runtime_count, bool)
        and runtime_count > 0,
        "Runtime tree identity is complete and pinned",
        "Manifest runtime must contain the reviewed SHA-256 tree hash and positive file count",
    )
    require(
        isinstance(local_patches, list)
        and all(isinstance(item, str) and item for item in local_patches)
        and len(local_patches) == len(set(local_patches))
        and set(local_patches) == EXPECTED_LOCAL_PATCHES,
        "Runtime portability patches are explicitly allowlisted",
        "Manifest runtime.local_patches is incomplete, duplicated, or unexpected",
    )
    require(
        manifest.get("release_validation") == EXPECTED_RELEASE_VALIDATION,
        "Fresh-clone release validation commands are complete",
        "Manifest release_validation must contain only checks that can pass in a skillonly single-branch clone",
    )
    require(
        manifest.get("source_mapping_validation") == EXPECTED_SOURCE_MAPPING_VALIDATION,
        "Maintainer source mapping is declared with its source prerequisite",
        "Manifest source_mapping_validation must name the main-checkout prerequisite and CI command",
    )
    require(
        manifest.get("ci_validation") == EXPECTED_CI_VALIDATION,
        "Full deterministic CI smoke is declared",
        "Manifest ci_validation must declare the full deterministic smoke command",
    )

    required_files = [
        "DEPLOYMENT.md",
        "VALIDATION.md",
        ".gitattributes",
        ".github/workflows/skillonly-validation.yml",
        "agents/openai.yaml",
        "scripts/debug_platform_skill.py",
        "scripts/check_provenance.py",
        "scripts/check_source_mapping.py",
        "scripts/ci_deterministic_smoke.py",
        "scripts/python_runtime.ps1",
        "scripts/gw_ap_debug.ps1",
        "scripts/gw_ap_debug.sh",
        "scripts/setup_user_skill.ps1",
        "scripts/setup_user_skill.sh",
        "references/claude-code.md",
        "references/installation.md",
        "references/host-agent-mode.md",
        "references/composable-knowledge.md",
        "references/capability-scope.md",
        "references/security-and-distribution.md",
        "tests/test_ci_deterministic_smoke.py",
        "tests/test_source_mapping.py",
    ]
    for relative in required_files:
        require((ROOT / relative).is_file(), f"Required file exists: {relative}", f"Required file is missing: {relative}")

    attrs = (ROOT / ".gitattributes").read_text(encoding="utf-8") if (ROOT / ".gitattributes").is_file() else ""
    require("* -text" in attrs, "Runtime byte-preservation rule exists", ".gitattributes must preserve runtime bytes")
    require("[DEPLOYMENT.md](DEPLOYMENT.md)" in skill_text, "SKILL.md routes to deployment guide", "SKILL.md must link DEPLOYMENT.md")
    require("[claude-code.md](references/claude-code.md)" in skill_text, "SKILL.md routes to Claude Code guidance", "SKILL.md must link Claude Code guidance")

    openai_yaml = (ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8") if (ROOT / "agents" / "openai.yaml").is_file() else ""
    require("$gw-ap-debug" in openai_yaml, "Codex UI prompt names the Skill", "agents/openai.yaml must name $gw-ap-debug")
    require("allow_implicit_invocation: true" in openai_yaml, "Implicit invocation remains enabled", "Codex implicit invocation policy drifted")

    entrypoint_text = optional_text(ROOT / "scripts" / "debug_platform_skill.py")
    windows_launcher_text = optional_text(ROOT / "scripts" / "gw_ap_debug.ps1")
    windows_setup_text = optional_text(ROOT / "scripts" / "setup_user_skill.ps1")
    workflow_text = optional_text(ROOT / ".github" / "workflows" / "skillonly-validation.yml")
    source_mapping_text = optional_text(ROOT / "scripts" / "check_source_mapping.py")
    validation_text = optional_text(ROOT / "VALIDATION.md")
    method_service_text = optional_text(ROOT / "runtime" / "backend" / "app" / "services" / "diagnostic_methods.py")
    system_api_text = optional_text(ROOT / "runtime" / "backend" / "app" / "api" / "system.py")
    require(
        f'SKILL_VERSION = "{version}"' in entrypoint_text,
        "Runtime bundle metadata uses the release version",
        "debug_platform_skill.py SKILL_VERSION differs from the manifest",
    )
    require(
        "gw_ap_debug.ps1" in entrypoint_text and "gw_ap_debug.sh" in entrypoint_text,
        "Generated host instructions route through portable launchers",
        "Host continuation generation must reference both platform launchers",
    )
    require(
        "import-skill-methods" in entrypoint_text
        and "--diagnostic-skill" in entrypoint_text
        and "--diagnostic-skill-scope" in entrypoint_text
        and "--max-host-method-tokens" in entrypoint_text
        and "MARKDOWN_ONLY_NO_IMPORTED_CODE_EXECUTION" in entrypoint_text,
        "Composable diagnostic Skill importer is present",
        "Composable diagnostic Skill importer or its no-execution policy is missing",
    )
    require(
        "[composable-knowledge.md](references/composable-knowledge.md)" in skill_text,
        "SKILL.md routes to composable diagnostic knowledge guidance",
        "SKILL.md must link composable diagnostic knowledge guidance",
    )
    require(
        "$CliPassthrough" in windows_launcher_text and "$SkillArguments" not in windows_launcher_text,
        "Windows launcher passes --skill without PowerShell parameter-prefix collision",
        "Windows launcher passthrough name collides with the public --skill option",
    )
    require(
        "uses the canonical checkout directly" in windows_setup_text
        and "New-Item -ItemType Junction" in windows_setup_text,
        "Windows setup supports a real Claude directory plus shared junctions",
        "Windows setup must recognize a canonical discovery directory and register other clients safely",
    )
    require(
        "fetch-depth: 0" in workflow_text
        and "check_source_mapping.py --source-repo ." in workflow_text,
        "CI verifies runtime files against the recorded source commit",
        "CI must fetch source history and run check_source_mapping.py",
    )
    require(
        "ci_deterministic_smoke.py --check-only" in workflow_text
        and workflow_text.count("ci_deterministic_smoke.py") >= 2
        and "windows-latest' && matrix.python == '3.14'" in workflow_text,
        "CI separates lightweight smoke contract checks from one full Windows smoke",
        "CI must run --check-only broadly and the full smoke once on Windows Python 3.14",
    )
    require(
        "backend/app/api/system.py" in source_mapping_text
        and "backend/app/services/diagnostic_methods.py" in source_mapping_text
        and "immutable generations, case bindings, and runtime probe" in source_mapping_text,
        "Generation and case-binding runtime patches are source-mapped",
        "Source mapping must declare both files in the diagnostic-method runtime patch",
    )
    require(
        f'SCHEMA = "{EXPECTED_SOURCE_MAPPING_SCHEMA}"' in source_mapping_text
        and "EXPECTED_PORTABILITY_PATCH_FINGERPRINTS" in source_mapping_text
        and all(
            field in source_mapping_text
            for field in (
                "source_normalized_sha256",
                "runtime_normalized_sha256",
                "normalized_diff_sha256",
            )
        ),
        "Source mapping pins both normalized endpoints and the canonical diff",
        "Source mapping must use schema v2 and exact source/runtime/diff fingerprints",
    )
    require(
        "_METHOD_GENERATION_SCHEMA" in method_service_text
        and "resolve_case_method_root" in method_service_text
        and '"case_binding_supported": True' in system_api_text,
        "Manifest diagnostic-method patch matches the bundled runtime",
        "Bundled runtime is missing generation, case-binding, or capability-probe behavior",
    )
    require(
        "snapshot_active_persistent_methods" in entrypoint_text
        and "finish_case_method_binding" in entrypoint_text
        and "renew_case_method_generation" in entrypoint_text
        and "verify_backend_method_runtime" in entrypoint_text
        and "max_host_method_tokens=host_budget" in entrypoint_text
        and "case_method_job_timeout" in entrypoint_text
        and "MAX_CASE_METHOD_BINDING_TTL_SECONDS" in entrypoint_text
        and "_method_generation_identity" in entrypoint_text
        and "_method_generation_registry_snapshot" in entrypoint_text
        and "method_job_failure_is_uncertain" in entrypoint_text
        and '"binding_required"' in entrypoint_text,
        "Case execution pins methods, atomically enforces budget, and preserves bounded uncertain leases",
        "Entrypoint must atomically budget method snapshots, verify runtime support, and safely finish or renew bounded case bindings",
    )
    require(
        "--diagnostic-skill-fault-tree" in entrypoint_text
        and "--diagnostic-skill-log-analysis" in entrypoint_text,
        "Explicit diagnostic role-path options are exposed",
        "Entrypoint must expose explicit fault-tree and log-analysis role paths",
    )
    require(
        "import msvcrt" in entrypoint_text
        and "msvcrt.locking" in entrypoint_text
        and "fcntl.flock" in entrypoint_text,
        "Method writers use kernel-backed cross-platform locking",
        "Entrypoint must use kernel-backed locking on Windows and POSIX",
    )
    try:
        discovered_test_count = unittest.defaultTestLoader.discover(
            str(ROOT / "tests"), pattern="test*.py",
        ).countTestCases()
    except Exception as exc:  # pragma: no cover - release-envelope failure path
        discovered_test_count = -1
        failures.append(f"Could not discover release tests: {exc}")
    require(
        discovered_test_count == EXPECTED_VALIDATION_TEST_COUNT,
        f"Release test inventory contains {EXPECTED_VALIDATION_TEST_COUNT} cases",
        "Actual unittest discovery count differs from the release record: "
        f"{discovered_test_count} != {EXPECTED_VALIDATION_TEST_COUNT}",
    )
    require(
        "## Current v0.6.0 release candidate" in validation_text
        and f"{EXPECTED_VALIDATION_TEST_COUNT} passed" in validation_text
        and EXPECTED_RUNTIME_HASH in validation_text
        and EXPECTED_SOURCE_MAPPING_SCHEMA in validation_text
        and "real Windows 11 / Python 3.14 full smoke passed" in validation_text,
        "Validation record describes the current reviewed candidate",
        "VALIDATION.md must record v0.6.0, the pinned runtime hash, the current test count, source mapping v2, and the full Windows smoke",
    )

    docs = [skill_path, ROOT / "DEPLOYMENT.md"] + sorted((ROOT / "references").glob("*.md"))
    missing_links: list[str] = []
    for document in docs:
        if document.is_file():
            missing_links.extend(str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path) for path in local_markdown_links(document))
    require(not missing_links, "All local documentation links resolve", f"Broken local links: {', '.join(missing_links)}")

    result = {"ok": not failures, "version": version, "checks": checks, "failures": failures}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
