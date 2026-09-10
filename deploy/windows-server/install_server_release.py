"""Install one immutable server release without moving any application tree."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def install(payload, target, data):
    payload, target, data = payload.resolve(strict=True), target.absolute(), data.absolute()
    # This installer creates a new leaf only. Never repair/merge an old release.
    if target == payload or target.is_relative_to(payload) or payload.is_relative_to(target):
        raise ValueError("Payload and installed release must be separate directories")
    if target.name in {"app", "", ".", ".."} or target.parent.name != "releases":
        raise ValueError("The server target must be a fresh leaf inside releases")
    if data == target or data.is_relative_to(target):
        raise ValueError("Business data must remain outside the application release")
    for ancestor in (target, *target.parents):
        if ancestor.is_symlink() or ancestor.is_junction():
            raise ValueError("Installed release path must not traverse a link or junction")
    if target.exists() and any(target.iterdir()):
        raise ValueError("Release directory is not empty; the existing program was not replaced")
    subprocess.run([str(payload / "runtime/python/python.exe"), "-B", "-s",
        str(payload / "install_server_knowledge.py"), "--data-root", str(data), "--check-only"],
        cwd=payload, check=True, timeout=120)
    print("[10%] Copying the complete server into its final new release directory.", flush=True)
    shutil.copytree(payload, target, dirs_exist_ok=True)
    python = target / "runtime/python/python.exe"
    environment = {**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    def command(*arguments, timeout=600):
        subprocess.run([str(python), "-B", "-s", *map(str, arguments)],
            cwd=target, env=environment, check=True, timeout=timeout)
    print("[35%] Verifying all copied files and complete runtime.", flush=True)
    with tempfile.TemporaryDirectory(prefix="gwap-release-check-") as isolated:
        check = Path(isolated)
        command(target / "portable_launcher.py", "--check", "--no-browser",
                "--data-root", check / "data", "--env-file", check / ".env")
    print("[50%] Updating the six bundled network Skill files in the existing server.", flush=True)
    command(target / "install_server_knowledge.py", "--data-root", data, timeout=43200)
    print("[100%] Release verified; previous program directories were not moved or overwritten.", flush=True)
    return {"status": "INSTALLED", "package": str(target), "data_root": str(data)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        install(args.payload, args.target, args.data_root)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print("[GWAP_INSTALL_ERROR] " + str(error), flush=True)
        raise SystemExit(1)
