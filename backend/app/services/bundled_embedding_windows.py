"""Fit bundled BGE inputs to its 512-token context without dropping source text."""

from types import SimpleNamespace
from typing import Any

from app.services.retrieval_model_contracts import (
    RetrievalModelError,
    validated_embedding_vectors,
)

TOKEN_LIMIT = 512
MAX_WINDOWS = 1024


def split_embedding_text(text: str, count_tokens) -> list[tuple[str, int]]:
    """Split on text boundaries; concatenating windows reproduces the exact input."""
    pending = [text]
    result: list[tuple[str, int]] = []
    while pending:
        part = pending.pop()
        count = count_tokens(part)
        if count <= TOKEN_LIMIT:
            result.append((part, count))
            continue
        if len(part) <= 1 or len(result) + len(pending) + 2 > MAX_WINDOWS:
            raise RetrievalModelError("Bundled Embedding input exceeds the bounded window budget")
        middle = len(part) // 2
        boundary = max(part.rfind(mark, middle // 2, middle) for mark in ("\n", "。", " "))
        cut = boundary + 1 if boundary >= 0 else middle
        pending.extend((part[cut:], part[:cut]))
    return result


def create_windowed_embeddings(client, http_client, request: dict[str, Any], *, timeout: float):
    """Return one normalized vector per source, pooling all windows of long inputs.

    Short inputs retain their original vector. Long inputs use token-weighted
    pooling, preserving the existing chunk IDs and original evidence contents.
    Tokenization and inference stay on the already validated loopback sidecar.
    """
    origin = str(client.base_url).rstrip("/").removesuffix("/v1")

    def count_tokens(text: str) -> int:
        response = http_client.post(
            origin + "/tokenize",
            headers={"Authorization": f"Bearer {client.api_key}"},
            json={"content": text, "add_special": True},
            timeout=timeout,
        )
        response.raise_for_status()
        tokens = response.json().get("tokens")
        if not isinstance(tokens, list) or any(type(token) is not int for token in tokens):
            raise RetrievalModelError("Bundled Embedding tokenizer returned invalid tokens")
        return len(tokens)

    windows = []
    for source_index, text in enumerate(request["input"]):
        windows.extend((source_index, part, count) for part, count in split_embedding_text(text, count_tokens))
        if len(windows) > MAX_WINDOWS:
            raise RetrievalModelError("Bundled Embedding request exceeds the bounded window budget")
    pooled: dict[int, list[float]] = {}
    dimension = None
    prompt_tokens = 0
    total_tokens = 0
    start = 0
    while start < len(windows):
        end, tokens_in_batch = start, 0
        while end < len(windows) and tokens_in_batch + windows[end][2] <= TOKEN_LIMIT and end - start < 16:
            tokens_in_batch += windows[end][2]
            end += 1
        batch = windows[start:end]
        response = client.embeddings.create(**{**request, "input": [part for _, part, _ in batch]})
        ordered = sorted(response.data, key=lambda item: item.index)
        if [item.index for item in ordered] != list(range(len(batch))):
            raise RetrievalModelError("Embedding API violated the response index contract")
        vectors = validated_embedding_vectors(
            [item.embedding for item in ordered], expected_count=len(batch), expected_dimension=dimension,
        )
        dimension = len(vectors[0])
        for (source_index, _, count), vector in zip(batch, vectors, strict=True):
            accumulator = pooled.setdefault(source_index, [0.0] * dimension)
            for index, value in enumerate(vector):
                accumulator[index] += value * max(1, count - 2)
        usage = getattr(response, "usage", None)
        prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
        total_tokens += getattr(usage, "total_tokens", 0) or 0
        start = end
    vectors = validated_embedding_vectors(
        [pooled[index] for index in range(len(request["input"]))],
        expected_count=len(request["input"]), expected_dimension=dimension,
    )
    return SimpleNamespace(
        data=[SimpleNamespace(index=index, embedding=vector) for index, vector in enumerate(vectors)],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, total_tokens=total_tokens),
    )
