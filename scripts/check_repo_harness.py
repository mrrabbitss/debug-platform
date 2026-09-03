"""Validate repository-level engineering harness contracts.

The checks intentionally use repository files as data. They do not import the
application, access runtime databases, or inspect secrets.
"""

from __future__ import annotations

import json
import re
import subprocess
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
    ".github/workflows/windows-portable.yml",
    ".github/workflows/windows-gguf-installer.yml",
    "AGENTS.md",
    "CAPABILITIES.md",
    "HARNESS_ENGINEERING.md",
    "harness/architecture_limits.json",
    "harness/quality_gates.json",
    "VALIDATION.md",
    "docs/README.md",
    "docs/windows-portable-deployment.md",
    "backend/app/services/model_downloads.py",
    "backend/app/services/model_download_storage.py",
    "backend/app/api/model_downloads.py",
    "backend/app/api/route_registry.py",
    "backend/tests/test_model_downloads.py",
    "deploy/windows-portable/portable_launcher.py",
    "deploy/windows-portable/start.bat",
    "deploy/windows-installer/DebugPlatform.iss",
    "deploy/windows-installer/Install.bat",
    "deploy/windows-installer/install_local.ps1",
    "scripts/model-runtime/model-assets.json",
    "scripts/model-runtime/model-assets.schema.json",
    "scripts/model-runtime/prepare_assets.py",
    "scripts/model-runtime/smoke_runtime.py",
    "scripts/model-runtime/validate_assets.py",
    "scripts/build_windows_gguf_installer.bat",
    "scripts/build_windows_gguf_installer.ps1",
    "scripts/test_portable_gguf_launcher.py",
    "scripts/build_windows_portable.bat",
    "scripts/build_windows_portable.ps1",
    "scripts/validate_all.bat",
    "scripts/validate_all.ps1",
    "start_codeagent.bat",
    "scripts/start_codeagent.ps1",
    "scripts/codeagent_launcher_support.ps1",
    "scripts/codeagent_launcher_http.ps1",
    "backend/tests/test_codeagent_launcher.py",
    "scripts/validate_glm_chat_features.bat",
    "scripts/validate_glm_chat_features.py",
    "scripts/check_architecture.py",
    "scripts/run_backend_tests.py",
    "scripts/verify_windows_portable.bat",
    "scripts/verify_windows_portable.ps1",
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
RETIRED_MODEL_INSTALLERS = (
    "scripts/install_local_models.bat",
    "scripts/install_local_models.ps1",
    "scripts/hf_model_tools.ps1",
    "scripts/check_hf_model_access.bat",
    "scripts/check_hf_model_access.ps1",
    "scripts/validate_local_models.py",
    "scripts/verify_local_models.py",
)


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


def check_portable_deployment_contract(checks: HarnessChecks) -> None:
    build = (REPO_ROOT / "scripts" / "build_windows_portable.ps1").read_text(
        encoding="utf-8"
    )
    launcher = (
        REPO_ROOT / "deploy" / "windows-portable" / "portable_launcher.py"
    ).read_text(encoding="utf-8")
    workflow = (
        REPO_ROOT / ".github" / "workflows" / "windows-portable.yml"
    ).read_text(encoding="utf-8")
    downloader = (
        REPO_ROOT / "backend" / "app" / "services" / "model_downloads.py"
    ).read_text(encoding="utf-8")
    download_store = (
        REPO_ROOT / "backend" / "app" / "services" / "model_download_storage.py"
    ).read_text(encoding="utf-8")
    settings_view = (
        REPO_ROOT / "frontend" / "src" / "views" / "SettingsView.vue"
    ).read_text(encoding="utf-8")
    retired_present = [
        path for path in RETIRED_MODEL_INSTALLERS if (REPO_ROOT / path).exists()
    ]
    checks.check(
        "portable-model-isolation",
        "find_spec('torch') is None" in build
        and "find_spec('sentence_transformers') is None" in build
        and not retired_present,
        "portable build excludes native model runtimes and retired installers stay removed"
        if not retired_present
        else f"retired installers present: {', '.join(retired_present)}",
    )
    checks.check(
        "portable-runtime-contract",
        "127.0.0.1" in launcher
        and "STATIC_FRONTEND_ROOT" in launcher
        and "DATA_ROOT" in launcher
        and "--check" in launcher,
        "portable launcher is loopback-only, path-explicit and self-checking",
    )
    checks.check(
        "portable-hash-contract",
        "file_hash.ps1" in build
        and "Get-Sha256Hex" in build
        and "Get-FileHash" not in build,
        "portable build uses the repository .NET SHA-256 helper without optional PowerShell cmdlets",
    )
    checks.check(
        "portable-ci-contract",
        "workflow_dispatch:" in workflow
        and "build_windows_portable.ps1" in workflow
        and "actions/upload-artifact" in workflow,
        "portable workflow builds, verifies and uploads the Win11 artifact",
    )
    checks.check(
        "managed-model-download-contract",
        "MODEL_DOWNLOAD_JOB_KIND" in downloader
        and ".partial" in downloader
        and "model_download_lock" in downloader
        and "publish_staging_generation" in downloader
        and "remote_manifest.resolved_revision" in downloader
        and "remote_sha256_verified" in downloader
        and "proxy_url_ciphertext" in downloader
        and "runtime_installed" in downloader
        and "active_generation" in download_store
        and "force_hash=True" in download_store
        and "MODEL_DOWNLOAD_GENERATIONS_DIRECTORY" in download_store
        and "MODEL_DOWNLOAD_STAGING_DIRECTORY" in download_store
        and "os.replace" in download_store
        and "model-download-proxy" in settings_view,
        "managed weights use pinned revisions, per-model locks, resumable staging, full integrity checks and atomic generation pointers",
    )


def check_full_gguf_installer_contract(checks: HarnessChecks) -> None:
    workflow_path = ".github/workflows/windows-gguf-installer.yml"
    workflow = load_yaml(workflow_path, base_loader=True)
    triggers = workflow.get("on", {}) if isinstance(workflow, dict) else {}
    trigger_ok = (
        isinstance(triggers, dict)
        and set(triggers) == {"workflow_dispatch", "release"}
        and isinstance(triggers.get("workflow_dispatch"), dict)
        and isinstance(triggers.get("release"), dict)
        and triggers["release"].get("types") == ["published"]
    )
    checks.check(
        "gguf-installer-trigger-contract",
        trigger_ok,
        "full-GGUF artifacts build only on explicit dispatch or published release",
    )

    content = (REPO_ROOT / workflow_path).read_text(encoding="utf-8")
    builder = (REPO_ROOT / "scripts/build_windows_gguf_installer.ps1").read_text(
        encoding="utf-8"
    )
    action_revisions = re.findall(r"(?m)^\s*uses:\s*[^\s@]+@([^\s#]+)", content)
    pinned_actions = bool(action_revisions) and all(
        re.fullmatch(r"[0-9a-f]{40}", revision) for revision in action_revisions
    )
    checks.check(
        "gguf-installer-pinned-toolchain",
        "runs-on: windows-2022" in content
        and "windows-latest" not in content
        and "ubuntu-latest" not in content
        and pinned_actions
        and "python-version: \"3.12.12\"" in content
        and "node-version: \"22.22.0\"" in content
        and "--version=6.7.1" in content,
        "runner, Actions, Python, Node and Inno Setup are immutable release inputs",
    )
    checks.check(
        "gguf-installer-build-gates",
        "scripts/model-runtime/prepare_assets.py" in content
        and '"--all"' in content
        and "--strict-release" in content
        and "scripts/build_windows_gguf_installer.ps1" in content
        and "-RequireSetupExe" in content
        and "-SkipModelSmoke" not in content
        and "-SkipSmokeTest" not in content
        and "actions/upload-artifact@" in content
        and "windows-x64.zip.sha256" in content
        and "x64.exe.sha256" in content
        and "provenance.json.sha256" in content
        and "model-runtime\\validate_assets.py" in builder
        and "model-runtime\\smoke_runtime.py" in builder
        and '"--component-lock", $lockPath' in builder,
        "release build prepares locked assets, runs real E/R inference plus full app smoke and uploads ZIP/Setup/hash/provenance",
    )

    validator = subprocess.run(
        [sys.executable, "scripts/model-runtime/validate_assets.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    checks.check(
        "gguf-model-supply-chain",
        validator.returncode == 0,
        "pinned GGUF source/runtime manifest is valid"
        if validator.returncode == 0
        else (validator.stdout + validator.stderr).strip(),
    )

    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    tracked_weights = subprocess.run(
        ["git", "ls-files", "--", "*.gguf", "*.safetensors", "*.bin"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    tracked = [line for line in tracked_weights.stdout.splitlines() if line.strip()]
    checks.check(
        "gguf-weight-boundary",
        tracked_weights.returncode == 0
        and not tracked
        and "/artifacts/build-cache/" in gitignore
        and "/artifacts/installer/" in gitignore
        and "/models/" in gitignore,
        "model weights and generated installers remain outside Git"
        if not tracked
        else f"tracked weight files: {', '.join(tracked)}",
    )

    inno = (REPO_ROOT / "deploy/windows-installer/DebugPlatform.iss").read_text(
        encoding="utf-8"
    )
    installer = (
        REPO_ROOT / "deploy/windows-installer/install_local.ps1"
    ).read_text(encoding="utf-8")
    files_section = inno.split("[Files]", maxsplit=1)[1].split(
        "[Icons]", maxsplit=1
    )[0]
    checks.check(
        "gguf-installer-immutable-app-tree",
        "UninstallFilesDir={localappdata}\\Programs\\GWAPDebugPlatform-Uninstall" in inno
        and 'DestDir: "{app}"' not in files_section
        and 'DestDir: "{tmp}\\GWAPDebugPlatformPayload"' in files_section
        and "AfterInstall: InstallPayloadAtomically" in files_section
        and "ewWaitUntilTerminated" in inno
        and "if ResultCode <> 0 then" in inno
        and "-NoLaunch -NoShortcuts" in inno
        and '[switch]$NoShortcuts' in installer
        and '"Local\\GWAPDebugPlatform.Install"' in installer
        and "$installMutex.WaitOne(" in installer
        and "[System.Threading.AbandonedMutexException]" in installer
        and "$installMutex.ReleaseMutex()" in installer
        and "function Assert-RestorableBackup" in installer
        and "$backupCandidates.Count -gt 1" in installer
        and "$backupCandidates.Count -eq 1" in installer
        and "Assert-RestorableBackup -Path $recoveryBackup" in installer
        and 'Type: filesandordirs; Name: "{app}"; Check: IsExpectedAppRoot' in inno
        and "{localappdata}\\GWAPDebugPlatform" not in inno,
        "Inno extracts to a temporary tree, serializes and recovers atomic publication, propagates failure and uninstalls only the immutable app tree",
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
    check_portable_deployment_contract(checks)
    check_full_gguf_installer_contract(checks)
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
