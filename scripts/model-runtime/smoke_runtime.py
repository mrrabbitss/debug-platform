#!/usr/bin/env python3
"""Smoke-test the pinned llama.cpp Embedding and Reranker GGUF runtime.

The check is deliberately content-safe and standard-library only. It starts both
loopback sidecars with an ephemeral bearer token, verifies their native HTTP
contracts and performs a small Chinese relevance sanity check. It is a release
smoke gate, not a replacement for the repository retrieval Golden Dataset.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class SmokeError(RuntimeError):
    pass


def _windows_loaded_modules(process_id: int) -> dict[str, Path]:
    if os.name != "nt":
        return {}
    try:
        import ctypes
        from ctypes import wintypes

        max_module_name32 = 255
        max_path = 260

        class ModuleEntry32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("th32ModuleID", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("GlblcntUsage", wintypes.DWORD),
                ("ProccntUsage", wintypes.DWORD),
                ("modBaseAddr", ctypes.POINTER(ctypes.c_byte)),
                ("modBaseSize", wintypes.DWORD),
                ("hModule", wintypes.HMODULE),
                ("szModule", wintypes.WCHAR * (max_module_name32 + 1)),
                ("szExePath", wintypes.WCHAR * max_path),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.Module32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ModuleEntry32W)]
        kernel32.Module32FirstW.restype = wintypes.BOOL
        kernel32.Module32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ModuleEntry32W)]
        kernel32.Module32NextW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        snapshot = kernel32.CreateToolhelp32Snapshot(0x00000008 | 0x00000010, process_id)
        invalid_handle = ctypes.c_void_p(-1).value
        if snapshot == invalid_handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = ModuleEntry32W()
            entry.dwSize = ctypes.sizeof(entry)
            if not kernel32.Module32FirstW(snapshot, ctypes.byref(entry)):
                raise ctypes.WinError(ctypes.get_last_error())
            result: dict[str, Path] = {}
            while True:
                result[entry.szModule.lower()] = Path(entry.szExePath).resolve()
                entry.dwSize = ctypes.sizeof(entry)
                if not kernel32.Module32NextW(snapshot, ctypes.byref(entry)):
                    error = ctypes.get_last_error()
                    if error != 18:  # ERROR_NO_MORE_FILES
                        raise ctypes.WinError(error)
                    break
            return result
        finally:
            kernel32.CloseHandle(snapshot)
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        raise SmokeError(f"Could not inspect sidecar native modules: {exc}") from exc


def check_app_local_msvc(sidecar: "Sidecar") -> None:
    if os.name != "nt":
        return
    if sidecar.process is None or sidecar.process.poll() is not None:
        raise SmokeError(f"{sidecar.task} sidecar is not running for native module inspection")
    modules = _windows_loaded_modules(sidecar.process.pid)
    expected_directory = sidecar.executable.parent.resolve()
    required = {"msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll"}
    missing = sorted(required - modules.keys())
    if missing:
        raise SmokeError(
            f"{sidecar.task} sidecar did not load the required Microsoft VC runtime: {missing}"
        )
    wrong_directory = {
        name: str(modules[name])
        for name in sorted(required)
        if modules[name].parent != expected_directory
    }
    if wrong_directory:
        raise SmokeError(
            f"{sidecar.task} sidecar used system/global VC runtime DLLs instead of app-local files: "
            f"{wrong_directory}"
        )
    print(
        f"[OK] {sidecar.task} loaded MSVCP140/VCRUNTIME140 app-local from "
        f"{expected_directory}."
    )


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _request_json(
    url: str,
    token: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: float = 30,
) -> dict[str, Any]:
    body = None
    headers = {"Authorization": f"Bearer {token}"}
    method = "GET"
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
        method = "POST"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read(1000).decode("utf-8", errors="replace")
        raise SmokeError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except (OSError, urllib.error.URLError) as exc:
        raise SmokeError(f"Request failed for {url}: {exc}") from exc
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SmokeError(f"Non-JSON response from {url}") from exc
    if not isinstance(value, dict):
        raise SmokeError(f"Unexpected JSON root from {url}")
    return value


def _log_tail(path: Path, maximum_bytes: int = 6000) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - maximum_bytes))
            return handle.read().decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


@dataclass
class Sidecar:
    task: str
    executable: Path
    model: Path
    token: str
    key_file: Path
    log_file: Path
    arguments: list[str]
    port: int = 0
    process: subprocess.Popen[bytes] | None = None
    log_stream: Any = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, timeout_seconds: int) -> None:
        self.port = _free_loopback_port()
        self.log_stream = self.log_file.open("wb")
        command = [
            str(self.executable),
            "--model",
            str(self.model),
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
            "--api-key-file",
            str(self.key_file),
            *self.arguments,
        ]
        try:
            self.process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=self.log_stream,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            self.close()
            raise SmokeError(f"Could not start {self.task} sidecar: {exc}") from exc

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise SmokeError(
                    f"{self.task} sidecar exited with {self.process.returncode}: "
                    f"{_log_tail(self.log_file)}"
                )
            try:
                _request_json(f"{self.base_url}/health", self.token, timeout=2)
                print(f"[OK] {self.task} sidecar is ready on a dynamic loopback port.")
                return
            except SmokeError:
                time.sleep(0.5)
        raise SmokeError(
            f"{self.task} sidecar did not become healthy within {timeout_seconds}s: "
            f"{_log_tail(self.log_file)}"
        )

    def close(self) -> None:
        process = self.process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if self.log_stream is not None:
            self.log_stream.close()
            self.log_stream = None


def _validated_vectors(payload: dict[str, Any]) -> list[list[float]]:
    data = payload.get("data")
    if not isinstance(data, list) or len(data) != 3:
        raise SmokeError("Embedding endpoint did not return exactly three vectors")
    ordered: list[tuple[int, list[float]]] = []
    for item in data:
        if not isinstance(item, dict) or not isinstance(item.get("embedding"), list):
            raise SmokeError("Embedding response item is malformed")
        try:
            index = int(item["index"])
            vector = [float(value) for value in item["embedding"]]
        except (KeyError, TypeError, ValueError) as exc:
            raise SmokeError("Embedding response contains invalid numeric data") from exc
        if len(vector) != 768 or any(not math.isfinite(value) for value in vector):
            raise SmokeError("BGE must return finite 768-dimensional vectors")
        norm = math.sqrt(sum(value * value for value in vector))
        if not math.isclose(norm, 1.0, rel_tol=2e-3, abs_tol=2e-3):
            raise SmokeError(f"BGE vector is not L2-normalized (norm={norm:.6f})")
        ordered.append((index, vector))
    ordered.sort(key=lambda item: item[0])
    if [index for index, _ in ordered] != [0, 1, 2]:
        raise SmokeError("Embedding response indexes are not contiguous")
    return [vector for _, vector in ordered]


def _cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


def check_embedding(sidecar: Sidecar) -> None:
    query = "为这个句子生成表示以用于检索相关文章：AP 频繁离线如何排查"
    relevant = "检查 AP 心跳超时、GW 端口状态和离线时间线。"
    unrelated = "设备外壳颜色和包装箱尺寸说明。"
    payload = _request_json(
        f"{sidecar.base_url}/v1/embeddings",
        sidecar.token,
        payload={
            "model": "BAAI/bge-base-zh-v1.5",
            "input": [query, relevant, unrelated],
            "encoding_format": "float",
        },
        timeout=120,
    )
    query_vector, relevant_vector, unrelated_vector = _validated_vectors(payload)
    relevant_score = _cosine(query_vector, relevant_vector)
    unrelated_score = _cosine(query_vector, unrelated_vector)
    if relevant_score <= unrelated_score:
        raise SmokeError(
            "BGE relevance sanity check failed: relevant text did not outrank unrelated text"
        )
    print(
        "[OK] BGE returned normalized 768-D vectors and ranked the synthetic "
        f"diagnostic text higher ({relevant_score:.4f} > {unrelated_score:.4f})."
    )


def check_reranker(sidecar: Sidecar) -> None:
    payload = _request_json(
        f"{sidecar.base_url}/v1/rerank",
        sidecar.token,
        payload={
            "model": "Qwen/Qwen3-Reranker-0.6B",
            "query": "AP 频繁离线如何排查",
            "documents": [
                "检查 AP 心跳超时、GW 端口状态和离线时间线。",
                "设备外壳颜色和包装箱尺寸说明。",
            ],
            "top_n": 2,
        },
        timeout=180,
    )
    results = payload.get("results")
    if not isinstance(results, list) or len(results) != 2:
        raise SmokeError("Reranker endpoint did not return both candidates")
    parsed: list[tuple[int, float]] = []
    for item in results:
        if not isinstance(item, dict):
            raise SmokeError("Reranker response item is malformed")
        raw_score = item.get("relevance_score", item.get("score"))
        try:
            index = int(item["index"])
            score = float(raw_score)
        except (KeyError, TypeError, ValueError) as exc:
            raise SmokeError("Reranker response contains invalid index or score") from exc
        if index not in {0, 1} or not math.isfinite(score):
            raise SmokeError("Reranker response contains an out-of-range value")
        parsed.append((index, score))
    parsed.sort(key=lambda item: item[1], reverse=True)
    if parsed[0][0] != 0 or parsed[0][1] <= parsed[1][1]:
        raise SmokeError("Qwen relevance sanity check failed")
    print(
        "[OK] Qwen Reranker ranked the synthetic diagnostic text first "
        f"({parsed[0][1]:.4f} > {parsed[1][1]:.4f})."
    )


def _existing_file(value: str, label: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"{label} is missing: {path}")
    return path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--llama-server", required=True)
    parser.add_argument("--embedding-model", required=True)
    parser.add_argument("--reranker-model", required=True)
    parser.add_argument("--start-timeout", type=int, default=300)
    args = parser.parse_args(argv)
    if not 30 <= args.start_timeout <= 1800:
        parser.error("--start-timeout must be between 30 and 1800 seconds")
    try:
        args.llama_server = _existing_file(args.llama_server, "llama-server")
        args.embedding_model = _existing_file(args.embedding_model, "Embedding model")
        args.reranker_model = _existing_file(args.reranker_model, "Reranker model")
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    token = secrets.token_urlsafe(32)
    sidecars: list[Sidecar] = []
    root = Path(tempfile.mkdtemp(prefix="debug-platform-gguf-smoke-"))
    failure_logs: dict[str, str] = {}
    try:
        key_file = root / "api-key.txt"
        key_file.write_text(token, encoding="ascii")
        try:
            os.chmod(key_file, 0o600)
        except OSError:
            pass
        embedding = Sidecar(
            task="embedding",
            executable=args.llama_server,
            model=args.embedding_model,
            token=token,
            key_file=key_file,
            log_file=root / "embedding.log",
            arguments=[
                "--embedding",
                "--pooling",
                "cls",
                "--embd-normalize",
                "2",
                "--ctx-size",
                "512",
                "--batch-size",
                "512",
                "--ubatch-size",
                "512",
                "--no-webui",
            ],
        )
        reranker = Sidecar(
            task="reranker",
            executable=args.llama_server,
            model=args.reranker_model,
            token=token,
            key_file=key_file,
            log_file=root / "reranker.log",
            arguments=[
                "--reranking",
                "--pooling",
                "rank",
                "--ctx-size",
                "8192",
                "--parallel",
                "1",
                "--batch-size",
                "512",
                "--ubatch-size",
                "512",
                "--no-webui",
            ],
        )
        sidecars.extend([embedding, reranker])
        embedding.start(args.start_timeout)
        reranker.start(args.start_timeout)
        check_app_local_msvc(embedding)
        check_app_local_msvc(reranker)
        check_embedding(embedding)
        check_reranker(reranker)
        print("[OK] Full GGUF E/R runtime smoke passed.")
        return 0
    except (OSError, SmokeError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        for sidecar in sidecars:
            detail = _log_tail(sidecar.log_file)
            if detail:
                failure_logs[sidecar.task] = detail
        for task, detail in failure_logs.items():
            print(f"[INFO] {task} log tail:\n{detail}", file=sys.stderr)
        return 1
    finally:
        for sidecar in reversed(sidecars):
            sidecar.close()
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
