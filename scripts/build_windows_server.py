"""Layer pinned LAN services onto a verified freshly built portable package."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import urllib.request
import zipfile
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
SERVER_INSTALLER_HELPERS = ("publish_server_payload.ps1",)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def stage_knowledge_bundle(source: Path, target: Path) -> dict:
    """Bind every approved source byte to the installer manifest; never use a DB export."""
    sys.path.insert(0, str(ROOT / "backend"))
    from app.services.bundled_knowledge import BUNDLE_ID
    from app.services.knowledge_reset import read_bundle
    bundle = read_bundle(source.resolve())
    folder = target / "bundled-knowledge"
    folder.mkdir()
    (folder / "hilink-diag.zip").write_bytes(bundle["raw"])
    with zipfile.ZipFile(source) as archive:
        for item in bundle["files"]:
            destination = folder / item["path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            raw = archive.read(item["path"])
            if hashlib.sha256(raw).hexdigest() != item["source_sha256"]:
                raise ValueError("Skill source changed during packaging")
            destination.write_bytes(raw)
    manifest = {"schema_version": 1, "bundle_id": BUNDLE_ID, "archive": "hilink-diag.zip",
                "approval_origin": "INSTALLER_DEFAULT", "source_sha256": bundle["source_sha256"],
                "files": [{key: item[key] for key in ("path", "bytes", "source_sha256", "role")}
                          for item in bundle["manifest"]]}
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def build(portable: Path, output: Path, cache: Path, deployment_config: Path | None = None,
          knowledge_bundle: Path | None = None) -> dict:
    deployment = None
    if deployment_config:
        deployment = json.loads(deployment_config.read_text(encoding="utf-8-sig"))
        uri = urlsplit(deployment.get("server_url", ""))
        if (set(deployment) != {"schema_version", "server_url", "simple_engineer_login"}
                or deployment["schema_version"] != 1 or type(deployment["simple_engineer_login"]) is not bool
                or uri.scheme != "https" or not uri.hostname or uri.username or uri.password
                or uri.path or uri.query or uri.fragment):
            raise ValueError("Invalid deployment profile")
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
        if (path.is_file() and path.suffix != ".iss"
                and path.name not in SERVER_INSTALLER_HELPERS):
            shutil.copyfile(path, target / path.name)
    for filename in SERVER_INSTALLER_HELPERS:
        shutil.copyfile(ROOT / "deploy/windows-server" / filename, target / filename)
    shutil.copyfile(ROOT / "scripts/run_lan_server.py", target / "scripts/run_lan_server.py")
    shutil.copyfile(ROOT / "deploy/windows-server/start_server.bat", target / "start.bat")
    shutil.copyfile(ROOT / "docs/服务器使用指南.md", target / "服务器使用指南.md")
    shutil.copyfile(ROOT / "scripts/file_hash.ps1", target / "file_hash.ps1")
    if deployment is not None:
        (target / "deployment.json").write_text(json.dumps(deployment, indent=2), encoding="utf-8")
    knowledge = stage_knowledge_bundle(knowledge_bundle, target) if knowledge_bundle else None
    entries = [{"path": path.relative_to(target).as_posix(), "size": path.stat().st_size, "sha256": digest(path)}
               for path in sorted(target.rglob("*")) if path.is_file() and path != target / "package-manifest.json"]
    manifest = json.loads((target / "package-manifest.json").read_text(encoding="utf-8-sig"))
    manifest["files"] = entries
    manifest["lan_services"] = {"caddy": lock["caddy"]["version"], "winsw": lock["winsw"]["version"],
                                "profile": "i7-14700-32gb-pilot", "windows_services_tested": False}
    if knowledge:
        manifest["bundled_knowledge"] = knowledge
    (target / "package-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    subprocess.run([str(target / "runtime/python/python.exe"), "-B", "-s", "-c",
                    "import sys; sys.path.insert(0,'.'); import portable_launcher as p; p.validate_layout(); p.verify_package_manifest()"], cwd=target, check=True)
    return {"directory": str(target), "files": len(entries), "runtime_hashes_verified": True,
            "bundled_skill_files": len(knowledge["files"]) if knowledge else 0,
            "notice": "Package integrity is not server acceptance; run LAN/GGUF verification before distribution."}


def compile_installer(package: Path, output: Path, compiler: Path) -> dict:
    """Compile a complete offline EXE using the existing atomic payload publisher."""
    output.mkdir(parents=True, exist_ok=True)
    info = json.loads((package / "build-info.json").read_text(encoding="utf-8-sig"))
    version = info["package_version"]
    installer = output / f"GWAP-Debug-Server-Setup-{version}-x64.exe"
    if installer.exists():
        raise ValueError("Installer already exists; choose a new output directory")
    subprocess.run([str(compiler.resolve()), "/Q", f"/DSourceRoot={package.resolve()}",
                    f"/DOutputRoot={output.resolve()}", f"/DAppVersion={version}",
                    str(ROOT / "deploy/windows-server/ServerInstaller.iss")], check=True)
    checksum = digest(installer)
    (output / "SHA256.txt").write_text(f"{checksum}  {installer.name}\n", encoding="ascii")
    shutil.copyfile(ROOT / "docs/服务器使用指南.md", output / "服务器使用指南.md")
    return {"installer": str(installer.resolve()), "sha256": checksum,
            "bytes": installer.stat().st_size}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--portable", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, default=ROOT / "artifacts/build-cache/lan-runtime")
    parser.add_argument("--installer-output", type=Path)
    parser.add_argument("--iscc", type=Path)
    parser.add_argument("--deployment-config", type=Path, help="Local deployment profile; never commit internal server addresses")
    parser.add_argument("--knowledge-bundle", type=Path, help="Release-owner-approved complete Skill ZIP to initialize empty servers")
    arguments = parser.parse_args()
    if arguments.installer_output and not arguments.iscc:
        parser.error("--installer-output requires --iscc")
    result = build(arguments.portable, arguments.output, arguments.cache, arguments.deployment_config, arguments.knowledge_bundle)
    if arguments.installer_output:
        result.update(compile_installer(arguments.output, arguments.installer_output, arguments.iscc))
    print(json.dumps(result, indent=2))
