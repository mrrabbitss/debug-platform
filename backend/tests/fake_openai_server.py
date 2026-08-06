"""Deterministic OpenAI-compatible test double used by browser and CI tests.

The service is deliberately content-safe and loads only the committed synthetic
golden responses. Select fault injection with ``X-Fake-Mode`` or a model name:
``golden-timeout``, ``golden-rate-limit``, ``golden-bad-json`` or
``golden-interrupted``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import re
import time
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import uvicorn


REPO_ROOT = Path(__file__).resolve().parents[2]
RESPONSES = json.loads(
    (REPO_ROOT / "sample_data" / "golden_incident" / "fake_model_responses.json")
    .read_text(encoding="utf-8")
)


class ChatRequest(BaseModel):
    model: str
    messages: list[dict[str, Any]] = Field(default_factory=list)
    stream: bool = False


class EmbeddingRequest(BaseModel):
    model: str
    input: str | list[str]


class RerankRequest(BaseModel):
    model: str
    query: str
    documents: list[str]
    top_n: int | None = None


def _mode(model: str, header: str | None) -> str:
    if header:
        return header.strip().lower()
    for candidate in ("timeout", "rate-limit", "bad-json", "interrupted"):
        if candidate in model.lower():
            return candidate
    return "success"


def _usage(messages: list[dict[str, Any]], content: str) -> dict[str, int]:
    prompt_chars = sum(len(str(item.get("content", ""))) for item in messages)
    prompt_tokens = max(1, prompt_chars // 4)
    completion_tokens = max(1, len(content) // 4)
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


def _mapped_response(template: dict[str, Any], user_text: str) -> str:
    """Map logical fixture refs to the browser's actual folder enumeration order."""
    logical_by_name = {
        "error.txt": "SRC-0001",
        "analysis.html": "SRC-0002",
        "solution.docx": "SRC-0003",
        "validation.pdf": "SRC-0004",
    }
    actual_by_logical: dict[str, str] = {}
    for source_ref, relative_path in re.findall(
        r"(?m)^##\s+(SRC-\d+)\s+\|\s+([^|]+?)\s+\|",
        user_text,
    ):
        filename = Path(relative_path.strip()).name.casefold()
        logical = logical_by_name.get(filename)
        if logical:
            actual_by_logical[logical] = source_ref
    rendered = json.dumps(template, ensure_ascii=False)
    for index, logical in enumerate(logical_by_name.values(), start=1):
        rendered = rendered.replace(logical, f"__GOLDEN_SOURCE_REF_{index}__")
    for index, logical in enumerate(logical_by_name.values(), start=1):
        actual = actual_by_logical.get(logical, logical)
        rendered = rendered.replace(f"__GOLDEN_SOURCE_REF_{index}__", actual)
    return rendered


def create_app(*, timeout_seconds: float = 2.0) -> FastAPI:
    app = FastAPI(title="GW/AP Golden Fake OpenAI", version="1.0")

    @app.get("/healthz")
    def health() -> dict[str, Any]:
        return {"status": "ok", "synthetic_only": True}

    @app.get("/v1/models")
    def models() -> dict[str, Any]:
        return {
            "object": "list",
            "data": [
                {"id": "golden-curation", "object": "model", "owned_by": "test"},
                {"id": "golden-embedding", "object": "model", "owned_by": "test"},
                {"id": "golden-reranker", "object": "model", "owned_by": "test"},
            ],
        }

    @app.post("/v1/chat/completions")
    async def chat(
        payload: ChatRequest,
        x_fake_mode: str | None = Header(default=None),
    ) -> JSONResponse:
        selected_mode = _mode(payload.model, x_fake_mode)
        if selected_mode == "timeout":
            await asyncio.sleep(timeout_seconds)
        if selected_mode == "rate-limit":
            raise HTTPException(429, "synthetic rate limit")
        if selected_mode == "interrupted":
            return JSONResponse(
                status_code=503,
                content={"error": {"type": "synthetic_interruption", "message": "upstream interrupted"}},
                headers={"Retry-After": "0"},
            )

        user_text = "\n".join(
            str(item.get("content", ""))
            for item in payload.messages
            if item.get("role") == "user"
        )
        if "MODEL_CONNECTION_OK" in user_text:
            content = "MODEL_CONNECTION_OK"
        elif selected_mode == "bad-json":
            content = "{this is not valid json"
        elif "E2E_CORRECTION_ADD_PEER_REVIEW" in user_text:
            content = _mapped_response(RESPONSES["refined"], user_text)
        else:
            content = _mapped_response(RESPONSES["initial"], user_text)
        now = int(time.time())
        return JSONResponse({
            "id": f"chatcmpl-golden-{now}",
            "object": "chat.completion",
            "created": now,
            "model": payload.model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }],
            "usage": _usage(payload.messages, content),
        })

    @app.post("/v1/embeddings")
    def embeddings(
        payload: EmbeddingRequest,
        x_fake_mode: str | None = Header(default=None),
    ) -> JSONResponse:
        selected_mode = _mode(payload.model, x_fake_mode)
        if selected_mode == "rate-limit":
            raise HTTPException(429, "synthetic rate limit")
        inputs = [payload.input] if isinstance(payload.input, str) else payload.input
        data = []
        for index, value in enumerate(inputs):
            seed = sum(ord(character) for character in value)
            vector = [round(((seed + offset * 17) % 101) / 100, 6) for offset in range(8)]
            data.append({"object": "embedding", "index": index, "embedding": vector})
        return JSONResponse({"object": "list", "model": payload.model, "data": data})

    @app.post("/v1/rerank")
    def rerank(
        payload: RerankRequest,
        x_fake_mode: str | None = Header(default=None),
    ) -> JSONResponse:
        selected_mode = _mode(payload.model, x_fake_mode)
        if selected_mode == "rate-limit":
            raise HTTPException(429, "synthetic rate limit")
        query_terms = set(payload.query.casefold().split())
        scored = []
        for index, document in enumerate(payload.documents):
            terms = set(document.casefold().split())
            scored.append({
                "index": index,
                "relevance_score": len(query_terms.intersection(terms)) / max(len(query_terms), 1),
            })
        scored.sort(key=lambda item: (-item["relevance_score"], item["index"]))
        return JSONResponse({"results": scored[: payload.top_n or len(scored)]})

    return app


app = create_app()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18081)
    parser.add_argument("--timeout-seconds", type=float, default=2.0)
    args = parser.parse_args()
    uvicorn.run(create_app(timeout_seconds=args.timeout_seconds), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
