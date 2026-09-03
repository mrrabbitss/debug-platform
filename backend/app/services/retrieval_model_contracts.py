import math
from collections.abc import Sequence
from typing import Any


class RetrievalModelError(RuntimeError):
    pass


def embedding_inputs(
    texts: list[str],
    config: dict[str, Any],
    purpose: str,
) -> list[str]:
    query_instruction = str(config.get("query_instruction") or "").strip()
    if query_instruction and purpose.endswith("_query"):
        return [f"{query_instruction}{text}" for text in texts]
    return list(texts)


def validated_embedding_vectors(
    raw_vectors: Sequence[Sequence[float]],
    *,
    expected_count: int,
    expected_dimension: int | None,
) -> list[list[float]]:
    if len(raw_vectors) != expected_count:
        raise RetrievalModelError(
            "Embedding model returned an unexpected number of vectors"
        )
    vectors: list[list[float]] = []
    actual_dimension: int | None = None
    for raw_vector in raw_vectors:
        try:
            vector = [float(value) for value in raw_vector]
        except (TypeError, ValueError) as exc:
            raise RetrievalModelError(
                "Embedding model returned a non-numeric vector"
            ) from exc
        if not vector or any(not math.isfinite(value) for value in vector):
            raise RetrievalModelError(
                "Embedding model returned an empty or non-finite vector"
            )
        if actual_dimension is None:
            actual_dimension = len(vector)
        elif len(vector) != actual_dimension:
            raise RetrievalModelError(
                "Embedding model returned vectors with inconsistent dimensions"
            )
        if expected_dimension and len(vector) != expected_dimension:
            raise RetrievalModelError(
                "Embedding dimension contract failed: "
                f"expected {expected_dimension}, received {len(vector)}"
            )
        norm = math.sqrt(sum(value * value for value in vector))
        if not math.isfinite(norm) or norm <= 0:
            raise RetrievalModelError("Embedding model returned a zero vector")
        normalized = [value / norm for value in vector]
        normalized_norm = math.sqrt(sum(value * value for value in normalized))
        if abs(normalized_norm - 1.0) > 1e-5:
            raise RetrievalModelError("Embedding normalization contract failed")
        vectors.append(normalized)
    return vectors


def llama_openai_base_url(base_url: str) -> str:
    endpoint = base_url.rstrip("/")
    return endpoint if endpoint.endswith("/v1") else f"{endpoint}/v1"
