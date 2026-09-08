"""Apply a narrowly scoped offline fix to a stopped 0.3.3 server package."""

import argparse
from datetime import datetime
import hashlib
import json
import msvcrt
import os
from pathlib import Path
import tempfile

TARGETS = (
    "app/backend/app/services/bundled_embedding_windows.py",
    "app/backend/app/services/retrieval_models.py",
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic_write(path, content):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        temporary.write(content)
        temporary.flush()
        os.fsync(temporary.fileno())
        staging = Path(temporary.name)
    try:
        os.replace(staging, path)
    finally:
        staging.unlink(missing_ok=True)


def apply(app, data_root, payload):
    app = app.resolve()
    manifest_path = app / "package-manifest.json"
    original_manifest = manifest_path.read_bytes()
    manifest = json.loads(original_manifest)
    contract = json.loads((payload / "hotfix.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or set(contract["files"]) != set(TARGETS):
        raise RuntimeError("Unsupported package or hotfix contract")
    entries = {entry["path"]: entry for entry in manifest["files"]}
    updates = []
    for relative in TARGETS:
        target = (app / relative).resolve()
        target.relative_to(app)
        source = (payload / "files" / relative).read_bytes()
        expected = contract["files"][relative]
        if digest(source) != expected["new_sha256"]:
            raise RuntimeError("Hotfix payload hash mismatch: " + relative)
        previous = target.read_bytes() if target.exists() else None
        actual_hash = digest(previous) if previous is not None else None
        if actual_hash not in (expected["old_sha256"], expected["new_sha256"]):
            raise RuntimeError("This fix requires the original 0.3.3 code: " + relative)
        recorded = entries.get(relative, {}).get("sha256")
        if recorded not in (expected["old_sha256"], expected["new_sha256"]):
            raise RuntimeError("Package manifest differs from 0.3.3: " + relative)
        updates.append((target, source, previous))
        entries[relative] = {"path": relative, "size": len(source), "sha256": digest(source)}
    manifest["files"] = [entries[key] for key in sorted(entries)]
    updated_manifest = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if all(old == new for _, new, old in updates) and all(
        entries[key] == next((item for item in json.loads(original_manifest)["files"] if item["path"] == key), None)
        for key in TARGETS
    ):
        print("Already installed. Start GWAP Server.")
        return
    # This is the same byte lock used by the shipped interactive server/backup.
    with (data_root / "script-runner.lock").open("a+b") as lock:
        if os.fstat(lock.fileno()).st_size == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise RuntimeError("Stop GWAP Server with Ctrl+C before applying the fix") from None
        try:
            backup = app.parent / ("embedding-hotfix-backup-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
            backup.mkdir()
            (backup / "package-manifest.json").write_bytes(original_manifest)
            for target, _, previous in updates:
                if previous is not None:
                    (backup / target.name).write_bytes(previous)
            try:
                for target, source, _ in updates:
                    atomic_write(target, source)
                atomic_write(manifest_path, updated_manifest)
                for target, source, _ in updates:
                    if target.read_bytes() != source:
                        raise RuntimeError("Installed file verification failed")
            except BaseException:
                for target, _, previous in reversed(updates):
                    if previous is None:
                        target.unlink(missing_ok=True)
                    else:
                        atomic_write(target, previous)
                atomic_write(manifest_path, original_manifest)
                raise
            print("[OK] Embedding fix installed. Start GWAP Server, then rebuild vectors in Settings.")
            print("Program backup: " + str(backup))
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        apply(args.app, args.data_root, Path(__file__).resolve().parent)
    except Exception as error:
        print("[ERROR] " + str(error))
        raise SystemExit(1)
