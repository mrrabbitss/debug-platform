# ruff: noqa: E402 -- disable bytecode before importing copied standard-library modules
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parent
BACKEND_ROOT = PACKAGE_ROOT / "app" / "backend"
FRONTEND_ROOT = PACKAGE_ROOT / "web"
ENV_EXAMPLE_PATH = PACKAGE_ROOT / ".env.example"
MANIFEST_PATH = PACKAGE_ROOT / "package-manifest.json"
MODEL_COMPONENTS_PATH = PACKAGE_ROOT / "model-components.json"
_local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
DEFAULT_STATE_ROOT = (
    Path(_local_app_data) / "GWAPDebugPlatform"
    if _local_app_data
    else PACKAGE_ROOT / ".portable-state"
)
DEFAULT_DATA_ROOT = DEFAULT_STATE_ROOT / "data"
DEFAULT_ENV_PATH = DEFAULT_STATE_ROOT / ".env"
_LOOPBACK_PROXY_BYPASS = ("127.0.0.1", "localhost")
_LOOPBACK_HTTP_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

class PortableLayoutError(RuntimeError):
    pass


@dataclass(frozen=True)
class BundledModelSpec:
    component_id: str
    task_type: str
    executable: Path
    model: Path
    model_name: str
    base_path: str
    health_path: str
    server_arguments: tuple[str, ...]


def _safe_package_file(value: object, label: str) -> Path:
    relative = str(value or "").strip()
    relative_path = Path(relative)
    if not relative or relative_path.is_absolute() or ".." in relative_path.parts:
        raise PortableLayoutError(f"Unsafe {label} path in model-components.json")
    target = (PACKAGE_ROOT / relative_path).resolve()
    try:
        target.relative_to(PACKAGE_ROOT)
    except ValueError as exc:
        raise PortableLayoutError(
            f"The {label} path escapes the package: {relative}"
        ) from exc
    if not target.is_file():
        raise PortableLayoutError(f"Bundled {label} file is missing: {relative}")
    return target


def load_bundled_model_specs() -> tuple[BundledModelSpec, ...]:
    """Load the optional, immutable local-retrieval component contract.

    The package integrity manifest protects this file and all referenced files.
    Runtime arguments are still checked here so a malformed build cannot move a
    sidecar away from loopback or replace the launcher's random API key.
    """
    if not MODEL_COMPONENTS_PATH.is_file():
        return ()
    try:
        payload = json.loads(MODEL_COMPONENTS_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, TypeError, ValueError) as exc:
        raise PortableLayoutError("model-components.json is invalid") from exc
    components = payload.get("components")
    if payload.get("schema_version") != 1 or not isinstance(components, list):
        raise PortableLayoutError("Unsupported model-components.json schema")

    result: list[BundledModelSpec] = []
    seen_ids: set[str] = set()
    seen_tasks: set[str] = set()
    forbidden_arguments = {
        "-m",
        "--model",
        "--host",
        "--port",
        "--api-key",
        "--api-key-file",
    }
    for item in components:
        if not isinstance(item, dict):
            raise PortableLayoutError("Invalid local model component entry")
        component_id = str(item.get("id") or "").strip()
        task_type = str(item.get("task_type") or "").strip()
        model_name = str(item.get("model_name") or "").strip()
        base_path = str(item.get("base_path") or "/v1").strip()
        health_path = str(item.get("health_path") or "/health").strip()
        raw_arguments = item.get("server_arguments") or []
        if (
            not component_id
            or component_id in seen_ids
            or task_type not in {"embedding", "reranker"}
            or task_type in seen_tasks
            or not model_name
            or not isinstance(raw_arguments, list)
            or not base_path.startswith("/")
            or not health_path.startswith("/")
            or any(character.isspace() for character in base_path + health_path)
        ):
            raise PortableLayoutError(
                f"Invalid local model component contract: {component_id or '<unnamed>'}"
            )
        arguments = tuple(str(argument) for argument in raw_arguments)
        for argument in arguments:
            normalized = argument.casefold()
            if (
                not argument
                or "\x00" in argument
                or normalized in forbidden_arguments
                or any(normalized.startswith(prefix + "=") for prefix in forbidden_arguments)
            ):
                raise PortableLayoutError(
                    f"Unsafe server argument in local model component {component_id}"
                )
        result.append(BundledModelSpec(
            component_id=component_id,
            task_type=task_type,
            executable=_safe_package_file(item.get("executable"), "llama.cpp runtime"),
            model=_safe_package_file(item.get("model"), f"{task_type} model"),
            model_name=model_name,
            base_path=base_path.rstrip("/"),
            health_path=health_path,
            server_arguments=arguments,
        ))
        seen_ids.add(component_id)
        seen_tasks.add(task_type)
    return tuple(result)


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _sidecar_environment() -> dict[str, str]:
    """Keep corporate proxies available while forcing sidecars to stay local."""

    environment = dict(os.environ)
    values: list[str] = []
    known: set[str] = set()
    for name, raw_value in environment.items():
        if name.casefold() != "no_proxy":
            continue
        for item in raw_value.split(","):
            cleaned = item.strip()
            if cleaned and cleaned.casefold() not in known:
                values.append(cleaned)
                known.add(cleaned.casefold())
    for host in _LOOPBACK_PROXY_BYPASS:
        if host.casefold() not in known:
            values.append(host)
            known.add(host.casefold())
    bypass = ",".join(values)
    environment["NO_PROXY"] = bypass
    environment["no_proxy"] = bypass
    return environment


def _read_log_tail(path: Path, maximum_bytes: int = 4096) -> str:
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - maximum_bytes))
            return stream.read().decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


class WindowsKillOnCloseJob:
    """Best-effort Win11 Job Object that kills sidecars if the launcher dies."""

    def __init__(self) -> None:
        self._kernel32: Any = None
        self._handle: Any = None
        if os.name != "nt":
            return
        try:
            import ctypes
            from ctypes import wintypes

            class JobBasicLimitInformation(ctypes.Structure):
                _fields_ = [
                    ("PerProcessUserTimeLimit", ctypes.c_longlong),
                    ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD),
                ]

            class IoCounters(ctypes.Structure):
                _fields_ = [
                    ("ReadOperationCount", ctypes.c_ulonglong),
                    ("WriteOperationCount", ctypes.c_ulonglong),
                    ("OtherOperationCount", ctypes.c_ulonglong),
                    ("ReadTransferCount", ctypes.c_ulonglong),
                    ("WriteTransferCount", ctypes.c_ulonglong),
                    ("OtherTransferCount", ctypes.c_ulonglong),
                ]

            class JobExtendedLimitInformation(ctypes.Structure):
                _fields_ = [
                    ("BasicLimitInformation", JobBasicLimitInformation),
                    ("IoInfo", IoCounters),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t),
                ]

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            kernel32.CreateJobObjectW.restype = wintypes.HANDLE
            kernel32.SetInformationJobObject.argtypes = [
                wintypes.HANDLE,
                ctypes.c_int,
                ctypes.c_void_p,
                wintypes.DWORD,
            ]
            kernel32.SetInformationJobObject.restype = wintypes.BOOL
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL

            handle = kernel32.CreateJobObjectW(None, None)
            if not handle:
                return
            information = JobExtendedLimitInformation()
            information.BasicLimitInformation.LimitFlags = 0x00002000
            configured = kernel32.SetInformationJobObject(
                handle,
                9,
                ctypes.byref(information),
                ctypes.sizeof(information),
            )
            if not configured:
                kernel32.CloseHandle(handle)
                return
            self._kernel32 = kernel32
            self._handle = handle
        except (AttributeError, OSError, TypeError, ValueError):
            self._kernel32 = None
            self._handle = None

    @property
    def active(self) -> bool:
        return self._handle is not None

    def assign(self, process_id: int) -> bool:
        if self._kernel32 is None or self._handle is None:
            return False
        process_handle = self._kernel32.OpenProcess(
            0x0001 | 0x0100,
            False,
            process_id,
        )
        if not process_handle:
            return False
        try:
            return bool(
                self._kernel32.AssignProcessToJobObject(
                    self._handle,
                    process_handle,
                )
            )
        finally:
            self._kernel32.CloseHandle(process_handle)

    def close(self) -> None:
        if self._kernel32 is not None and self._handle is not None:
            self._kernel32.CloseHandle(self._handle)
        self._kernel32 = None
        self._handle = None


class BundledModelProcess:
    def __init__(
        self,
        spec: BundledModelSpec,
        *,
        api_key: str,
        api_key_file: Path,
        log_root: Path,
        process_job: WindowsKillOnCloseJob,
    ) -> None:
        self.spec = spec
        self.api_key = api_key
        self.api_key_file = api_key_file
        self.process_job = process_job
        self.port = _free_loopback_port()
        self.base_url = f"http://127.0.0.1:{self.port}{spec.base_path}"
        self.log_path = log_root / f"{spec.task_type}.log"
        self.process: subprocess.Popen[bytes] | None = None
        self._log_stream: Any = None
        self.job_attached = False

    def start(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_stream = self.log_path.open("wb")
        arguments = [
            str(self.spec.executable),
            "--model",
            str(self.spec.model),
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
            "--api-key-file",
            str(self.api_key_file),
            *self.spec.server_arguments,
        ]
        cpu_threads = os.environ.get("MODEL_CPU_THREADS", "")
        if cpu_threads:
            if not cpu_threads.isdigit() or not 1 <= int(cpu_threads) <= 64:
                raise PortableLayoutError("MODEL_CPU_THREADS must be between 1 and 64")
            if "--threads" not in arguments and "-t" not in arguments:
                arguments.extend(["--threads", cpu_threads])
        try:
            self.process = subprocess.Popen(
                arguments,
                cwd=str(PACKAGE_ROOT),
                env=_sidecar_environment(),
                stdin=subprocess.DEVNULL,
                stdout=self._log_stream,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self.job_attached = self.process_job.assign(self.process.pid)
            if not self.job_attached:
                print(
                    "[WARN] Windows Job Object attachment was unavailable; "
                    "normal shutdown cleanup remains active."
                )
        except Exception:
            self._close_log()
            raise

    def wait_until_ready(self, timeout_seconds: int) -> tuple[bool, str]:
        if self.process is None:
            return False, "sidecar was not started"
        deadline = time.monotonic() + timeout_seconds
        health_url = f"http://127.0.0.1:{self.port}{self.spec.health_path}"
        while time.monotonic() < deadline:
            exit_code = self.process.poll()
            if exit_code is not None:
                detail = _read_log_tail(self.log_path)
                message = f"sidecar exited with code {exit_code}"
                return False, f"{message}: {detail}" if detail else message
            try:
                request = urllib.request.Request(
                    health_url,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                with _LOOPBACK_HTTP_OPENER.open(request, timeout=2) as response:
                    if response.status == 200:
                        return True, "ready"
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(0.5)
        detail = _read_log_tail(self.log_path)
        message = f"health check timed out after {timeout_seconds}s"
        return False, f"{message}: {detail}" if detail else message

    def _close_log(self) -> None:
        if self._log_stream is not None:
            self._log_stream.close()
            self._log_stream = None

    def stop(self) -> None:
        process = self.process
        if process is None:
            self._close_log()
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                else:
                    process.kill()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        self._close_log()


class BundledModelManager:
    def __init__(
        self,
        specs: tuple[BundledModelSpec, ...],
        *,
        data_root: Path,
        timeout_seconds: int,
    ) -> None:
        self.specs = specs
        self.timeout_seconds = timeout_seconds
        self.api_key = secrets.token_urlsafe(32)
        self.log_root = data_root / "logs" / "local-models"
        self.api_key_file = self.log_root / f"api-key-{os.getpid()}.txt"
        self.process_job = WindowsKillOnCloseJob()
        self.processes: list[BundledModelProcess] = []
        self.ready: dict[str, BundledModelProcess] = {}

    def start(self, *, require_all: bool = False) -> None:
        self.log_root.mkdir(parents=True, exist_ok=True)
        self.api_key_file.write_text(self.api_key, encoding="ascii")
        try:
            os.chmod(self.api_key_file, 0o600)
        except OSError:
            pass
        for spec in self.specs:
            process = BundledModelProcess(
                spec,
                api_key=self.api_key,
                api_key_file=self.api_key_file,
                log_root=self.log_root,
                process_job=self.process_job,
            )
            self.processes.append(process)
            try:
                print(f"[INFO] Starting bundled {spec.task_type} model...")
                process.start()
                ok, detail = process.wait_until_ready(self.timeout_seconds)
            except (OSError, ValueError) as exc:
                ok, detail = False, str(exc)
            if ok:
                self.ready[spec.task_type] = process
                print(f"[OK] Bundled {spec.task_type} model is ready.")
            else:
                process.stop()
                print(
                    f"[WARN] Bundled {spec.task_type} model is unavailable: {detail}\n"
                    f"[WARN] Falling back without this local {spec.task_type} component."
                )
        if require_all and len(self.ready) != len(self.specs):
            missing = sorted({spec.task_type for spec in self.specs} - set(self.ready))
            raise PortableLayoutError(
                "Bundled model runtime smoke check failed: " + ", ".join(missing)
            )
        self.apply_environment()
        self.write_status()

    def apply_environment(self) -> None:
        for name in (
            "BUNDLED_GGUF_API_KEY",
            "BUNDLED_GGUF_EMBEDDING_URL",
            "BUNDLED_GGUF_EMBEDDING_MODEL",
            "BUNDLED_GGUF_RERANKER_URL",
            "BUNDLED_GGUF_RERANKER_MODEL",
        ):
            os.environ.pop(name, None)
        if not self.ready:
            return
        os.environ["BUNDLED_GGUF_API_KEY"] = self.api_key
        for task_type, process in self.ready.items():
            prefix = f"BUNDLED_GGUF_{task_type.upper()}"
            os.environ[f"{prefix}_URL"] = process.base_url
            os.environ[f"{prefix}_MODEL"] = process.spec.model_name

    def write_status(self) -> None:
        payload = {
            "schema_version": 1,
            "updated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "components": [
                {
                    "id": spec.component_id,
                    "task_type": spec.task_type,
                    "model_name": spec.model_name,
                    "status": "ready" if spec.task_type in self.ready else "fallback",
                    "log": str((self.log_root / f"{spec.task_type}.log").resolve()),
                    "job_object_attached": bool(
                        self.ready.get(spec.task_type)
                        and self.ready[spec.task_type].job_attached
                    ),
                }
                for spec in self.specs
            ],
        }
        status_path = self.log_root / "status.json"
        status_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = status_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, status_path)

    def stop(self) -> None:
        for process in reversed(self.processes):
            process.stop()
        self.process_job.close()
        try:
            self.api_key_file.unlink(missing_ok=True)
        except OSError:
            pass
        self.apply_environment()


def _required_paths() -> tuple[Path, ...]:
    return (
        BACKEND_ROOT / "app" / "main.py",
        BACKEND_ROOT / "alembic.ini",
        FRONTEND_ROOT / "index.html",
        PACKAGE_ROOT / "agent-skills" / "gw-ap-debug" / "SKILL.md",
        PACKAGE_ROOT
        / "agent-skills"
        / "gw-ap-debug"
        / "scripts"
        / "upload-knowledge-markdown.ps1",
        PACKAGE_ROOT / "scripts" / "install_agent_skill_mcp.ps1",
        PACKAGE_ROOT / "scripts" / "install_agent_skill_mcp.bat",
        PACKAGE_ROOT / "scripts" / "start_codeagent.ps1",
        PACKAGE_ROOT / "scripts" / "codeagent_launcher_support.ps1",
        PACKAGE_ROOT / "scripts" / "codeagent_launcher_http.ps1",
        PACKAGE_ROOT / "start_codeagent.bat",
        PACKAGE_ROOT / "portable_codeagent.py",
        PACKAGE_ROOT / "portable_mcp.py",
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


def configure_server_environment(host: str, port: int) -> str:
    public_base_url = f"http://{host}:{port}"
    os.environ["CORS_ORIGINS"] = (
        f"{public_base_url},http://localhost:{port}"
    )
    os.environ["MCP_PUBLIC_BASE_URL"] = public_base_url
    return public_base_url


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
    bundled_models = load_bundled_model_specs()
    if bundled_models:
        tasks = ", ".join(spec.task_type for spec in bundled_models)
        print(f"[OK] Bundled GGUF component contract is valid: {tasks}.")
    else:
        print("[INFO] No bundled GGUF retrieval components are installed.")
    print(f"[OK] Writable data directory: {data_root}")


def _open_browser_when_ready(host: str, port: int, timeout_seconds: int = 90) -> None:
    health_url = f"http://{host}:{port}/api/v1/health/ready"
    app_url = f"http://{host}:{port}/"
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with _LOOPBACK_HTTP_OPENER.open(health_url, timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if response.status == 200 and payload.get("ready") is True:
                webbrowser.open(app_url)
                return
        except (OSError, ValueError, urllib.error.URLError):
            pass
        time.sleep(0.5)
    print(f"[WARN] The browser was not opened automatically. Visit {app_url}")


def _watch_parent_pipe(server: Any, input_fd: int) -> None:
    """Observe the private shutdown pipe without holding a Windows CRT fd lock.

    A blocking stdin read can deadlock native DLL initialization that inspects
    stdio during imports. PeekNamedPipe does not consume input or lock CRT stdio.
    The parent owns this anonymous pipe; any byte or EOF requests graceful exit.
    """
    if os.name != "nt":
        try:
            os.read(input_fd, 1)
        finally:
            server.should_exit = True
        return
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.PeekNamedPipe.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
        ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
    ]
    kernel.PeekNamedPipe.restype = wintypes.BOOL
    handle = msvcrt.get_osfhandle(input_fd)
    while not server.should_exit:
        available = wintypes.DWORD()
        if not kernel.PeekNamedPipe(handle, None, 0, None, ctypes.byref(available), None):
            server.should_exit = True
            return
        if available.value:
            server.should_exit = True
            return
        time.sleep(0.1)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Start the self-contained GW/AP Debug Platform package."
    )
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--managed-stdin", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--check-models",
        action="store_true",
        help="Load bundled GGUF models and require both health checks to pass.",
    )
    parser.add_argument(
        "--no-local-retrieval",
        action="store_true",
        help="Do not start bundled Embedding/Reranker sidecars for this run.",
    )
    parser.add_argument(
        "--model-start-timeout",
        type=int,
        default=240,
        help="Seconds allowed for each bundled model to become healthy.",
    )
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_PATH)
    parser.add_argument("--server-config", type=Path, help="Machine-level LAN server profile (HTTPS gateway required).")
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("--port must be between 1024 and 65535")
    if not 10 <= args.model_start_timeout <= 1800:
        parser.error("--model-start-timeout must be between 10 and 1800")
    if args.check_models and not args.check:
        parser.error("--check-models requires --check")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    model_manager: BundledModelManager | None = None
    try:
        validate_layout()
        sys.path.insert(0, str(PACKAGE_ROOT))
        server_config = None
        if args.server_config:
            from portable_server_config import load_server_config

            server_config = load_server_config(args.server_config.resolve())
            args.data_root = server_config.root / "data"
            args.env_file = server_config.env_file
            args.port = server_config.backend_port
            args.no_browser = True
            server_config.apply_environment()
        data_root, env_path = ensure_local_environment(args.data_root, args.env_file)
        if args.check:
            run_self_check(data_root, env_path)
            if args.check_models:
                specs = load_bundled_model_specs()
                if not specs:
                    raise PortableLayoutError(
                        "--check-models was requested but no GGUF components are installed"
                    )
                model_manager = BundledModelManager(
                    specs,
                    data_root=data_root,
                    timeout_seconds=args.model_start_timeout,
                )
                model_manager.start(require_all=True)
                print("[OK] All installed GGUF component health checks passed.")
            return 0

        sys.path.insert(0, str(PACKAGE_ROOT))
        from portable_mcp import prepare_mcp_environment

        prepare_mcp_environment(data_root, env_path)
        host = "127.0.0.1"
        check_port_available(host, args.port)
        public_base_url = server_config.origin if server_config else configure_server_environment(host, args.port)

        specs = () if args.no_local_retrieval else load_bundled_model_specs()
        if specs:
            model_manager = BundledModelManager(
                specs,
                data_root=data_root,
                timeout_seconds=args.model_start_timeout,
            )
            # Sidecars must be ready and their ephemeral connection settings
            # exported before FastAPI imports its cached Settings instance.
            model_manager.start(require_all=bool(server_config))
        else:
            for name in (
                "BUNDLED_GGUF_API_KEY",
                "BUNDLED_GGUF_EMBEDDING_URL",
                "BUNDLED_GGUF_EMBEDDING_MODEL",
                "BUNDLED_GGUF_RERANKER_URL",
                "BUNDLED_GGUF_RERANKER_MODEL",
            ):
                os.environ.pop(name, None)
            if args.no_local_retrieval and MODEL_COMPONENTS_PATH.is_file():
                print("[INFO] Bundled local retrieval is disabled for this run.")

        if not args.no_browser:
            threading.Thread(
                target=_open_browser_when_ready,
                args=(host, args.port),
                daemon=True,
            ).start()

        import uvicorn

        print(f"[INFO] Data directory: {data_root}")
        print(f"[INFO] Open {public_base_url}/")
        print(f"[INFO] MCP endpoint: {public_base_url}/mcp")
        print("[INFO] Press Ctrl+C to stop the platform.")
        config = uvicorn.Config(
            "app.main:app",
            host=host,
            port=args.port,
            reload=False,
            access_log=True,
        )
        server = uvicorn.Server(config)
        model_failure = threading.Event()
        if server_config and model_manager:
            def watch_models() -> None:
                while not server.should_exit:
                    if any(item.process is not None and item.process.poll() is not None
                           for item in model_manager.ready.values()):
                        print("[ERROR] A required GGUF process exited; stopping for service recovery.", file=sys.stderr)
                        model_failure.set()
                        server.should_exit = True
                        return
                    time.sleep(1)
            threading.Thread(target=watch_models, daemon=True).start()
        if args.managed_stdin:
            threading.Thread(
                target=_watch_parent_pipe, args=(server, sys.stdin.fileno()), daemon=True,
            ).start()
        server.run()
        return 1 if model_failure.is_set() else 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    finally:
        if model_manager is not None:
            model_manager.stop()


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
