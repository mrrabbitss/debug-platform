#!/usr/bin/env python3
"""Verify the immutable source/runtime identity recorded for this Skill."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys


EXCLUDED_PARTS = {".venv", "__pycache__", "data", "artifacts"}


def runtime_tree_sha256(runtime_root: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    count = 0
    for path in sorted(runtime_root.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file() or any(part in EXCLUDED_PARTS for part in path.relative_to(runtime_root).parts):
            continue
        relative = path.relative_to(runtime_root).as_posix()
        file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_hash.encode("ascii"))
        digest.update(b"\n")
        count += 1
    return digest.hexdigest(), count


def main() -> int:
    skill_root = Path(__file__).resolve().parents[1]
    manifest_path = skill_root / "skill-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    actual_hash, actual_count = runtime_tree_sha256(skill_root / "runtime")
    expected_hash = str(manifest.get("runtime", {}).get("tree_sha256") or "")
    expected_count = int(manifest.get("runtime", {}).get("file_count") or 0)
    ok = actual_hash == expected_hash and actual_count == expected_count
    print(json.dumps({
        "ok": ok,
        "runtime_tree_sha256": actual_hash,
        "runtime_file_count": actual_count,
        "expected_tree_sha256": expected_hash,
        "expected_file_count": expected_count,
        "source_commit": manifest.get("source", {}).get("commit"),
    }, ensure_ascii=False, indent=2))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
