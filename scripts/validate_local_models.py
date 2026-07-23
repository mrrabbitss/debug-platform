from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


GIB = 1024**3


@dataclass(frozen=True)
class ModelSpec:
    role: str
    repo_id: str
    revision: str
    relative_dir: str


MODEL_SPECS = (
    ModelSpec(
        role="embedding",
        repo_id="BAAI/bge-base-zh-v1.5",
        revision="f03589ceff5aac7111bd60cfc7d497ca17ecac65",
        relative_dir="embedding/bge-base-zh-v1.5",
    ),
    ModelSpec(
        role="reranker",
        repo_id="Qwen/Qwen3-Reranker-0.6B",
        revision="e61197ed45024b0ed8a2d74b80b4d909f1255473",
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
        "revision": spec.revision,
        "path": str(target.resolve()),
        "file_count": len(files),
        "bytes_on_disk": sum(path.stat().st_size for path in files),
    }


def write_manifest(
    models_root: Path,
    *,
    endpoint: str,
    download_method: str = "unknown",
    models: list[dict[str, Any]],
) -> Path:
    manifest_path = models_root / "model-installation.json"
    manifest = {
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "download_method": download_method,
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
        description="Validate local retrieval models downloaded by the Hugging Face CLI."
    )
    parser.add_argument("--models-root", type=Path, required=True)
    parser.add_argument("--endpoint", default="https://hf-mirror.com")
    parser.add_argument("--download-method", default="unknown")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    endpoint = validate_endpoint(args.endpoint)
    models_root = args.models_root.resolve()
    ensure_layout(models_root)
    models = [
        verify_model_dir(spec, models_root / Path(spec.relative_dir))
        for spec in MODEL_SPECS
    ]
    manifest_path = write_manifest(
        models_root,
        endpoint=endpoint,
        models=models,
        download_method=args.download_method,
    )
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
