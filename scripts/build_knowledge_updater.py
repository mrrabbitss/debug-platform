"""Build the small updater; authorized Skill bytes belong only in release assets."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def build(source, output):
    source, output = source.resolve(strict=True), output.resolve()
    if output.is_relative_to(ROOT) and not output.is_relative_to(ROOT / "artifacts"):
        raise ValueError("Release assets inside the repository must stay under ignored artifacts")
    output.parent.mkdir(parents=True, exist_ok=True)
    files = {"hilink-diag.zip": source.read_bytes()}
    for name in ("update_network_skill.py", "offline_skill_update.py"):
        files[name] = (ROOT / "scripts" / name).read_bytes()
    for name in ("knowledge_reset.py", "knowledge_bundle_import.py", "bundled_knowledge.py"):
        files["services/" + name] = (ROOT / "backend/app/services" / name).read_bytes()
    files["Update-Network-Skill.bat"] = (ROOT / "deploy/windows-server/Update-Network-Skill.bat").read_text(encoding="utf-8").replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")
    files["使用说明.txt"] = (ROOT / "docs/offline-network-skill-update-20260910.md").read_bytes()
    manifest = {"schema_version": 1, "version": "2026.09.10.1",
        "supported_versions": ["0.5.1", "0.5.2", "0.5.3", "0.5.4", "0.5.5"],
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
    files["updater-manifest.json"] = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    result = {"path": str(output), "bytes": output.stat().st_size,
              "sha256": hashlib.sha256(output.read_bytes()).hexdigest(), "source_sha256": manifest["files"]["hilink-diag.zip"]}
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.source, args.output)
