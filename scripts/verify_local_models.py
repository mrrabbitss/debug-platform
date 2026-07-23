from __future__ import annotations

import argparse
import gc
import math
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load and exercise local models through the application adapters."
    )
    parser.add_argument("--models-root", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    from app.core.utils import json_dumps
    from app.models import ModelProfile
    from app.services import retrieval_models

    args = parse_args()
    models_root = args.models_root.resolve()
    embedding_path = models_root / "embedding" / "bge-base-zh-v1.5"
    reranker_path = models_root / "reranker" / "Qwen3-Reranker-0.6B"

    embedding_profile = ModelProfile(
        id="MODEL-install-check-embedding",
        name="Installation check BGE",
        task_type="embedding",
        mode="local",
        provider="sentence_transformers",
        model_name=str(embedding_path),
        config_json=json_dumps({
            "device": "cpu",
            "batch_size": 2,
            "normalize": True,
            "query_instruction": "为这个句子生成表示以用于检索相关文章：",
        }),
    )
    vectors = retrieval_models.embed_texts(
        embedding_profile,
        ["AP 无法上线", "检查认证和 DHCP 日志"],
        purpose="case_retrieval_query",
    )
    if len(vectors) != 2 or not vectors[0] or len(vectors[0]) != len(vectors[1]):
        raise RuntimeError("BGE compatibility test returned invalid vectors")
    if len(vectors[0]) != 768:
        raise RuntimeError(
            f"Unexpected BGE vector dimension {len(vectors[0])}; expected 768"
        )
    if not all(math.isfinite(value) for vector in vectors for value in vector):
        raise RuntimeError("BGE compatibility test returned non-finite values")
    print(f"[OK] Application Embedding adapter: {len(vectors[0])}-dimension vectors", flush=True)

    retrieval_models._load_sentence_transformer.cache_clear()
    gc.collect()

    reranker_profile = ModelProfile(
        id="MODEL-install-check-reranker",
        name="Installation check Qwen reranker",
        task_type="reranker",
        mode="local",
        provider="sentence_transformers",
        model_name=str(reranker_path),
        config_json=json_dumps({
            "device": "cpu",
            "batch_size": 1,
            "candidate_count": 2,
            "instruction": (
                "Given a network troubleshooting query, retrieve passages "
                "that help diagnose and solve it."
            ),
        }),
    )
    ranking = retrieval_models.rerank_documents(
        "AP 无法上线",
        ["天气晴朗", "检查 AP 认证、DHCP 地址分配和上线失败日志"],
        2,
        reranker_profile,
        purpose="local_model_installation_check",
    )
    if ranking is None or len(ranking) != 2:
        raise RuntimeError("Qwen reranker compatibility test returned no ranking")
    if not all(math.isfinite(score) for _, score in ranking):
        raise RuntimeError("Qwen reranker compatibility test returned non-finite scores")
    print("[OK] Application Reranker adapter: returned a valid two-document ranking", flush=True)

    retrieval_models._load_cross_encoder.cache_clear()
    gc.collect()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1) from None
