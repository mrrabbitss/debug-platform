"""Layer pinned LAN services onto a verified freshly built portable package."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def build(portable: Path, output: Path, cache: Path) -> dict:
    portable = portable.resolve()
    python = portable / "runtime/python/python.exe"
    # Source manifest verification runs without starting backend or reading user data.
    subprocess.run([str(python), "-B", "-s", "-c",
                    "import sys; sys.path.insert(0,'.'); import portable_launcher as p; p.validate_layout(); p.verify_package_manifest()"],
                   cwd=portable, check=True)
    lock = json.loads((ROOT / "deploy/windows-server/runtime.lock.json").read_text())
    cache.mkdir(parents=True, exist_ok=True)
    downloads = {}
    for name in ("caddy", "winsw"):
        item = lock[name]
        path = cache / item["url"].rsplit("/", 1)[-1]
        if not path.exists():
            partial = path.with_suffix(path.suffix + ".partial")
            with urllib.request.urlopen(item["url"], timeout=120) as response, partial.open("xb") as stream:
                shutil.copyfileobj(response, stream)
            if digest(partial) != item["sha256"]:
                raise ValueError(f"{name} download hash mismatch; partial file retained for inspection")
            partial.replace(path)
        if digest(path) != item["sha256"]:
            raise ValueError(f"{name} runtime hash mismatch")
        downloads[name] = path
    target = output.resolve()
    if target.exists():
        raise ValueError("Server output already exists; choose a new version directory")
    shutil.copytree(portable, target)
    runtime = target / "server-runtime"
    runtime.mkdir()
    with zipfile.ZipFile(downloads["caddy"]) as archive:
        for name in ("caddy.exe", "LICENSE"):
            (runtime / name).write_bytes(archive.read(name))
    shutil.copyfile(downloads["winsw"], runtime / "WinSW-x64.exe")
    for path in (ROOT / "deploy/windows-server").iterdir():
        if path.is_file():
            shutil.copyfile(path, target / path.name)
    shutil.copyfile(ROOT / "scripts/file_hash.ps1", target / "file_hash.ps1")
    entries = [{"path": path.relative_to(target).as_posix(), "size": path.stat().st_size, "sha256": digest(path)}
               for path in sorted(target.rglob("*")) if path.is_file() and path != target / "package-manifest.json"]
    manifest = json.loads((target / "package-manifest.json").read_text(encoding="utf-8-sig"))
    manifest["files"] = entries
    manifest["lan_services"] = {"caddy": lock["caddy"]["version"], "winsw": lock["winsw"]["version"],
                                "profile": "i7-14700-32gb-pilot", "windows_services_tested": False}
    (target / "package-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    subprocess.run([str(target / "runtime/python/python.exe"), "-B", "-s", "-c",
                    "import sys; sys.path.insert(0,'.'); import portable_launcher as p; p.validate_layout(); p.verify_package_manifest()"], cwd=target, check=True)
    return {"directory": str(target), "files": len(entries), "runtime_hashes_verified": True,
            "notice": "Package integrity is not server acceptance; run LAN/GGUF verification before distribution."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--portable", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, default=ROOT / "artifacts/build-cache/lan-runtime")
    arguments = parser.parse_args()
    print(json.dumps(build(arguments.portable, arguments.output, arguments.cache), indent=2))
