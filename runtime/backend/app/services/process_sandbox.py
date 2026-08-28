"""Small cross-platform subprocess sandbox used for untrusted document parsing."""

from __future__ import annotations

import ctypes
import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


class SandboxError(RuntimeError):
    pass


class SandboxTimeout(SandboxError):
    pass


@dataclass(frozen=True)
class SandboxResult:
    returncode: int
    stdout: str
    stderr: str


class _WindowsJob:
    _JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", ctypes.c_ulong),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_ulong),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.c_ulong),
            ("SchedulingClass", ctypes.c_ulong),
        ]

    class _ExtendedLimitInformation(ctypes.Structure):
        pass

    _ExtendedLimitInformation._fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]

    def __init__(self, process: subprocess.Popen[str], memory_bytes: int) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise SandboxError(f"CreateJobObject failed: {ctypes.get_last_error()}")
        self._kernel32 = kernel32
        self._handle = handle
        information = self._ExtendedLimitInformation()
        information.BasicLimitInformation.LimitFlags = (
            self._JOB_OBJECT_LIMIT_PROCESS_MEMORY
            | self._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        information.ProcessMemoryLimit = memory_bytes
        configured = kernel32.SetInformationJobObject(
            handle,
            self._JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(information),
            ctypes.sizeof(information),
        )
        process_handle = getattr(process, "_handle", None)
        assigned = (
            kernel32.AssignProcessToJobObject(handle, process_handle)
            if configured and process_handle
            else 0
        )
        if not configured or not assigned:
            error = ctypes.get_last_error()
            self.close()
            process.kill()
            raise SandboxError(f"Unable to apply Windows process limits: {error}")

    def close(self) -> None:
        handle = getattr(self, "_handle", None)
        if handle:
            self._kernel32.CloseHandle(handle)
            self._handle = None


def _posix_limit_function(memory_bytes: int, cpu_seconds: int):
    def apply_limits() -> None:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))

    return apply_limits


def run_sandboxed(
    command: Sequence[str],
    *,
    cwd: Path,
    timeout_seconds: int,
    memory_bytes: int,
    cpu_seconds: int,
    env: Mapping[str, str] | None = None,
) -> SandboxResult:
    if timeout_seconds <= 0 or memory_bytes <= 0 or cpu_seconds <= 0:
        raise ValueError("Sandbox budgets must be positive")
    kwargs: dict[str, object] = {
        "cwd": str(cwd),
        "env": dict(env or os.environ),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
        kwargs["preexec_fn"] = _posix_limit_function(memory_bytes, cpu_seconds)

    process = subprocess.Popen(list(command), **kwargs)
    windows_job: _WindowsJob | None = None
    try:
        if os.name == "nt":
            windows_job = _WindowsJob(process, memory_bytes)
        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            if windows_job:
                windows_job.close()
                windows_job = None
            else:
                process.kill()
            process.communicate()
            raise SandboxTimeout(
                f"Sandbox exceeded {timeout_seconds}s wall-clock budget"
            ) from exc
        return SandboxResult(
            returncode=process.returncode,
            stdout=stdout[:1_000_000],
            stderr=stderr[:64_000],
        )
    finally:
        if windows_job:
            windows_job.close()
