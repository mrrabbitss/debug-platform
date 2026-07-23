from __future__ import annotations

import argparse
import fnmatch
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


GIB = 1024**3
NON_RUNTIME_PATTERNS = (
    "*.h5",
    "*.msgpack",
    "*.onnx",
    "onnx/*",
    "openvino/*",
    "*.tflite",
    "*.ot",
)


@dataclass(frozen=True)
class ModelSpec:
    role: str
    repo_id: str
    relative_dir: str


MODEL_SPECS = (
    ModelSpec(
        role="embedding",
        repo_id="BAAI/bge-base-zh-v1.5",
        relative_dir="embedding/bge-base-zh-v1.5",
    ),
    ModelSpec(
        role="reranker",
        repo_id="Qwen/Qwen3-Reranker-0.6B",
        relative_dir="reranker/Qwen3-Reranker-0.6B",
    ),
)


def validate_endpoint(endpoint: str) -> str:
    value = endpoint.strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Mirror endpoint must be an http(s) URL with a hostname")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Mirror endpoint must not contain credentials, a query, or a fragment")
    return value


def ensure_layout(models_root: Path) -> None:
    for name in ("inference", "reranker", "embedding"):
        (models_root / name).mkdir(parents=True, exist_ok=True)


def verify_model_dir(spec: ModelSpec, target: Path) -> dict[str, Any]:
    missing: list[str] = []
    if not (target / "config.json").is_file():
        missing.append("config.json")
    weights = [
        path
        for pattern in ("*.safetensors", "*.bin")
        for path in target.glob(pattern)
        if path.is_file()
    ]
    if not weights:
        missing.append("*.safetensors or *.bin model weights")
    tokenizer_files = [
        target / "tokenizer.json",
        target / "tokenizer_config.json",
        target / "vocab.txt",
    ]
    if not any(path.is_file() for path in tokenizer_files):
        missing.append("tokenizer.json, tokenizer_config.json, or vocab.txt")
    if missing:
        raise RuntimeError(
            f"{spec.repo_id} is incomplete in {target}: missing {', '.join(missing)}"
        )
    files = [path for path in target.rglob("*") if path.is_file()]
    return {
        "role": spec.role,
        "repo_id": spec.repo_id,
        "path": str(target.resolve()),
        "file_count": len(files),
        "bytes_on_disk": sum(path.stat().st_size for path in files),
    }


def _matches_any(path: str, patterns: list[str] | tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def _repo_plan(info: Any) -> tuple[str, list[str], int]:
    siblings = list(getattr(info, "siblings", []) or [])
    filenames = [str(getattr(item, "rfilename", "")) for item in siblings]
    ignore_patterns = list(NON_RUNTIME_PATTERNS)
    if any(name.endswith(".safetensors") for name in filenames):
        # Prefer safe tensor weights when a repository also exposes a duplicate
        # legacy pytorch_model.bin. Older BGE snapshots that only have .bin stay
        # supported.
        ignore_patterns.append("*.bin")
    selected_size = 0
    for item, filename in zip(siblings, filenames, strict=True):
        if not filename or _matches_any(filename, ignore_patterns):
            continue
        selected_size += int(getattr(item, "size", 0) or 0)
    revision = str(getattr(info, "sha", "") or "main")
    return revision, ignore_patterns, selected_size


def _check_disk_space(models_root: Path, target: Path, expected_bytes: int) -> None:
    if expected_bytes <= 0:
        return
    existing_bytes = sum(
        path.stat().st_size for path in target.rglob("*") if path.is_file()
    )
    remaining_bytes = max(0, expected_bytes - existing_bytes)
    safety_margin = max(GIB, int(expected_bytes * 0.15))
    free_bytes = shutil.disk_usage(models_root).free
    if free_bytes < remaining_bytes + safety_margin:
        needed_gib = (remaining_bytes + safety_margin) / GIB
        free_gib = free_bytes / GIB
        raise RuntimeError(
            f"Insufficient disk space for {target.name}: need about {needed_gib:.2f} GiB "
            f"including safety margin, only {free_gib:.2f} GiB is free"
        )


def download_model(
    spec: ModelSpec,
    models_root: Path,
    *,
    endpoint: str,
    max_workers: int,
    retries: int,
) -> dict[str, Any]:
    # HF_ENDPOINT must be set before importing huggingface_hub because its
    # constants are initialized at import time.
    from huggingface_hub import HfApi, snapshot_download

    target = models_root / Path(spec.relative_dir)
    target.mkdir(parents=True, exist_ok=True)
    revision = "main"
    ignore_patterns = list(NON_RUNTIME_PATTERNS)
    expected_bytes = 0
    try:
        info = HfApi(endpoint=endpoint).model_info(spec.repo_id, files_metadata=True)
        revision, ignore_patterns, expected_bytes = _repo_plan(info)
    except Exception as exc:
        print(
            f"[WARN] Unable to read size/revision metadata for {spec.repo_id}: {exc}",
            flush=True,
        )
        print("[WARN] Continuing with revision main; snapshot validation still applies.", flush=True)
    else:
        _check_disk_space(models_root, target, expected_bytes)
        print(
            f"[INFO] {spec.repo_id}: revision {revision[:12]}, "
            f"planned size {expected_bytes / GIB:.2f} GiB",
            flush=True,
        )

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            print(
                f"[INFO] Downloading {spec.repo_id} to {target} "
                f"(attempt {attempt}/{retries})...",
                flush=True,
            )
            snapshot_download(
                repo_id=spec.repo_id,
                revision=revision,
                local_dir=str(target),
                max_workers=max_workers,
                ignore_patterns=ignore_patterns,
            )
            result = verify_model_dir(spec, target)
            result["revision"] = revision
            return result
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                wait_seconds = min(5 * attempt, 15)
                print(
                    f"[WARN] Download attempt failed: {exc}. Retrying in {wait_seconds}s...",
                    flush=True,
                )
                time.sleep(wait_seconds)
    raise RuntimeError(f"Failed to download {spec.repo_id}: {last_error}") from last_error


def write_manifest(
    models_root: Path,
    *,
    endpoint: str,
    models: list[dict[str, Any]],
) -> Path:
    manifest_path = models_root / "model-installation.json"
    manifest = {
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "endpoint": endpoint,
        "layout": ["inference", "reranker", "embedding"],
        "models": models,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download the debug-platform local retrieval models."
    )
    parser.add_argument("--models-root", type=Path, required=True)
    parser.add_argument("--endpoint", default="https://hf-mirror.com")
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--verify-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    endpoint = validate_endpoint(args.endpoint)
    models_root = args.models_root.resolve()
    ensure_layout(models_root)

    if args.verify_only:
        verified = [
            verify_model_dir(spec, models_root / Path(spec.relative_dir))
            for spec in MODEL_SPECS
        ]
        print(f"[OK] Verified {len(verified)} local model directories.", flush=True)
        return 0

    os.environ["HF_ENDPOINT"] = endpoint
    os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
    models = [
        download_model(
            spec,
            models_root,
            endpoint=endpoint,
            max_workers=max(1, min(args.max_workers, 16)),
            retries=max(1, min(args.retries, 10)),
        )
        for spec in MODEL_SPECS
    ]
    manifest_path = write_manifest(models_root, endpoint=endpoint, models=models)
    for model in models:
        print(
            f"[OK] {model['repo_id']} -> {model['path']} "
            f"({model['bytes_on_disk'] / GIB:.2f} GiB)",
            flush=True,
        )
    print(f"[OK] Installation manifest: {manifest_path}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1) from None
