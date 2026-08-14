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


def _structured_user_payload(user_text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(user_text)
    except (TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _diagnostic_response(payload: dict[str, Any]) -> str | None:
    methods = payload.get("mandatory_method_documents")
    if not isinstance(methods, list):
        methods = []
    method_ids = [
        str(item.get("id"))
        for item in methods
        if isinstance(item, dict) and item.get("id")
    ]

    patterns = payload.get("compiled_patterns")
    if isinstance(patterns, list):
        selected = [
            str(item.get("id"))
            for item in patterns
            if isinstance(item, dict)
            and item.get("id")
            and "authentication failed" in str(item.get("text") or "").casefold()
        ]
        return json.dumps({
            "read_document_ids": method_ids,
            "selected_pattern_ids": selected[:20],
            "additional_keywords": [{
                "keyword": "authentication failed",
                "reason": "Synthetic issue description points to authentication failure",
                "relevance": 0.96,
            }],
            "hypotheses": ["Authentication configuration mismatch"],
            "screening_steps": [
                "Read every applicable synthetic method",
                "Scan the complete local log and rank matching evidence",
            ],
            "missing_information": [],
            "stop_conditions": ["All method patterns have been scanned"],
            "rationale": "Deterministic fake response for browser validation",
        }, ensure_ascii=False)

    if "round" in payload and "ranked_log_evidence" in payload:
        round_number = max(1, int(payload.get("round") or 1))
        fault_tree_items = [
            item for item in payload.get("fault_tree_items", [])
            if isinstance(item, dict) and item.get("id") and item.get("method_document_id")
        ]
        fault_tree_item_ids = [str(item["id"]) for item in fault_tree_items]
        assessments = [{
            "method_document_id": document_id,
            "relevance": "POSSIBLY_RELEVANT",
            "rationale": "Synthetic method remains applicable until evidence excludes it",
            "matched_signals": ["synthetic issue"],
        } for document_id in method_ids]
        checks = [{
            "check_id": f"synthetic-check-{round_number}-{index}",
            "method_document_id": document_id,
            "description": "Verify the method against ranked synthetic evidence",
            "evidence_needed": "A matching evidence ID or an explicit evidence gap",
            "completion_rule": "Record support, contradiction, or missing evidence",
            "fault_tree_item_ids": fault_tree_item_ids if index == 1 else [],
        } for index, document_id in enumerate(method_ids, start=1)]
        return json.dumps({
            "read_document_ids": method_ids,
            "method_assessments": assessments,
            "hypotheses": ["Synthetic shared-key mismatch"],
            "checks": checks,
            "search_queries": [{
                "query_id": f"synthetic-query-{round_number}",
                "query": f"synthetic diagnostic evidence round {round_number}",
                "method_document_ids": method_ids,
                "rationale": "Verify the synthetic method checks",
                "expected_evidence": "Synthetic support or contradiction",
                "fault_tree_item_ids": fault_tree_item_ids,
            }] if method_ids else [],
            "fault_tree_assessments": [{
                "item_id": str(item["id"]),
                "method_document_id": str(item["method_document_id"]),
                "status": "INSUFFICIENT_EVIDENCE",
                "rationale": "The browser fixture does not contain evidence for this optional local tree node.",
                "evidence_ids": [],
                "next_action": "Collect the GW/AP logs requested by this tree node.",
            } for item in fault_tree_items] if round_number == 1 else [],
            "evidence_gaps": [],
            "continue_analysis": round_number < 2,
            "stop_reason": (
                "MORE_EVIDENCE_NEEDED" if round_number < 2 else "ENOUGH_EVIDENCE"
            ),
        }, ensure_ascii=False)

    if "human_revision_instruction" in payload and "current_diagnosis" in payload:
        current = payload.get("current_diagnosis")
        revised = dict(current) if isinstance(current, dict) else {}
        evidence = payload.get("evidence")
        evidence_id = next((
            str(item.get("evidence_id"))
            for item in evidence if isinstance(item, dict) and item.get("evidence_id")
        ), "SYNTHETIC-EVIDENCE") if isinstance(evidence, list) else "SYNTHETIC-EVIDENCE"
        revised.update({
            "summary": "Synthetic human-requested GW/AP joint diagnosis revision.",
            "confirmed_facts": [{
                "statement": "The revision remains constrained to synthetic evidence.",
                "evidence_ids": [evidence_id],
            }],
            "hypotheses": [{
                "rank": 1,
                "title": "Synthetic cross-device authentication dependency",
                "description": "The GW/AP relationship must be checked before attribution.",
                "supporting_evidence": [evidence_id],
                "contradicting_evidence": [],
                "confidence_score": 0.76,
                "confidence_level": "MEDIUM",
                "priority": "P1",
                "needs_human_review": True,
            }],
            "recommended_actions": [{
                "priority": "P1",
                "action": "Compare the primary GW configuration with the secondary AP state.",
                "reason": "The requested revision requires cross-device verification.",
                "expected_result": "The dependency is confirmed or excluded.",
            }],
            "missing_information": [],
            "suspected_modules": ["GW", "AP", "AUTH"],
            "limitations": ["Synthetic revision requires explicit human approval."],
        })
        return json.dumps({
            "change_summary": "Added explicit GW/AP cross-device verification.",
            "assistant_message": "A diagnosis and report revision draft is ready for human review.",
            "revised_diagnosis": revised,
        }, ensure_ascii=False)

    evidence = payload.get("evidence")
    if "deterministic_result" in payload and isinstance(evidence, list):
        evidence_ids = [
            str(item.get("evidence_id"))
            for item in evidence
            if isinstance(item, dict) and item.get("evidence_id")
        ]
        evidence_id = evidence_ids[0] if evidence_ids else "SYNTHETIC-EVIDENCE"
        deterministic = payload.get("deterministic_result")
        coverage = (
            deterministic.get("diagnostic_planning", {}).get("fault_tree_coverage", {})
            if isinstance(deterministic, dict) else {}
        )
        conclusions = [{
            "item_id": str(item["id"]),
            "method_document_id": str(item["method_document_id"]),
            "status": str(item["status"]),
            "conclusion": str(item.get("rationale") or "Synthetic coverage conclusion"),
            "evidence_ids": list(item.get("evidence_ids") or []),
            "next_action": str(item.get("next_action") or "Human verification required"),
        } for item in coverage.get("items", []) if isinstance(item, dict) and item.get("id")]
        return json.dumps({
            "summary": "Synthetic evidence-constrained comprehensive diagnosis completed.",
            "confirmed_facts": [{
                "statement": "The synthetic log contains an authentication failure signal.",
                "evidence_ids": [evidence_id],
            }],
            "hypotheses": [{
                "rank": 1,
                "title": "Synthetic shared-key mismatch",
                "description": "The ranked evidence supports checking authentication configuration.",
                "supporting_evidence": [evidence_id],
                "contradicting_evidence": [],
                "confidence_score": 0.82,
                "confidence_level": "HIGH",
                "priority": "P1",
                "needs_human_review": True,
            }],
            "recommended_actions": [{
                "priority": "P1",
                "action": "Compare the configured shared key with the approved baseline.",
                "reason": "Authentication evidence is present.",
                "expected_result": "The mismatch is confirmed or excluded.",
            }],
            "missing_information": [],
            "suspected_modules": ["AUTH"],
            "limitations": ["Synthetic browser fixture only."],
            "fault_tree_conclusions": conclusions,
        }, ensure_ascii=False)

    if "question" in payload and "conversation_history" in payload:
        evidence = payload.get("evidence")
        evidence_id = next((
            str(item.get("evidence_id"))
            for item in evidence if isinstance(item, dict) and item.get("evidence_id")
        ), "SYNTHETIC-EVIDENCE") if isinstance(evidence, list) else "SYNTHETIC-EVIDENCE"
        return (
            "Synthetic asynchronous answer completed. The current hypothesis remains "
            f"subject to human verification; evidence_id={evidence_id}."
        )
    return None


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
            structured = _structured_user_payload(user_text)
            content = (
                _diagnostic_response(structured) if structured is not None else None
            ) or _mapped_response(RESPONSES["initial"], user_text)
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
