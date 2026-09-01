#!/usr/bin/env python3
"""Run the Skill-only deterministic pipeline against a fresh local backend.

This smoke test intentionally uses only the Python standard library.  The
production CLI performs the real virtual-environment bootstrap, launches the
vendored FastAPI backend, applies migrations, and drives the public workflow.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterator


EXTERNAL_SKILL_NAME = "ci-external-ap-diagnosis"
EXTERNAL_SKILL_CANARY = "GW_AP_DEBUG_EXTERNAL_METHOD_CANARY_6A0F3B19"
EXTERNAL_LOG_CANARY = "GW_AP_DEBUG_EXTERNAL_LOG_CANARY_9C4E2D71"


class SmokeFailure(RuntimeError):
    """Raised when the deterministic release envelope is incomplete."""


def require(condition: Any, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def read_json(path: Path) -> Any:
    require(path.is_file(), f"missing JSON export: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SmokeFailure(f"invalid JSON export {path}: {exc}") from exc


def canonical_json_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def command_output(command: list[str], *, cwd: Path) -> str:
    completed = subprocess.run(
        command,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    require(
        completed.returncode == 0,
        f"command failed ({completed.returncode}): {' '.join(command)}\n{completed.stdout}",
    )
    return completed.stdout


def skill_wrapper_command(repository_root: Path, *arguments: str) -> list[str]:
    """Build the native package-wrapper command for the current platform."""

    if sys.platform == "win32":
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        require(bool(powershell), "Windows PowerShell is required for the package smoke")
        wrapper = repository_root / "scripts" / "gw_ap_debug.ps1"
        require(wrapper.is_file(), f"Windows Skill wrapper is missing: {wrapper}")
        return [
            str(powershell),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(wrapper),
            "-Python",
            sys.executable,
            *arguments,
        ]
    wrapper = repository_root / "scripts" / "gw_ap_debug.sh"
    require(wrapper.is_file(), f"POSIX Skill wrapper is missing: {wrapper}")
    shell = shutil.which("bash")
    require(bool(shell), "Bash is required for the POSIX package smoke")
    return [str(shell), str(wrapper), *arguments]


def check_cli_contract(repository_root: Path) -> dict[str, Any]:
    cli = repository_root / "scripts" / "debug_platform_skill.py"
    runtime = repository_root / "runtime" / "backend"
    required_paths = (
        cli,
        runtime / "app" / "main.py",
        runtime / "alembic.ini",
        runtime / "constraints.lock",
        runtime / "uv.lock",
        repository_root / "references" / "fault-tree.md",
        repository_root / "references" / "log-analysis.md",
        repository_root / "scripts" / "gw_ap_debug.ps1",
        repository_root / "scripts" / "gw_ap_debug.sh",
    )
    missing = [str(path) for path in required_paths if not path.is_file()]
    require(not missing, "release inputs are missing: " + ", ".join(missing))

    top_help = command_output(
        skill_wrapper_command(repository_root, "--help"), cwd=repository_root,
    )
    run_help = command_output(
        skill_wrapper_command(repository_root, "run", "--help"), cwd=repository_root,
    )
    for command in ("bootstrap", "run", "triage", "diagnose", "result"):
        require(command in top_help, f"CLI help is missing the {command!r} command")
    for option in (
        "--mode",
        "--bootstrap",
        "--state-dir",
        "--output-dir",
        "--ap-log",
        "--job-timeout",
        "--diagnostic-skill",
        "--diagnostic-skill-fault-tree",
        "--diagnostic-skill-log-analysis",
        "--diagnostic-skill-scope",
        "--max-host-method-tokens",
    ):
        require(option in run_help, f"run help is missing the {option!r} option")
    require("deterministic" in run_help, "run help no longer advertises deterministic mode")
    return {
        "cli": str(cli),
        "python": sys.version.split()[0],
        "runtime": str(runtime),
        "wrapper": str(
            repository_root / "scripts" /
            ("gw_ap_debug.ps1" if sys.platform == "win32" else "gw_ap_debug.sh")
        ),
    }


def verify_method_generation(state_dir: Path) -> dict[str, Any]:
    """Verify that active.json authenticates one complete immutable method pair."""

    control_root = state_dir / "method-packs"
    pointer_path = control_root / "active.json"
    pointer = read_json(pointer_path)
    require(isinstance(pointer, dict), "active method pointer must be a JSON object")
    require(
        pointer.get("schema") == "gw-ap-debug-method-active/v1",
        "active method pointer has an unexpected schema",
    )
    generation_id = str(pointer.get("generation_id") or "")
    relative = Path(str(pointer.get("path") or ""))
    require(
        re.fullmatch(r"gen-[0-9a-f]{20}", generation_id) is not None,
        f"active method generation ID is invalid: {generation_id!r}",
    )
    require(
        not relative.is_absolute()
        and relative.as_posix() == f"generations/{generation_id}",
        f"active method generation path is invalid: {relative}",
    )
    generations_root = (control_root / "generations").resolve()
    generation_dir = (control_root / relative).resolve()
    try:
        generation_dir.relative_to(generations_root)
    except ValueError as exc:
        raise SmokeFailure("active method generation escapes its control root") from exc

    manifest = read_json(generation_dir / "manifest.json")
    require(isinstance(manifest, dict), "active method generation manifest must be a JSON object")
    require(
        manifest.get("schema") == "gw-ap-debug-method-generation/v1"
        and manifest.get("id") == generation_id,
        "active method generation manifest identity is invalid",
    )
    require(
        manifest.get("scope") == "persistent",
        f"bootstrap active method generation has an unexpected scope: {manifest.get('scope')!r}",
    )
    require(
        canonical_json_sha256(manifest) == pointer.get("manifest_sha256"),
        "active method pointer does not authenticate its generation manifest",
    )
    roles = manifest.get("roles")
    filenames = ("故障树.md", "日志分析.md")
    require(
        isinstance(roles, dict) and set(roles) == set(filenames),
        "active method generation does not contain exactly two diagnostic roles",
    )
    verified_roles: dict[str, str] = {}
    for filename in filenames:
        path = generation_dir / filename
        require(path.is_file(), f"active method generation role is missing: {path}")
        content = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        role = roles.get(filename)
        require(isinstance(role, dict), f"active method generation role metadata is invalid: {filename}")
        expected = str(role.get("sha256") or "")
        require(digest == expected, f"active method generation role hash mismatch: {path}")
        verified_roles[filename] = digest

    legacy_seeds = state_dir / "methods"
    require(
        all((legacy_seeds / name).is_file() for name in filenames),
        "bootstrap did not initialize the legacy-compatible method seeds",
    )
    return {
        "generation_id": generation_id,
        "generation_dir": str(generation_dir),
        "scope": manifest.get("scope"),
        "roles": verified_roles,
        "legacy_seed_dir": str(legacy_seeds),
    }


def reserve_local_base_url() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        port = int(listener.getsockname()[1])
    return f"http://127.0.0.1:{port}/api/v1"


def write_synthetic_log(path: Path) -> None:
    # These are synthetic strings chosen to exercise both the Huawei parser and
    # bundled diagnostic method patterns.  They contain no production data.
    path.write_text(
        "\n".join(
            (
                "Start run collect command : WLAN : display ap all",
                "WARN 2026-08-31 10:00:01 [wlan] RefreshTopoTree APInstId: 7 TestLinkOK failed",
                "ERROR 2026-08-31 10:00:02 [udm] ApInst:7 Recv Offline Event, src=CtrlPointVerify",
                "WARN 2026-08-31 10:00:03 [udm] [Abnormal] curTime[900], iAdvrTimeOut[250], lastEventTime[500]",
                "ERROR 2026-08-31 10:00:04 [udm] dynamic:[udm] listen port check failed",
                "WARN 2026-08-31 10:00:05 [network] wan0 link down carrier lost",
                "ERROR 2026-08-31 10:00:06 [wlan] COVER_PonApLeaveProc ApInst offline:7",
                "WARN 2026-08-31 10:00:07 [hostapd] hostapd failed to set beacon on wlan0",
                f"ERROR 2026-08-31 10:00:08 [wlan] {EXTERNAL_LOG_CANARY} peer handoff failed",
            )
        )
        + "\n",
        encoding="utf-8",
    )


def write_external_diagnostic_skill(skill_dir: Path) -> dict[str, str]:
    """Create a self-contained external Skill with explicit role metadata."""

    references = skill_dir / "references"
    references.mkdir(parents=True, exist_ok=False)
    fault_tree_relative = "references/diagnosis.md"
    log_analysis_relative = "references/logs.md"
    (skill_dir / "SKILL.md").write_text(
        "\n".join((
            "---",
            f"name: {EXTERNAL_SKILL_NAME}",
            "description: Synthetic external knowledge for the deterministic release smoke.",
            "metadata:",
            f"  gw_ap_debug_fault_tree: {fault_tree_relative}",
            f"  gw_ap_debug_log_analysis: {log_analysis_relative}",
            "---",
            "# CI external diagnosis Skill",
            "",
            "This package is synthetic and contains Markdown knowledge only.",
            "",
        )),
        encoding="utf-8",
        newline="\n",
    )
    (skill_dir / fault_tree_relative).write_text(
        "\n".join((
            "# Comprehensive diagnosis",
            "",
            f"Knowledge provenance canary: `{EXTERNAL_SKILL_CANARY}`.",
            "",
            "| Decision point | Required evidence | Conclusion |",
            "|---|---|---|",
            f"| Peer handoff | `{EXTERNAL_LOG_CANARY}` in the AP log | Inspect the AP peer state |",
            "",
        )),
        encoding="utf-8",
        newline="\n",
    )
    (skill_dir / log_analysis_relative).write_text(
        "\n".join((
            "# Log analysis",
            "",
            "| Log signature | Meaning |",
            "|---|---|",
            f"| `{EXTERNAL_LOG_CANARY}` | Synthetic peer-handoff failure for CI |",
            "",
        )),
        encoding="utf-8",
        newline="\n",
    )
    return {
        "skill_dir": str(skill_dir),
        "fault_tree": fault_tree_relative,
        "log_analysis": log_analysis_relative,
    }


def migration_heads(migrations_dir: Path) -> set[str]:
    revisions: set[str] = set()
    parents: set[str] = set()
    revision_pattern = re.compile(r"^revision(?:\s*:[^=]+)?\s*=\s*['\"]([^'\"]+)['\"]", re.MULTILINE)
    parent_pattern = re.compile(
        r"^down_revision(?:\s*:[^=]+)?\s*=\s*['\"]([^'\"]+)['\"]", re.MULTILINE,
    )
    for path in migrations_dir.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        revision = revision_pattern.search(text)
        require(revision is not None, f"migration has no scalar revision: {path}")
        revisions.add(revision.group(1))
        parent = parent_pattern.search(text)
        if parent:
            parents.add(parent.group(1))
    heads = revisions.difference(parents)
    require(heads, f"could not determine a migration head under {migrations_dir}")
    return heads


def scalar(connection: sqlite3.Connection, sql: str) -> Any:
    row = connection.execute(sql).fetchone()
    return row[0] if row else None


def verify_database(repository_root: Path, state_dir: Path) -> dict[str, Any]:
    database = state_dir / "data" / "gw_ap_debug.db"
    require(database.is_file() and database.stat().st_size > 0, "runtime database was not created")
    expected_heads = migration_heads(
        repository_root / "runtime" / "backend" / "app" / "migrations" / "versions"
    )
    # sqlite3.Connection's context manager commits/rolls back but does not close
    # the handle. Close it explicitly so Windows can remove the isolated state
    # directory immediately after the smoke completes.
    with contextlib.closing(sqlite3.connect(database)) as connection:
        applied_heads = {
            str(row[0]) for row in connection.execute("SELECT version_num FROM alembic_version")
        }
        require(
            applied_heads == expected_heads,
            f"migration head mismatch: applied={sorted(applied_heads)}, expected={sorted(expected_heads)}",
        )
        counts = {
            "cases": int(scalar(connection, "SELECT COUNT(*) FROM cases") or 0),
            "artifacts": int(scalar(connection, "SELECT COUNT(*) FROM artifacts") or 0),
            "log_events": int(scalar(connection, "SELECT COUNT(*) FROM log_events") or 0),
            "triage_runs": int(scalar(connection, "SELECT COUNT(*) FROM log_triage_runs") or 0),
            "analysis_runs": int(scalar(connection, "SELECT COUNT(*) FROM analysis_runs") or 0),
        }
        require(counts["cases"] == 1, f"expected one synthetic case, found {counts['cases']}")
        require(counts["artifacts"] == 1, f"expected one uploaded artifact, found {counts['artifacts']}")
        require(counts["log_events"] >= 5, f"parse produced too few log events: {counts['log_events']}")
        require(counts["triage_runs"] == 1, f"expected one triage run, found {counts['triage_runs']}")
        require(counts["analysis_runs"] == 1, f"expected one analysis run, found {counts['analysis_runs']}")
        artifact = connection.execute(
            "SELECT original_name, status, source_device_type, source_device_role, size_bytes, sha256 "
            "FROM artifacts"
        ).fetchone()
        require(artifact is not None, "uploaded artifact record is missing")
        artifact_name = Path(str(artifact[0])).name
        require(
            Path(artifact_name).stem == "collectDebuginfo"
            and Path(artifact_name).suffix.casefold() in {"", ".txt"},
            f"unexpected uploaded artifact name: {artifact[0]}",
        )
        require(artifact[1] == "PARSED", f"uploaded artifact was not parsed: {artifact[1]}")
        require(artifact[2:4] == ("AP", "SECONDARY"), f"upload provenance was not retained: {artifact[2:4]}")
        require(int(artifact[4]) > 0, "uploaded artifact has an empty stored payload")
        require(len(str(artifact[5])) == 64, "uploaded artifact has no SHA-256 digest")
        triage_statuses = [
            str(row[0]) for row in connection.execute("SELECT status FROM log_triage_runs")
        ]
        analysis_statuses = [
            str(row[0]) for row in connection.execute("SELECT status FROM analysis_runs")
        ]
        job_rows = [
            (str(row[0]), str(row[1]))
            for row in connection.execute("SELECT kind, status FROM jobs ORDER BY created_at")
        ]
        require(triage_statuses == ["COMPLETED"], f"triage did not complete: {triage_statuses}")
        require(analysis_statuses == ["COMPLETED"], f"diagnosis did not complete: {analysis_statuses}")
        require(len(job_rows) >= 3, f"expected parse/triage/diagnosis jobs, found {job_rows}")
        require(
            all(status == "COMPLETED" for _kind, status in job_rows),
            f"one or more pipeline jobs did not complete: {job_rows}",
        )
        job_kinds = {kind for kind, _status in job_rows}
        require(
            {"parse_artifact", "log_triage", "analyze_case"}.issubset(job_kinds),
            f"pipeline job kinds are incomplete: {sorted(job_kinds)}",
        )
    return {
        "database": str(database),
        "migration_heads": sorted(applied_heads),
        "counts": counts,
        "jobs": [{"kind": kind, "status": status} for kind, status in job_rows],
    }


def verify_export(output_dir: Path) -> dict[str, Any]:
    required_files = (
        "manifest.json",
        "case.json",
        "analysis.json",
        "analysis_record.json",
        "evidence.json",
        "diagnosis.md",
    )
    missing = [name for name in required_files if not (output_dir / name).is_file()]
    require(not missing, "portable export is missing files: " + ", ".join(missing))
    manifest = read_json(output_dir / "manifest.json")
    case = read_json(output_dir / "case.json")
    analysis = read_json(output_dir / "analysis.json")
    analysis_record = read_json(output_dir / "analysis_record.json")
    triage_files = sorted((output_dir / "triage").glob("*.json"))
    require(manifest.get("schema") == "gw-ap-debug-skill-run/v2", "unexpected manifest schema")
    require(manifest.get("execution_mode") == "deterministic", "export did not record deterministic mode")
    require(manifest.get("analysis_status") == "COMPLETED", "exported analysis is not complete")
    require(case.get("id") == manifest.get("case_id"), "case export does not match the manifest")
    require(
        analysis_record.get("id") == manifest.get("analysis_id"),
        "analysis record does not match the manifest",
    )
    require(bool(manifest.get("triage_run_ids")), "manifest has no completed triage run")
    require(len(triage_files) == len(manifest["triage_run_ids"]), "triage exports are incomplete")
    require(isinstance(analysis.get("summary"), str) and analysis["summary"].strip(), "diagnosis has no summary")
    require(bool(analysis.get("analysis_engine")), "diagnosis has no recorded analysis engine")
    diagnosis = (output_dir / "diagnosis.md").read_text(encoding="utf-8")
    require(len(diagnosis.strip()) >= 200, "human-readable diagnosis export is unexpectedly small")
    for path in triage_files:
        payload = read_json(path)
        require((payload.get("triage") or {}).get("status") == "COMPLETED", f"incomplete triage export: {path}")
    return {
        "output_dir": str(output_dir),
        "case_id": manifest.get("case_id"),
        "analysis_id": manifest.get("analysis_id"),
        "analysis_engine": analysis.get("analysis_engine"),
        "triage_exports": len(triage_files),
    }


def verify_run_scoped_external_methods(
    state_dir: Path,
    output_dir: Path,
    *,
    persistent_pointer_before: dict[str, Any],
) -> dict[str, Any]:
    """Prove the run used external knowledge without mutating persistent state."""

    export_manifest = read_json(output_dir / "manifest.json")
    require(
        export_manifest.get("diagnostic_method_scope") == "run",
        "export did not record run-scoped diagnostic methods",
    )
    require(
        export_manifest.get("persistent_active_unchanged") is True,
        "export did not retain the persistent-active invariant",
    )
    generation_id = str(export_manifest.get("method_generation_id") or "")
    require(
        re.fullmatch(r"gen-[0-9a-f]{20}", generation_id) is not None,
        f"run-scoped method generation ID is invalid: {generation_id!r}",
    )
    generation_dir = (
        state_dir / "method-packs" / "generations" / generation_id
    ).resolve()
    exported_generation_dir = Path(
        str(export_manifest.get("diagnostic_methods_dir") or "")
    ).expanduser().resolve()
    require(
        exported_generation_dir == generation_dir,
        "exported run-scoped method directory does not match its generation ID",
    )
    generation = read_json(generation_dir / "manifest.json")
    require(
        generation.get("schema") == "gw-ap-debug-method-generation/v1"
        and generation.get("id") == generation_id
        and generation.get("scope") == "run",
        "run-scoped method generation manifest identity is invalid",
    )
    roles = generation.get("roles")
    filenames = ("故障树.md", "日志分析.md")
    require(
        isinstance(roles, dict) and set(roles) == set(filenames),
        "run-scoped method generation does not contain exactly two roles",
    )
    role_hashes: dict[str, str] = {}
    generation_text: list[str] = []
    for filename in filenames:
        role_path = generation_dir / filename
        require(role_path.is_file(), f"run-scoped role is missing: {role_path}")
        content = role_path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        expected = str(((roles or {}).get(filename) or {}).get("sha256") or "")
        require(digest == expected, f"run-scoped role hash mismatch: {role_path}")
        role_hashes[filename] = digest
        generation_text.append(content)
    require(
        EXTERNAL_SKILL_CANARY in "\n".join(generation_text),
        "external Skill knowledge canary is absent from the run generation",
    )

    packs = generation.get("packs")
    require(isinstance(packs, list), "run-scoped method generation pack list is invalid")
    selected_pack = next(
        (item for item in packs if isinstance(item, dict) and item.get("name") == EXTERNAL_SKILL_NAME),
        None,
    )
    require(selected_pack is not None, "run generation does not identify the external Skill pack")
    expected_generation_id = "gen-" + canonical_json_sha256({
        "scope": "run",
        "roles": role_hashes,
        "packs": [
            {
                "id": str(item.get("id") or ""),
                "name": str(item.get("name") or ""),
                "content_sha256": str(item.get("content_sha256") or ""),
            }
            for item in packs
            if isinstance(item, dict)
        ],
    })[:20]
    require(
        generation_id == expected_generation_id,
        "run-scoped generation ID is not content-addressed by its roles and packs",
    )
    imported = export_manifest.get("imported_diagnostic_method_packs")
    require(isinstance(imported, list), "exported diagnostic Skill pack list is invalid")
    exported_pack = next(
        (item for item in imported if isinstance(item, dict) and item.get("name") == EXTERNAL_SKILL_NAME),
        None,
    )
    require(
        exported_pack is not None and exported_pack.get("status") == "RUN_SCOPED",
        "export does not record the external Skill as run-scoped",
    )

    analysis = read_json(output_dir / "analysis.json")
    catalog = ((analysis.get("diagnostic_planning") or {}).get("method_catalog") or [])
    require(isinstance(catalog, list), "analysis method catalog is invalid")
    catalog_hashes = {
        str(item.get("content_sha256") or "")
        for item in catalog
        if isinstance(item, dict) and str(item.get("id") or "").startswith("LOCALDOC-")
    }
    expected_hashes = set(role_hashes.values())
    require(
        expected_hashes.issubset(catalog_hashes),
        "analysis method catalog does not reference both run-scoped role hashes",
    )
    evidence = read_json(output_dir / "evidence.json")
    require(isinstance(evidence, list), "analysis evidence export is not a list")
    method_evidence = [
        item for item in evidence
        if isinstance(item, dict) and str(item.get("content_sha256") or "") in expected_hashes
    ]
    require(
        {str(item.get("content_sha256") or "") for item in method_evidence} == expected_hashes,
        "analysis evidence does not contain both run-scoped method documents",
    )
    require(
        all(item.get("content_omitted") is True and "content" not in item for item in method_evidence),
        "persisted diagnostic-method evidence did not apply the content-omission contract",
    )
    triage_text = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted((output_dir / "triage").glob("*.json"))
    )
    require(
        EXTERNAL_LOG_CANARY in triage_text,
        "external Skill log canary was not matched in the triage output",
    )

    pointer_after = read_json(state_dir / "method-packs" / "active.json")
    require(
        pointer_after == persistent_pointer_before,
        "run-scoped Skill changed the persistent active method pointer",
    )
    case_id = str(export_manifest.get("case_id") or "")
    require(case_id, "export manifest has no case ID for binding cleanup verification")
    binding_name = hashlib.sha256(case_id.encode("utf-8")).hexdigest()[:24]
    binding_path = state_dir / "method-packs" / "bindings" / f"case-{binding_name}.json"
    require(
        not binding_path.exists(),
        f"successful run left its case method binding behind: {binding_path}",
    )
    return {
        "scope": "run",
        "generation_id": generation_id,
        "generation_dir": str(generation_dir),
        "pack_id": str(selected_pack.get("id") or ""),
        "role_hashes": role_hashes,
        "method_catalog_hashes_verified": len(expected_hashes),
        "method_evidence_verified": len(method_evidence),
        "knowledge_canary_in_generation_verified": True,
        "log_canary_in_triage_output_verified": True,
        "persistent_generation_id": persistent_pointer_before.get("generation_id"),
        "case_binding_cleaned": True,
    }


def backend_log_tail(state_dir: Path, limit: int = 120) -> str:
    path = state_dir / "logs" / "backend.log"
    if not path.is_file():
        return "<backend log was not created>"
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:])
    except OSError as exc:
        return f"<could not read backend log: {exc}>"


def cleanup_temporary_work_directory(work_dir: Path) -> None:
    """Remove one exact smoke temp directory after validating its boundary."""
    temp_root = Path(tempfile.gettempdir()).resolve()
    work_dir = work_dir.expanduser().resolve()
    if work_dir.parent != temp_root or not work_dir.name.startswith("gw-ap-debug-ci-"):
        raise SmokeFailure(f"refusing unsafe temporary work directory: {work_dir}")
    for attempt in range(8):
        try:
            shutil.rmtree(work_dir)
            break
        except FileNotFoundError:
            break
        except OSError as exc:
            transient = getattr(exc, "winerror", None) in {5, 32, 33}
            if not transient or attempt == 7:
                raise
            time.sleep(0.25 * (attempt + 1))


@contextlib.contextmanager
def temporary_work_directory() -> Iterator[Path]:
    """Create and reliably clean a validated CI temp root on Windows.

    A just-terminated SQLite backend can retain a file handle for a fraction of
    a second on Windows.  ``TemporaryDirectory`` performs only one cleanup
    attempt, which can turn a successful smoke into a false failure.  Retry only
    lock/access errors and only for the exact directory returned by ``mkdtemp``.
    """
    temp_root = Path(tempfile.gettempdir()).resolve()
    work_dir = Path(tempfile.mkdtemp(prefix="gw-ap-debug-ci-", dir=temp_root)).resolve()
    try:
        yield work_dir
    finally:
        cleanup_temporary_work_directory(work_dir)


def run_smoke(repository_root: Path, *, timeout_seconds: float) -> dict[str, Any]:
    with temporary_work_directory() as work_dir:
        state_dir = work_dir / "state"
        output_dir = work_dir / "export"
        synthetic_log = work_dir / "collectDebuginfo"
        external_skill = write_external_diagnostic_skill(work_dir / "external-diagnostic-skill")
        write_synthetic_log(synthetic_log)
        base_url = reserve_local_base_url()
        started = time.monotonic()

        def remaining_timeout() -> float:
            remaining = timeout_seconds - (time.monotonic() - started)
            require(remaining > 0, f"release smoke exceeded {timeout_seconds:.0f}s")
            return remaining

        bootstrap_command = skill_wrapper_command(
            repository_root,
            "bootstrap",
            "--platform-root",
            str(repository_root / "runtime"),
            "--state-dir",
            str(state_dir),
        )
        print("[ci-smoke] bootstrapping through the native package wrapper", flush=True)
        try:
            bootstrap = subprocess.run(
                bootstrap_command,
                cwd=repository_root,
                stdin=subprocess.DEVNULL,
                check=False,
                timeout=remaining_timeout(),
            )
        except subprocess.TimeoutExpired as exc:
            raise SmokeFailure(
                f"backend bootstrap exceeded the total {timeout_seconds:.0f}s smoke budget"
            ) from exc
        require(
            bootstrap.returncode == 0,
            f"package-wrapper bootstrap failed with exit code {bootstrap.returncode}",
        )
        persistent_generation = verify_method_generation(state_dir)
        persistent_pointer_before = read_json(state_dir / "method-packs" / "active.json")

        command = skill_wrapper_command(
            repository_root,
            "run",
            "--mode",
            "deterministic",
            "--title",
            "CI synthetic AP offline smoke",
            "--description",
            "Synthetic evidence validates the portable deterministic pipeline.",
            "--ap-log",
            str(synthetic_log),
            "--diagnostic-skill",
            external_skill["skill_dir"],
            "--diagnostic-skill-fault-tree",
            external_skill["fault_tree"],
            "--diagnostic-skill-log-analysis",
            external_skill["log_analysis"],
            "--diagnostic-skill-scope",
            "run",
            "--base-url",
            base_url,
            "--platform-root",
            str(repository_root / "runtime"),
            "--state-dir",
            str(state_dir),
            "--output-dir",
            str(output_dir),
            "--bootstrap",
            "--job-timeout",
            "600",
            "--max-evidence-per-bucket",
            "100",
            "--max-occurrences-per-match",
            "100",
        )
        print(
            "[ci-smoke] running wrapper-based deterministic pipeline with an external run-scoped Skill",
            flush=True,
        )
        try:
            completed = subprocess.run(
                command,
                cwd=repository_root,
                stdin=subprocess.DEVNULL,
                check=False,
                timeout=remaining_timeout(),
            )
        except subprocess.TimeoutExpired as exc:
            raise SmokeFailure(
                f"deterministic pipeline exceeded the total {timeout_seconds:.0f}s smoke budget\n"
                f"backend log tail:\n{backend_log_tail(state_dir)}"
            ) from exc
        if completed.returncode != 0:
            raise SmokeFailure(
                f"deterministic pipeline failed with exit code {completed.returncode}\n"
                f"backend log tail:\n{backend_log_tail(state_dir)}"
            )
        venv_python = (
            state_dir / "venv" / "Scripts" / "python.exe"
            if sys.platform == "win32"
            else state_dir / "venv" / "bin" / "python"
        )
        require(venv_python.is_file(), "bootstrap did not create the backend virtual environment")
        require(
            (state_dir / "venv" / ".gw-ap-debug-environment.json").is_file(),
            "bootstrap did not record its dependency fingerprint",
        )
        persistent_generation_after = verify_method_generation(state_dir)
        require(
            persistent_generation_after["generation_id"] == persistent_generation["generation_id"],
            "run-scoped Skill replaced the persistent method generation",
        )
        database = verify_database(repository_root, state_dir)
        exported = verify_export(output_dir)
        external_methods = verify_run_scoped_external_methods(
            state_dir,
            output_dir,
            persistent_pointer_before=persistent_pointer_before,
        )
        return {
            "ok": True,
            "base_url": base_url,
            "bootstrap_python": str(venv_python),
            "persistent_method_generation": persistent_generation_after,
            "run_scoped_external_methods": external_methods,
            "database": database,
            "export": exported,
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate the Skill-only bootstrap and deterministic diagnosis release path",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Validate repository inputs and CLI help without bootstrapping the backend",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=25 * 60,
        help="Maximum time for bootstrap plus the deterministic pipeline",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repository_root = Path(__file__).resolve().parents[1]
    try:
        contract = check_cli_contract(repository_root)
        result = {"ok": True, "check_only": True, "contract": contract}
        if not args.check_only:
            require(args.timeout_seconds > 0, "--timeout-seconds must be greater than zero")
            result = {"contract": contract, **run_smoke(repository_root, timeout_seconds=args.timeout_seconds)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (
        SmokeFailure,
        OSError,
        UnicodeError,
        sqlite3.Error,
        subprocess.SubprocessError,
    ) as exc:
        print(f"[ci-smoke] ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
