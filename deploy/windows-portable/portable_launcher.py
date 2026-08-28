# ruff: noqa: E402 -- disable bytecode before importing copied standard-library modules
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import hashlib
import json
import os
import shutil
import socket
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parent
BACKEND_ROOT = PACKAGE_ROOT / "app" / "backend"
FRONTEND_ROOT = PACKAGE_ROOT / "web"
ENV_EXAMPLE_PATH = PACKAGE_ROOT / ".env.example"
MANIFEST_PATH = PACKAGE_ROOT / "package-manifest.json"
_local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
DEFAULT_STATE_ROOT = (
    Path(_local_app_data) / "GWAPDebugPlatform"
    if _local_app_data
    else PACKAGE_ROOT / ".portable-state"
)
DEFAULT_DATA_ROOT = DEFAULT_STATE_ROOT / "data"
DEFAULT_ENV_PATH = DEFAULT_STATE_ROOT / ".env"

class PortableLayoutError(RuntimeError):
    pass


def _required_paths() -> tuple[Path, ...]:
    return (
        BACKEND_ROOT / "app" / "main.py",
        BACKEND_ROOT / "alembic.ini",
        FRONTEND_ROOT / "index.html",
        ENV_EXAMPLE_PATH,
        MANIFEST_PATH,
    )


def validate_layout() -> None:
    missing = [path for path in _required_paths() if not path.is_file()]
    if missing:
        details = "\n".join(f"  - {path}" for path in missing)
        raise PortableLayoutError(
            "The portable package is incomplete. Missing required files:\n" + details
        )


def verify_package_manifest() -> None:
    try:
        payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8-sig"))
        entries = payload["files"]
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise PortableLayoutError("The package integrity manifest is invalid.") from exc
    if payload.get("schema_version") != 1 or not isinstance(entries, list):
        raise PortableLayoutError("The package integrity manifest schema is unsupported.")

    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise PortableLayoutError("The package integrity manifest has an invalid entry.")
        relative = str(entry.get("path") or "")
        expected_hash = str(entry.get("sha256") or "").lower()
        expected_size = entry.get("size")
        relative_path = Path(relative)
        if (
            not relative
            or relative in seen
            or relative_path.is_absolute()
            or ".." in relative_path.parts
            or len(expected_hash) != 64
            or not isinstance(expected_size, int)
        ):
            raise PortableLayoutError(
                f"The package integrity manifest contains an unsafe entry: {relative!r}"
            )
        seen.add(relative)
        target = (PACKAGE_ROOT / relative_path).resolve()
        try:
            target.relative_to(PACKAGE_ROOT)
        except ValueError as exc:
            raise PortableLayoutError(
                f"The package integrity manifest escapes the package: {relative}"
            ) from exc
        if not target.is_file() or target.stat().st_size != expected_size:
            raise PortableLayoutError(f"Package file is missing or incomplete: {relative}")
        digest = hashlib.sha256()
        with target.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != expected_hash:
            raise PortableLayoutError(f"Package file integrity check failed: {relative}")

    actual = {
        path.relative_to(PACKAGE_ROOT).as_posix()
        for path in PACKAGE_ROOT.rglob("*")
        if path.is_file() and path != MANIFEST_PATH
    }
    unexpected = sorted(actual - seen)
    if unexpected:
        raise PortableLayoutError(
            f"Unexpected file is present in the package: {unexpected[0]}"
        )


def ensure_local_environment(data_root: Path, env_path: Path) -> tuple[Path, Path]:
    data_root = data_root.expanduser().resolve()
    env_path = env_path.expanduser().resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    (data_root / "storage").mkdir(parents=True, exist_ok=True)
    env_path.parent.mkdir(parents=True, exist_ok=True)
    if not env_path.exists():
        shutil.copyfile(ENV_EXAMPLE_PATH, env_path)

    database_path = (data_root / "gw_ap_debug.db").resolve()
    storage_path = (data_root / "storage").resolve()
    os.environ["DEBUG_PLATFORM_ENV_FILE"] = str(env_path)
    os.environ["DATABASE_URL"] = f"sqlite:///{database_path.as_posix()}"
    os.environ["DATA_ROOT"] = str(data_root)
    os.environ["STORAGE_ROOT"] = str(storage_path)
    os.environ["STATIC_FRONTEND_ROOT"] = str(FRONTEND_ROOT.resolve())
    os.environ["MODEL_DISABLE_IN_PROCESS_LOCAL"] = "true"
    os.environ["PYTHONUTF8"] = "1"

    backend = str(BACKEND_ROOT.resolve())
    if backend not in sys.path:
        sys.path.insert(0, backend)
    return data_root, env_path


def check_port_available(host: str, port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError as exc:
            raise PortableLayoutError(
                f"Port {port} is already in use. Close the other service or run "
                f"start.bat --port <another-port>. ({exc})"
            ) from exc


def run_self_check(data_root: Path, env_path: Path) -> None:
    validate_layout()
    verify_package_manifest()
    data_root, _ = ensure_local_environment(data_root, env_path)

    marker = data_root / ".portable-write-test"
    marker.write_text("ok", encoding="utf-8")
    marker.unlink()

    import importlib.util

    if importlib.util.find_spec("fastapi") is None:
        raise PortableLayoutError("FastAPI is missing from the bundled Python runtime.")
    if importlib.util.find_spec("uvicorn") is None:
        raise PortableLayoutError("Uvicorn is missing from the bundled Python runtime.")
    if not sys.flags.isolated or not sys.flags.no_user_site:
        raise PortableLayoutError(
            "The bundled Python runtime is not isolated from global/user packages."
        )
    if importlib.util.find_spec("torch") is not None:
        raise PortableLayoutError(
            "The platform portable runtime must not include Torch. "
            "Local model runtimes must be deployed separately."
        )
    if importlib.util.find_spec("sentence_transformers") is not None:
        raise PortableLayoutError(
            "The platform portable runtime must not include sentence-transformers."
        )

    from app.main import app

    if app.title != "GW/AP Intelligent Debug Platform":
        raise PortableLayoutError("The bundled backend application could not be loaded.")

    print("[OK] Portable package layout is complete.")
    print("[OK] Package file sizes and SHA-256 hashes are valid.")
    print("[OK] Bundled backend and frontend are loadable.")
    print("[OK] Bundled Python is isolated from global and user packages.")
    print("[OK] Torch and sentence-transformers are isolated from the platform runtime.")
    print(f"[OK] Writable data directory: {data_root}")


def _open_browser_when_ready(host: str, port: int, timeout_seconds: int = 90) -> None:
    health_url = f"http://{host}:{port}/api/v1/health/ready"
    app_url = f"http://{host}:{port}/"
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(health_url, timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if response.status == 200 and payload.get("ready") is True:
                webbrowser.open(app_url)
                return
        except (OSError, ValueError, urllib.error.URLError):
            pass
        time.sleep(0.5)
    print(f"[WARN] The browser was not opened automatically. Visit {app_url}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Start the self-contained GW/AP Debug Platform package."
    )
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_PATH)
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("--port must be between 1024 and 65535")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        validate_layout()
        data_root, env_path = ensure_local_environment(args.data_root, args.env_file)
        if args.check:
            run_self_check(data_root, env_path)
            return 0

        host = "127.0.0.1"
        check_port_available(host, args.port)
        os.environ["CORS_ORIGINS"] = (
            f"http://{host}:{args.port},http://localhost:{args.port}"
        )

        if not args.no_browser:
            threading.Thread(
                target=_open_browser_when_ready,
                args=(host, args.port),
                daemon=True,
            ).start()

        import uvicorn

        print(f"[INFO] Data directory: {data_root}")
        print(f"[INFO] Open http://{host}:{args.port}/")
        print("[INFO] Press Ctrl+C to stop the platform.")
        uvicorn.run(
            "app.main:app",
            host=host,
            port=args.port,
            reload=False,
            access_log=True,
        )
        return 0
    except (OSError, PortableLayoutError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    if not sys.flags.no_user_site:
        os.execv(
            sys.executable,
            [
                sys.executable,
                "-B",
                "-s",
                str(Path(__file__).resolve()),
                *sys.argv[1:],
            ],
        )
    raise SystemExit(main())
