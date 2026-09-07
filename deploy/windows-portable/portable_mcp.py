"""Share a per-Windows-user MCP credential between portable Web and CLI entrypoints."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import secrets
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path
from typing import Iterator

from dotenv import dotenv_values


_ENTROPY = b"gwap-portable-mcp-v1"
_TOKEN_NAME = "mcp-token.dpapi"
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{64}")
_FALSE_VALUES = frozenset({"0", "off", "false", "no", "n", "f"})
_TRUE_VALUES = frozenset({"1", "on", "true", "yes", "y", "t"})


class PortableMCPError(RuntimeError):
    """A credential could not be prepared without changing user authentication."""


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _windows_libraries():
    if os.name != "nt":
        raise PortableMCPError("Automatic portable MCP credentials require Windows DPAPI.")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    for name in ("CryptProtectData", "CryptUnprotectData"):
        function = getattr(crypt, name)
        function.argtypes = [
            ctypes.POINTER(_DataBlob),
            ctypes.c_void_p,
            ctypes.POINTER(_DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_DataBlob),
        ]
        function.restype = wintypes.BOOL
    return kernel, crypt


def _dpapi(data: bytes, *, decrypt: bool) -> bytes:
    kernel, crypt = _windows_libraries()
    input_buffer = ctypes.create_string_buffer(data)
    entropy_buffer = ctypes.create_string_buffer(_ENTROPY)
    source = _DataBlob(len(data), ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    entropy = _DataBlob(
        len(_ENTROPY), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_ubyte)),
    )
    output = _DataBlob()
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    # CRYPTPROTECT_UI_FORBIDDEN, without CRYPTPROTECT_LOCAL_MACHINE: CurrentUser.
    if not function(
        ctypes.byref(source), None, ctypes.byref(entropy), None, None, 1,
        ctypes.byref(output),
    ):
        raise PortableMCPError(
            "Portable MCP credential cannot be protected or decrypted by this Windows account."
        )
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel.LocalFree(ctypes.cast(output.pbData, ctypes.c_void_p))


@contextmanager
def _credential_lock(path: Path) -> Iterator[None]:
    kernel, _crypt = _windows_libraries()
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel.ReleaseMutex.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    # Resolve the stable directory, never the concurrently replaced token file.
    # Windows can report the temporary file's previous name during replacement,
    # which would give racing callers different named mutexes.
    path.parent.mkdir(parents=True, exist_ok=True)
    canonical = path.parent.resolve() / path.name
    identity = hashlib.sha256(str(canonical).casefold().encode("utf-8")).hexdigest()
    handle = kernel.CreateMutexW(None, False, "Local\\GWAP-Portable-MCP-" + identity)
    if not handle:
        raise PortableMCPError("Cannot lock portable MCP credential initialization.")
    acquired = False
    try:
        # An abandoned mutex is acquired by this caller; re-read the atomic file.
        acquired = kernel.WaitForSingleObject(handle, 15_000) in {0, 0x80}
        if not acquired:
            raise PortableMCPError("Timed out waiting for portable MCP credential initialization.")
        yield
    finally:
        if acquired:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


def _load_or_create_token(data_root: Path) -> str:
    path = data_root / ".launcher" / _TOKEN_NAME
    with _credential_lock(path):
        if path.exists():
            try:
                if not path.is_file() or not 0 < path.stat().st_size <= 16_384:
                    raise ValueError("Invalid encrypted credential size")
                payload = json.loads(_dpapi(path.read_bytes(), decrypt=True).decode("utf-8"))
                token = payload.get("token")
                if (
                    payload.get("schema_version") != 1
                    or not isinstance(token, str)
                    or _TOKEN_PATTERN.fullmatch(token) is None
                ):
                    raise ValueError("Invalid encrypted credential schema")
                return token
            except (OSError, ValueError, TypeError, AttributeError) as exc:
                raise PortableMCPError(
                    "Saved portable MCP credential is invalid; it was left unchanged."
                ) from exc
        token = secrets.token_urlsafe(48)
        payload = json.dumps({"schema_version": 1, "token": token}).encode("utf-8")
        encrypted = _dpapi(payload, decrypt=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.parent / (".mcp-token-" + secrets.token_hex(16) + ".tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(encrypted)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except OSError as exc:
            raise PortableMCPError("Cannot atomically save the portable MCP credential.") from exc
        finally:
            temporary.unlink(missing_ok=True)
        return token


def prepare_mcp_environment(data_root: Path, env_path: Path) -> str | None:
    """Return one credential valid for this backend's MCP and REST data plane.

    Only unkeyed local mode gets a generated static MCP token. Configuration and
    process environment are never rewritten except MCP_BEARER_TOKEN for that
    local connection. RBAC always requires an explicitly supplied personal CLI
    token; neither API_KEY nor an existing static MCP admin token is borrowed.
    """
    configured = {
        str(key).casefold(): value
        for key, value in dotenv_values(env_path, encoding="utf-8").items()
        if value is not None
    }
    environment = {key.casefold(): value for key, value in os.environ.items()}

    def setting(name: str, default: str = "") -> str:
        return environment.get(name.casefold(), configured.get(name.casefold(), default))

    enabled = setting("MCP_ENABLED", "true").casefold()
    if enabled in _FALSE_VALUES:
        return None
    if enabled not in _TRUE_VALUES:
        raise PortableMCPError("MCP_ENABLED must be a valid boolean setting.")
    mode = setting("AUTH_MODE", "local")
    if mode == "rbac":
        return environment.get("debugplatform_mcp_token") or None
    if mode not in {"local", "api_key"}:
        return None
    api_key = setting("API_KEY")
    if api_key:
        return api_key
    if mode != "local":
        return None
    token = setting("MCP_BEARER_TOKEN") or _load_or_create_token(Path(data_root))
    os.environ["MCP_BEARER_TOKEN"] = token
    return token
