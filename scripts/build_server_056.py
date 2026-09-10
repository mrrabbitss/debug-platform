"""Build the complete 0.5.6 installer from verified 0.5.5 runtime/model bytes."""
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import re

from build_windows_server import compile_installer

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/lan/hotfix-0.5.6"
SOURCE = ROOT / "artifacts/lan/server-hotfix-0.5.5"
TARGET = ROOT / "artifacts/lan/server-hotfix-0.5.6"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def build():
    if TARGET.exists():
        raise ValueError("Choose a new build directory; existing payloads are immutable")
    OUT.mkdir(exist_ok=True)
    shutil.copytree(SOURCE, TARGET)
    replacements = {
        "publish_server_payload.ps1": "deploy/windows-server/publish_server_payload.ps1",
        "install_server_release.ps1": "deploy/windows-server/install_server_release.ps1",
        "install_server_release.py": "deploy/windows-server/install_server_release.py",
        "install_server_knowledge.py": "deploy/windows-server/install_server_knowledge.py",
        "scripts/offline_skill_update.py": "scripts/offline_skill_update.py",
        "app/backend/app/services/knowledge_reset.py": "backend/app/services/knowledge_reset.py",
        "app/backend/app/services/knowledge_bundle_import.py": "backend/app/services/knowledge_bundle_import.py",
        "服务器使用指南.md": "docs/服务器使用指南.md",
    }
    for relative, source in replacements.items():
        shutil.copyfile(ROOT / source, TARGET / relative)
    info = json.loads((TARGET / "build-info.json").read_text(encoding="utf-8-sig"))
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    info.update(package_version="0.5.6", commit=head, source_dirty=bool(subprocess.check_output(
        ["git", "diff", "--name-only", "HEAD"], cwd=ROOT)), built_at_utc=datetime.now(timezone.utc).isoformat(),
        installer_hotfix_from="0.5.5", application_and_models_reused=False, models_and_frontend_reused=True)
    (TARGET / "build-info.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    manifest = json.loads((TARGET / "package-manifest.json").read_text(encoding="utf-8"))
    old = {e["path"]: e for e in manifest["files"]}
    entries = [{"path": p.relative_to(TARGET).as_posix(), "size": p.stat().st_size, "sha256": digest(p)}
               for p in sorted(TARGET.rglob("*")) if p.is_file() and p.name != "package-manifest.json"]
    for entry in entries:
        if entry["path"] not in {*replacements, "build-info.json"}:
            assert old[entry["path"]] == entry, entry["path"]
    manifest["files"] = entries
    assert len(manifest["bundled_knowledge"]["files"]) == 6
    for item in manifest["bundled_knowledge"]["files"]:
        assert digest(TARGET / "bundled-knowledge" / item["path"]) == item["source_sha256"]
    assert digest(TARGET / "bundled-knowledge/hilink-diag.zip") == "c45d0f0be6a4cb0e2b9565c8b88be25437c32c95537aa397366d510009a99d3f"
    (TARGET / "package-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    proof = {"status": "PASS", "files": len(entries), "original_skill_files": 6, "source_commit": head,
             "source_dirty": info["source_dirty"], "replaced": list(replacements), "old_program_rename_required": False}
    (OUT / "package-verification.json").write_text(json.dumps(proof, indent=2), encoding="utf-8")
    print(json.dumps(proof), flush=True)
    result = compile_installer(TARGET, OUT / "delivery", ROOT / "artifacts/build-cache/inno-setup-6.7.1/compiler/ISCC.exe")
    (OUT / "installer-build.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-id", default="")
    args = parser.parse_args()
    if args.build_id:
        if not re.fullmatch(r"[a-z0-9-]+", args.build_id):
            raise ValueError("Invalid build identifier")
        OUT = OUT / args.build_id
        TARGET = TARGET.with_name(TARGET.name + "-" + args.build_id)
    build()
