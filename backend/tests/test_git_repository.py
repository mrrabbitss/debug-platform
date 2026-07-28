import shutil
from pathlib import Path

import pytest

from app.services.git_repository import (
    GitRepositoryError,
    clone_git_bundle,
    repository_head,
    run_git,
)


@pytest.mark.skipif(shutil.which("git") is None, reason="Git is not installed")
def test_git_bundle_import_preserves_history_without_running_repository_code(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    run_git(["init"], cwd=source)
    run_git(["config", "user.name", "Bundle Test"], cwd=source)
    run_git(["config", "user.email", "bundle@example.invalid"], cwd=source)
    (source / "main.py").write_text("print('first')\n", encoding="utf-8")
    run_git(["add", "main.py"], cwd=source)
    run_git(["commit", "-m", "initial version"], cwd=source)
    (source / "main.py").write_text("print('second')\n", encoding="utf-8")
    run_git(["add", "main.py"], cwd=source)
    run_git(["commit", "-m", "fix startup regression"], cwd=source)

    bundle = tmp_path / "repository.bundle"
    run_git(["bundle", "create", str(bundle), "--all"], cwd=source)
    destination = tmp_path / "imported"
    destination.mkdir()

    imported = clone_git_bundle(bundle, destination)
    assert imported.root == destination.resolve()
    assert imported.file_count > 0
    assert imported.total_bytes > 0
    assert imported.commit_hash
    assert (destination / "main.py").read_text(encoding="utf-8") == "print('second')\n"
    commit_count = run_git(
        ["rev-list", "--count", "--all"],
        cwd=destination,
    )
    assert commit_count.stdout.strip() == "2"
    assert repository_head(destination)[1] == imported.commit_hash


@pytest.mark.skipif(shutil.which("git") is None, reason="Git is not installed")
def test_git_bundle_import_rejects_invalid_input(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.bundle"
    invalid.write_text("not a git bundle", encoding="utf-8")
    destination = tmp_path / "destination"
    destination.mkdir()

    with pytest.raises(GitRepositoryError, match="Invalid Git Bundle"):
        clone_git_bundle(invalid, destination)
