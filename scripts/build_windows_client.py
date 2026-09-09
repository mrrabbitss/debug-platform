"""Build the PowerShell-only remote connector; never copy user configuration."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ("start_codeagent.ps1", "codeagent_launcher_support.ps1", "codeagent_launcher_http.ps1", "file_hash.ps1",
           "install_agent_skill_mcp.ps1", "install_agent_skill_mcp.bat")


def build(destination: Path, deployment_config: Path | None = None) -> dict:
    sources: dict[str, Path] = {}
    sources["分机使用指南.md"] = ROOT / "docs/分机使用指南.md"
    if deployment_config:
        deployment = json.loads(deployment_config.read_text(encoding="utf-8-sig"))
        uri = urlsplit(deployment.get("server_url", ""))
        if (set(deployment) != {"schema_version", "server_url", "simple_engineer_login"}
                or deployment["schema_version"] != 1 or type(deployment["simple_engineer_login"]) is not bool
                or uri.scheme != "https" or not uri.hostname or uri.username or uri.password
                or uri.path or uri.query or uri.fragment):
            raise ValueError("Invalid deployment profile")
        sources["deployment.json"] = deployment_config
    for source in (ROOT / "deploy/windows-client").rglob("*"):
        if source.is_file():
            sources[source.relative_to(ROOT / "deploy/windows-client").as_posix()] = source
    for name in SCRIPTS:
        sources[f"scripts/{name}"] = ROOT / "scripts" / name
    for source in (ROOT / "agent-skills/gw-ap-debug").rglob("*"):
        if source.is_file() and "__pycache__" not in source.parts and source.suffix != ".pyc":
            sources[source.relative_to(ROOT).as_posix()] = source
    entries = [{"path": name, "size": source.stat().st_size,
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest()} for name, source in sorted(sources.items())]
    package_id = hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()[:16]
    target = destination.resolve() / f"GWAP-Client-{package_id}"
    target.mkdir(parents=True, exist_ok=False)
    for name, source in sources.items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, path)
    manifest = {"schema_version": 1, "package_id": package_id, "client_contract_version": "1.0",
                "backend_bundled": False, "models_bundled": False, "files": entries}
    (target / "client-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    archive_path = target.with_suffix(".zip")
    with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for source in target.rglob("*"):
            if source.is_file():
                archive.write(source, source.relative_to(target.parent).as_posix())
    return {"directory": str(target), "archive": str(archive_path), "files": len(entries),
            "archive_bytes": archive_path.stat().st_size, "package_id": package_id}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/lan/client")
    parser.add_argument("--deployment-config", type=Path, help="Local deployment profile; never commit internal server addresses")
    args = parser.parse_args()
    print(json.dumps(build(args.output, args.deployment_config), indent=2))
