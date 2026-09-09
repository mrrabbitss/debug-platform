"""Capture committed history and the current source tree without touching the index.

Generated archives stay under the ignored artifacts directory. Runtime data,
ignored files, and unrelated untracked root documents are deliberately excluded.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import zipfile
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UNTRACKED_SOURCE_ROOTS = ("backend/app/", "backend/tests/", "frontend/src/", "frontend/e2e/",
                          "docs/", "deploy/", "scripts/", "agent-skills/", "workflow/")


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=ROOT)


def main() -> None:
    target = ROOT / "artifacts" / "baselines" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target.mkdir(parents=True, exist_ok=False)
    tracked = set(git("ls-files", "-z").decode("utf-8").split("\0")) - {""}
    untracked = set(git("ls-files", "--others", "--exclude-standard", "-z").decode("utf-8").split("\0")) - {""}
    included = tracked | {name for name in untracked if name.startswith(UNTRACKED_SOURCE_ROOTS)}
    if ".codex/config.toml" in untracked:
        included.add(".codex/config.toml")
    entries = []
    archive = target / "source.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        for name in sorted(included):
            path = ROOT / name
            if not path.is_file() or path.is_symlink():
                continue
            content = path.read_bytes()
            output.writestr(name, content)
            entries.append({"path": name, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()})
    with zipfile.ZipFile(archive) as check:
        for entry in entries:
            if hashlib.sha256(check.read(entry["path"])).hexdigest() != entry["sha256"]:
                raise RuntimeError(f"Baseline verification failed: {entry['path']}")
    subprocess.run(["git", "bundle", "create", str(target / "history.bundle"), "HEAD"], cwd=ROOT, check=True)
    git("diff", "--binary", f"--output={target / 'tracked.patch'}", "HEAD")
    manifest = {
        "schema_version": 1,
        "head": git("rev-parse", "HEAD").decode().strip(),
        "branch": git("branch", "--show-current").decode().strip(),
        "status": git("status", "--short").decode("utf-8"),
        "excluded_untracked": sorted(untracked - included),
        "source_archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "files": entries,
        "restore": "Extract source.zip into a NEW directory; use history.bundle for Git history. Runtime data is not included.",
    }
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "directory": str(target), "files": len(entries), "head": manifest["head"]}))


if __name__ == "__main__":
    main()
