#!/usr/bin/env python3
"""Verify the vendored runtime against the source commit recorded in the manifest.

The Skill-only branch deliberately has no Git ancestry with ``main``.  This
checker therefore compares Git blobs by path instead of relying on a merge base.
It never fetches from a remote.  Exit codes are 0 for PASS, 2 for FAIL, and 3
for SKIPPED when the recorded source commit is not available locally.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Iterable


SCHEMA = "gw-ap-debug-source-mapping/v2"
EXCLUDED_RUNTIME_PARTS = {".venv", "__pycache__", "data", "artifacts"}
LOCAL_METHOD_FILES = {"日志分析.md", "故障树.md"}

# A manifest declaration names the intent but, by design, does not contain
# source paths.  Keeping this small mapping here makes every allowed source
# divergence explicit and reviewable.
DECLARED_PORTABILITY_PATCHES = {
    "backend/app/core/config.py": "external DATA_ROOT support",
    "backend/app/api/system.py": (
        "external method root with immutable generations, case bindings, and runtime probe"
    ),
    "backend/app/services/diagnostic_methods.py": (
        "external method root with immutable generations, case bindings, and runtime probe"
    ),
    "backend/app/services/log_triage.py": (
        "deterministic Triage when backend model egress is not approved"
    ),
    "backend/constraints.lock": "Mako 1.4.1 non-yanked dependency lock",
    "backend/uv.lock": "Mako 1.4.1 non-yanked dependency lock",
}

# Every runtime portability patch is pinned to the exact normalized source ->
# runtime transformation reviewed for the source commit in skill-manifest.json.
# The two content hashes make the endpoints explicit; the canonical unified-
# diff hash makes the reviewed delta independently auditable.  Updating a
# portability patch or its source snapshot therefore requires an intentional
# review and an update to this table instead of merely remaining "different".
EXPECTED_PORTABILITY_PATCH_FINGERPRINTS = {
    "backend/app/core/config.py": {
        "source_normalized_sha256": "d40d022748621c91dd99d45c392a93288f7179215a1a9d5550161470d59565b0",
        "runtime_normalized_sha256": "8dbeae1c3741324d2988a17e1594b065f623fd096bad2aa41b7b42f56cf6179b",
        "normalized_diff_sha256": "e9ccc213266599466f8025c564a528651c7fb6df3ceca68b65b44c4e50063a3e",
    },
    "backend/app/api/system.py": {
        "source_normalized_sha256": "41abec3d0ce00fa53a24af008cafa9bf880ee9769a3f4954aaf5fdae89015281",
        "runtime_normalized_sha256": "f4a4ac89f6d42b0cab8b0d98fda18c055aee3bfac5acfbc1658f19a23b772d91",
        "normalized_diff_sha256": "61e8be23c4b509a6ceb06f3d8177fc4ad3d0e09ba4a2eb3ee5f4cb81cea8afcc",
    },
    "backend/app/services/diagnostic_methods.py": {
        "source_normalized_sha256": "3c5a7e94f1995ed803d9c91f787f48de12aead9890294bdc55ea8e4578aff42e",
        "runtime_normalized_sha256": "9e6bdbf3c9921ef55c4abbcc1dc4ed23009a7de9fc7a28d93efbfa79162d2e6d",
        "normalized_diff_sha256": "b3c6f07405614bca929a4e7ef3d70060547a8d2facec4cc279891eade45345fe",
    },
    "backend/app/services/log_triage.py": {
        "source_normalized_sha256": "09c56d9ad281a93156af477cba36e0c4ee938603e4c3a995e5dbb2d32d1366fa",
        "runtime_normalized_sha256": "9fffb0630b368b662d585d153a706cf290245d74f6480dc35b7ed3b431f4e313",
        "normalized_diff_sha256": "fee576ccede2510b7e545fbf2beb4447ee0855de61645343180a0ef15f3864b0",
    },
    "backend/constraints.lock": {
        "source_normalized_sha256": "f8f6e49f708ccc80df51c4a829b577be95a26cca2a8043f9b23d53ce198e2b8c",
        "runtime_normalized_sha256": "fd2e17d176254493db9ee834e4a755a3b437d363237bbaa7fed20cba6450f7c8",
        "normalized_diff_sha256": "064f39e1b13027c9d7386a3197eb0109408ccf7063b1e9d0698058a357ba679c",
    },
    "backend/uv.lock": {
        "source_normalized_sha256": "906cb03bf7d035986c28cd4d40ed2a45c2da42e22dcc37eba348ea1f66a8e429",
        "runtime_normalized_sha256": "a09b1404b8093a4b412710a9085b0f8f3c9d5d1583b2a4b68714d068e55ae827",
        "normalized_diff_sha256": "5ee1014ffad1a9cf5f365b42e801167ff04206031501ef73ce3fd18b84297d97",
    },
}

PATCH_FINGERPRINT_FIELDS = (
    "source_normalized_sha256",
    "runtime_normalized_sha256",
    "normalized_diff_sha256",
)

# This declared patch lives in the outer launcher/bootstrap layer, not under
# runtime/.  Recording that fact avoids incorrectly claiming that this checker
# verified it against a backend source blob.
NON_RUNTIME_PATCHES = {"dependency-only bootstrap without editable source writes"}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize_line_endings(data: bytes) -> bytes:
    """Normalize only CRLF/CR to LF; do not hide other byte differences."""

    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def normalized_diff(source_data: bytes, runtime_data: bytes) -> bytes:
    """Return a stable, binary-safe unified diff over LF-normalized content."""

    source_normalized = normalize_line_endings(source_data)
    runtime_normalized = normalize_line_endings(runtime_data)
    return b"".join(
        difflib.diff_bytes(
            difflib.unified_diff,
            source_normalized.splitlines(keepends=True),
            runtime_normalized.splitlines(keepends=True),
            fromfile=b"source",
            tofile=b"runtime",
            lineterm=b"\n",
        )
    )


def portability_patch_fingerprints(
    source_data: bytes, runtime_data: bytes
) -> dict[str, str]:
    """Fingerprint both normalized endpoints and their canonical delta."""

    return {
        "source_normalized_sha256": sha256(normalize_line_endings(source_data)),
        "runtime_normalized_sha256": sha256(normalize_line_endings(runtime_data)),
        "normalized_diff_sha256": sha256(normalized_diff(source_data, runtime_data)),
    }


def patch_fingerprint_configuration_errors() -> list[str]:
    errors: list[str] = []
    declared_paths = set(DECLARED_PORTABILITY_PATCHES)
    expected_paths = set(EXPECTED_PORTABILITY_PATCH_FINGERPRINTS)
    for path in sorted(declared_paths - expected_paths):
        errors.append(f"declared portability patch has no pinned fingerprint: {path}")
    for path in sorted(expected_paths - declared_paths):
        errors.append(f"pinned portability patch has no declaration: {path}")
    for path in sorted(declared_paths & expected_paths):
        expected = EXPECTED_PORTABILITY_PATCH_FINGERPRINTS[path]
        if not isinstance(expected, dict):
            errors.append(f"pinned portability patch fingerprint must be an object: {path}")
            continue
        unexpected_fields = set(expected) - set(PATCH_FINGERPRINT_FIELDS)
        missing_fields = set(PATCH_FINGERPRINT_FIELDS) - set(expected)
        for field in sorted(missing_fields):
            errors.append(f"pinned portability patch fingerprint is missing {field}: {path}")
        for field in sorted(unexpected_fields):
            errors.append(f"pinned portability patch fingerprint has unknown {field}: {path}")
        for field in PATCH_FINGERPRINT_FIELDS:
            value = expected.get(field)
            if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
                errors.append(
                    f"pinned portability patch fingerprint has invalid {field}: {path}"
                )
    return errors


def runtime_files(runtime_root: Path) -> list[Path]:
    result: list[Path] = []
    if not runtime_root.is_dir():
        return result
    for path in runtime_root.rglob("*"):
        if not path.is_file():
            continue
        relative_parts = path.relative_to(runtime_root).parts
        if any(part in EXCLUDED_RUNTIME_PARTS for part in relative_parts):
            continue
        result.append(path)
    return sorted(result, key=lambda item: item.relative_to(runtime_root).as_posix())


def source_path_for_runtime(runtime_path: str) -> str | None:
    if runtime_path in LOCAL_METHOD_FILES:
        return None
    if runtime_path == ".env.example":
        return ".env.example"
    if runtime_path.startswith("backend/"):
        return runtime_path
    return None


def intentionally_excluded_source_path(source_path: str) -> bool:
    return source_path == "backend/Dockerfile" or source_path.startswith("backend/tests/")


def relevant_source_paths(paths: Iterable[str]) -> set[str]:
    return {
        path
        for path in paths
        if path == ".env.example" or path.startswith("backend/")
    }


def run_git(repo: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(repo), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def git_commit_available(repo: Path, commit: str) -> tuple[bool, str]:
    try:
        result = run_git(repo, "cat-file", "-t", commit)
    except (FileNotFoundError, OSError) as exc:
        return False, str(exc)
    object_type = result.stdout.decode("ascii", errors="replace").strip()
    if result.returncode == 0 and object_type == "commit":
        return True, ""
    error = result.stderr.decode("utf-8", errors="replace").strip()
    if result.returncode == 0:
        error = f"object is {object_type!r}, not a commit"
    return False, error or "commit object is unavailable"


def candidate_repositories(
    skill_root: Path,
    repository_name: str,
    explicit_source_repo: Path | None,
) -> list[Path]:
    if explicit_source_repo is not None:
        return [explicit_source_repo.expanduser().resolve()]

    candidates = [skill_root.resolve(), Path.cwd().resolve()]
    for ancestor in (skill_root, *skill_root.parents):
        candidates.append((ancestor / repository_name).resolve())

    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def locate_source_repository(
    skill_root: Path,
    repository_name: str,
    commit: str,
    explicit_source_repo: Path | None,
) -> tuple[Path | None, list[dict[str, Any]]]:
    probes: list[dict[str, Any]] = []
    for candidate in candidate_repositories(
        skill_root, repository_name, explicit_source_repo
    ):
        if not candidate.exists():
            probes.append(
                {"path": str(candidate), "commit_available": False, "reason": "path does not exist"}
            )
            continue
        available, reason = git_commit_available(candidate, commit)
        probe: dict[str, Any] = {
            "path": str(candidate),
            "commit_available": available,
        }
        if reason:
            probe["reason"] = reason
        probes.append(probe)
        if available:
            return candidate, probes
    return None, probes


def git_tree_paths(repo: Path, commit: str) -> list[str]:
    result = run_git(repo, "ls-tree", "-r", "-z", "--name-only", commit)
    if result.returncode != 0:
        error = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(error or "git ls-tree failed")
    return [
        part.decode("utf-8", errors="surrogateescape")
        for part in result.stdout.split(b"\0")
        if part
    ]


def git_blob(repo: Path, commit: str, source_path: str) -> bytes:
    result = run_git(repo, "show", f"{commit}:{source_path}")
    if result.returncode != 0:
        error = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(error or f"cannot read source blob {source_path}")
    return result.stdout


def empty_report(skill_root: Path) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "status": "FAIL",
        "ok": False,
        "verifiable": False,
        "skill_root": str(skill_root.resolve()),
        "source": {},
        "normalization": "CRLF and CR are normalized to LF; all other bytes are compared",
        "patch_fingerprint": (
            "SHA-256 of each normalized source and runtime endpoint plus a canonical "
            "binary-safe unified diff labeled source/runtime"
        ),
        "summary": {},
        "declared_patches": [],
        "files": [],
        "source_exclusions": [],
        "errors": [],
    }


def build_report(skill_root: Path, explicit_source_repo: Path | None = None) -> dict[str, Any]:
    skill_root = skill_root.expanduser().resolve()
    report = empty_report(skill_root)
    manifest_path = skill_root / "skill-manifest.json"
    runtime_root = skill_root / "runtime"

    if not manifest_path.is_file():
        report["errors"].append("skill-manifest.json is missing")
        return report
    if not runtime_root.is_dir():
        report["errors"].append("runtime directory is missing")
        return report

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        report["errors"].append(f"cannot read skill-manifest.json: {exc}")
        return report

    source_manifest = manifest.get("source")
    runtime_manifest = manifest.get("runtime")
    if not isinstance(source_manifest, dict) or not isinstance(runtime_manifest, dict):
        report["errors"].append("manifest source/runtime sections must be objects")
        return report

    repository_name = str(source_manifest.get("repository") or "").strip()
    commit = str(source_manifest.get("commit") or "").strip()
    declared_patches = runtime_manifest.get("local_patches")
    if not repository_name:
        report["errors"].append("manifest source.repository is missing")
    if not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", commit):
        report["errors"].append("manifest source.commit is not a full Git object ID")
    if not isinstance(declared_patches, list) or not all(
        isinstance(item, str) and item.strip() for item in declared_patches
    ):
        report["errors"].append("manifest runtime.local_patches must be a list of names")
    if report["errors"]:
        return report

    declared_names = {item.strip() for item in declared_patches}
    if len(declared_names) != len(declared_patches):
        report["errors"].append("manifest runtime.local_patches contains duplicates")
    known_names = set(DECLARED_PORTABILITY_PATCHES.values()) | NON_RUNTIME_PATCHES
    for name in sorted(declared_names - known_names):
        report["errors"].append(f"declared patch has no source-mapping rule: {name}")
    for name in sorted(known_names - declared_names):
        report["errors"].append(f"source-mapping rule is not declared in the manifest: {name}")
    report["errors"].extend(patch_fingerprint_configuration_errors())
    if report["errors"]:
        return report

    source_repo, probes = locate_source_repository(
        skill_root, repository_name, commit, explicit_source_repo
    )
    report["source"] = {
        "repository": repository_name,
        "branch": source_manifest.get("branch"),
        "commit": commit,
        "commit_available": source_repo is not None,
        "git_repository": str(source_repo) if source_repo else None,
        "probes": probes,
    }
    if source_repo is None:
        report.update(
            {
                "status": "SKIPPED",
                "ok": None,
                "verifiable": False,
                "skip_reason": (
                    "recorded source commit is not available in any local Git repository; "
                    "no network fetch was attempted"
                ),
            }
        )
        return report

    try:
        source_tree = set(git_tree_paths(source_repo, commit))
    except RuntimeError as exc:
        report["errors"].append(f"cannot enumerate source commit: {exc}")
        return report

    source_relevant = relevant_source_paths(source_tree)
    source_expected = {
        path for path in source_relevant if not intentionally_excluded_source_path(path)
    }
    excluded_source = sorted(source_relevant - source_expected)
    report["source_exclusions"] = [
        {
            "source_path": path,
            "classification": "source-only-excluded",
            "reason": (
                "container deployment file"
                if path == "backend/Dockerfile"
                else "main-repository backend test"
            ),
        }
        for path in excluded_source
    ]

    mapped_source_paths: set[str] = set()
    classifications = {
        "normalized-identical": 0,
        "declared-portability-patch": 0,
        "declared-portability-patch-verified": 0,
        "declared-portability-patch-failed": 0,
        "local-method": 0,
        "unexpected": 0,
    }

    for path in runtime_files(runtime_root):
        runtime_path = path.relative_to(runtime_root).as_posix()
        runtime_data = path.read_bytes()
        source_path = source_path_for_runtime(runtime_path)
        entry: dict[str, Any] = {
            "runtime_path": runtime_path,
            "source_path": source_path,
            "runtime_sha256": sha256(runtime_data),
            "runtime_bytes": len(runtime_data),
        }

        if runtime_path in LOCAL_METHOD_FILES:
            entry["classification"] = "local-method"
            entry["valid"] = bool(runtime_data.strip())
            if not entry["valid"]:
                report["errors"].append(f"local method is empty: {runtime_path}")
            classifications["local-method"] += 1
            report["files"].append(entry)
            continue

        if source_path is None:
            entry.update({"classification": "unexpected", "valid": False})
            classifications["unexpected"] += 1
            report["errors"].append(f"runtime path has no source-mapping rule: {runtime_path}")
            report["files"].append(entry)
            continue

        mapped_source_paths.add(source_path)
        if source_path not in source_tree:
            entry.update({"classification": "unexpected", "valid": False})
            classifications["unexpected"] += 1
            report["errors"].append(
                f"mapped source path is missing at {commit}: {source_path}"
            )
            report["files"].append(entry)
            continue

        try:
            source_data = git_blob(source_repo, commit, source_path)
        except RuntimeError as exc:
            entry.update({"classification": "unexpected", "valid": False})
            classifications["unexpected"] += 1
            report["errors"].append(str(exc))
            report["files"].append(entry)
            continue

        normalized_equal = normalize_line_endings(runtime_data) == normalize_line_endings(source_data)
        entry.update(
            {
                "source_sha256": sha256(source_data),
                "source_bytes": len(source_data),
                "exact_bytes": runtime_data == source_data,
                "normalized_equal": normalized_equal,
            }
        )
        patch_name = DECLARED_PORTABILITY_PATCHES.get(runtime_path)
        if patch_name is not None:
            actual_fingerprints = portability_patch_fingerprints(
                source_data, runtime_data
            )
            expected_fingerprints = EXPECTED_PORTABILITY_PATCH_FINGERPRINTS[
                runtime_path
            ]
            fingerprint_matches = {
                field: actual_fingerprints[field] == expected_fingerprints[field]
                for field in PATCH_FINGERPRINT_FIELDS
            }
            mismatched_fields = [
                field for field in PATCH_FINGERPRINT_FIELDS if not fingerprint_matches[field]
            ]
            valid = (
                patch_name in declared_names
                and not normalized_equal
                and not mismatched_fields
            )
            entry.update(
                {
                    "classification": "declared-portability-patch",
                    "patch": patch_name,
                    **actual_fingerprints,
                    "expected_fingerprints": dict(expected_fingerprints),
                    "fingerprint_matches": fingerprint_matches,
                    "verification_status": "VERIFIED" if valid else "HASH_MISMATCH",
                    "valid": valid,
                }
            )
            classifications["declared-portability-patch"] += 1
            verification_counter = (
                "declared-portability-patch-verified"
                if valid
                else "declared-portability-patch-failed"
            )
            classifications[verification_counter] += 1
            if normalized_equal:
                report["errors"].append(
                    f"declared patch no longer differs from its source blob: {runtime_path}"
                )
            for field in mismatched_fields:
                report["errors"].append(
                    "declared portability patch fingerprint mismatch "
                    f"({field}): {runtime_path}; "
                    f"expected {expected_fingerprints[field]}, "
                    f"actual {actual_fingerprints[field]}"
                )
        elif normalized_equal:
            entry.update({"classification": "normalized-identical", "valid": True})
            classifications["normalized-identical"] += 1
        else:
            entry.update({"classification": "unexpected", "valid": False})
            classifications["unexpected"] += 1
            report["errors"].append(
                f"undeclared runtime difference from source: {runtime_path}"
            )
        report["files"].append(entry)

    present_runtime_paths = {entry["runtime_path"] for entry in report["files"]}
    for method_path in sorted(LOCAL_METHOD_FILES - present_runtime_paths):
        report["errors"].append(f"required local method is missing: {method_path}")
    for patch_path in sorted(set(DECLARED_PORTABILITY_PATCHES) - present_runtime_paths):
        report["errors"].append(f"declared portability patch file is missing: {patch_path}")

    missing_runtime = sorted(source_expected - mapped_source_paths)
    for source_path in missing_runtime:
        report["errors"].append(
            f"source production path is not present in runtime: {source_path}"
        )

    patch_rows: list[dict[str, Any]] = []
    for name in declared_patches:
        matching_paths = sorted(
            path for path, patch_name in DECLARED_PORTABILITY_PATCHES.items() if patch_name == name
        )
        if matching_paths:
            matching_entries = [
                entry
                for entry in report["files"]
                if entry.get("runtime_path") in matching_paths
            ]
            patch_rows.append(
                {
                    "name": name,
                    "scope": "runtime",
                    "runtime_paths": matching_paths,
                    "status": (
                        "VERIFIED"
                        if len(matching_entries) == len(matching_paths)
                        and all(entry.get("valid") for entry in matching_entries)
                        else "FAILED"
                    ),
                }
            )
        else:
            patch_rows.append(
                {
                    "name": name,
                    "scope": "distribution-layer",
                    "runtime_paths": [],
                    "status": "NOT_APPLICABLE_TO_RUNTIME_MAPPING",
                }
            )
    report["declared_patches"] = patch_rows

    report["summary"] = {
        "runtime_files": len(report["files"]),
        "source_mapped_files": len(mapped_source_paths),
        "source_expected_files": len(source_expected),
        "source_excluded_files": len(excluded_source),
        "source_production_paths_missing_from_runtime": len(missing_runtime),
        **classifications,
    }
    report["verifiable"] = True
    report["ok"] = not report["errors"]
    report["status"] = "PASS" if report["ok"] else "FAIL"
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare runtime/ with the source commit in skill-manifest.json without fetching."
        )
    )
    parser.add_argument(
        "--source-repo",
        type=Path,
        help="Local Git worktree or bare repository containing the recorded source commit",
    )
    parser.add_argument(
        "--skill-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help=argparse.SUPPRESS,
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = build_report(args.skill_root, args.source_repo)
    except Exception as exc:  # keep command failures machine-readable
        report = empty_report(args.skill_root)
        report["errors"].append(f"unexpected checker failure: {type(exc).__name__}: {exc}")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] == "PASS":
        return 0
    if report["status"] == "SKIPPED":
        return 3
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
