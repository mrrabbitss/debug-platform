import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_settings


class GitRepositoryError(ValueError):
    pass


@dataclass(frozen=True)
class GitImportManifest:
    root: Path
    file_count: int
    total_bytes: int
    branch: str | None
    commit_hash: str | None


def _git_executable() -> str:
    executable = shutil.which("git")
    if not executable:
        raise GitRepositoryError("Git executable was not found")
    return executable


def safe_git_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.update({
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_LFS_SKIP_SMUDGE": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_PAGER": "cat",
        "PAGER": "cat",
    })
    return environment


def run_git(
    arguments: list[str],
    *,
    cwd: Path,
    timeout: int = 180,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [
            _git_executable(),
            "-c",
            f"core.hooksPath={os.devnull}",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.untrackedCache=false",
            *arguments,
        ],
        cwd=cwd,
        env=safe_git_environment(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        shell=False,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown Git error").strip()[:2000]
        raise GitRepositoryError(f"Git command failed ({result.returncode}): {detail}")
    return result


def find_git_worktree_root(root: Path) -> Path | None:
    resolved = root.resolve()
    direct = resolved / ".git"
    if direct.is_dir() and not direct.is_symlink():
        return resolved
    candidates: list[Path] = []
    for candidate in resolved.glob("*"):
        if (
            candidate.is_dir()
            and not candidate.is_symlink()
            and (candidate / ".git").is_dir()
            and not (candidate / ".git").is_symlink()
        ):
            candidates.append(candidate.resolve())
    return sorted(candidates, key=lambda item: len(item.parts))[0] if candidates else None


def validate_git_worktree(root: Path) -> None:
    resolved = root.resolve()
    git_dir = resolved / ".git"
    if not git_dir.is_dir() or git_dir.is_symlink():
        raise GitRepositoryError("A normal in-tree .git directory is required")
    alternates = git_dir / "objects" / "info" / "alternates"
    if alternates.exists():
        raise GitRepositoryError("Git object alternates are not allowed")
    for path in resolved.rglob("*"):
        if path.is_symlink():
            raise GitRepositoryError(f"Symbolic links are not allowed in imported repositories: {path.name}")


def _validate_repository_limits(root: Path) -> tuple[int, int]:
    settings = get_settings()
    count = 0
    total = 0
    for path in root.rglob("*"):
        if path.is_symlink():
            raise GitRepositoryError(f"Symbolic links are not allowed: {path.name}")
        if not path.is_file():
            continue
        size = path.stat().st_size
        count += 1
        total += size
        if count > settings.max_archive_files:
            raise GitRepositoryError("Git repository contains too many files")
        if size > settings.max_single_file_bytes:
            raise GitRepositoryError(f"Git repository file exceeds the per-file limit: {path.name}")
        if total > settings.max_extracted_bytes:
            raise GitRepositoryError("Git repository exceeds the extracted-size limit")
    return count, total


def repository_head(root: Path) -> tuple[str | None, str | None]:
    branch_result = run_git(
        ["branch", "--show-current"],
        cwd=root,
        timeout=30,
        check=False,
    )
    hash_result = run_git(
        ["rev-parse", "HEAD"],
        cwd=root,
        timeout=30,
        check=False,
    )
    branch = branch_result.stdout.strip() if branch_result.returncode == 0 else ""
    commit_hash = hash_result.stdout.strip() if hash_result.returncode == 0 else ""
    return branch or None, commit_hash or None


def clone_git_bundle(bundle_path: Path, destination: Path) -> GitImportManifest:
    bundle = bundle_path.resolve()
    target = destination.resolve()
    if not bundle.is_file():
        raise GitRepositoryError("Git Bundle file was not found")
    verification = run_git(
        ["bundle", "list-heads", str(bundle)],
        cwd=bundle.parent,
        timeout=120,
        check=False,
    )
    if verification.returncode != 0 or not verification.stdout.strip():
        detail = (verification.stderr or verification.stdout or "invalid bundle").strip()[:2000]
        raise GitRepositoryError(f"Invalid Git Bundle: {detail}")
    if target.exists() and any(target.iterdir()):
        raise GitRepositoryError("Git Bundle destination is not empty")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        run_git(
            [
                "-c",
                f"core.hooksPath={os.devnull}",
                "clone",
                "--no-local",
                "--no-hardlinks",
                str(bundle),
                str(target),
            ],
            cwd=target.parent,
            timeout=600,
        )
    except GitRepositoryError as exc:
        raise GitRepositoryError(f"Invalid or incomplete Git Bundle: {exc}") from exc
    validate_git_worktree(target)
    file_count, total_bytes = _validate_repository_limits(target)
    branch, commit_hash = repository_head(target)
    return GitImportManifest(
        root=target,
        file_count=file_count,
        total_bytes=total_bytes,
        branch=branch,
        commit_hash=commit_hash,
    )
