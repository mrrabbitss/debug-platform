"""Token-aware prompt governance and transient evidence spill handles."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from math import ceil
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.core.config import get_settings
from app.core.utils import json_dumps, json_loads


def estimate_tokens(value: Any) -> int:
    """Return a deterministic conservative token estimate without a tokenizer.

    Three UTF-8 bytes per token slightly overestimates most English JSON while
    remaining close enough for CJK text to make prompt admission decisions.
    Provider-reported usage remains authoritative after the request.
    """

    rendered = value if isinstance(value, str) else json_dumps(value)
    if not rendered:
        return 0
    return max(1, ceil(len(rendered.encode("utf-8")) / 3))


def _truncate_tokens(text: str, token_limit: int) -> str:
    if estimate_tokens(text) <= token_limit:
        return text
    byte_limit = max(1, token_limit * 3)
    return text.encode("utf-8")[:byte_limit].decode("utf-8", errors="ignore")


class ContextWindowPolicy(BaseModel):
    context_window_tokens: int = Field(default=131_072, ge=8_192, le=10_000_000)
    reserved_output_tokens: int = Field(default=32_768, ge=256, le=2_000_000)
    safety_margin_tokens: int = Field(default=2_048, ge=0, le=1_000_000)
    target_occupancy: float = Field(default=0.85, gt=0.1, le=0.98)
    max_item_tokens: int = Field(default=10_000, ge=256, le=1_000_000)
    preview_tokens: int = Field(default=768, ge=64, le=100_000)
    spill_chunk_tokens: int = Field(default=3_000, ge=256, le=100_000)

    @model_validator(mode="after")
    def validate_reserve(self) -> "ContextWindowPolicy":
        if self.reserved_output_tokens + self.safety_margin_tokens >= self.context_window_tokens:
            raise ValueError("Context output reserve must leave room for model input")
        return self

    @property
    def input_budget_tokens(self) -> int:
        available = (
            self.context_window_tokens
            - self.reserved_output_tokens
            - self.safety_margin_tokens
        )
        return max(1_024, int(available * self.target_occupancy))


class EvidenceSpillStore:
    """Run-scoped, in-memory storage for prompt content that does not fit.

    Handles are locators, not evidence identifiers. Resolving a handle returns
    the original evidence ID when one exists and a continuation handle when the
    content needs more than one bounded chunk.
    """

    def __init__(self, *, chunk_tokens: int = 3_000) -> None:
        self.chunk_tokens = max(256, chunk_tokens)
        self._records: dict[str, dict[str, Any]] = {}
        self._root_handles: dict[str, str] = {}

    @property
    def handle_ids(self) -> set[str]:
        return set(self._records)

    def _handle_for(self, digest: str, offset: int) -> str:
        return f"SPILL-{digest[:20]}-{offset}"

    def spill(
        self,
        content: str,
        *,
        source_evidence_id: str | None = None,
        title: str | None = None,
        locator: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        source_id = str(source_evidence_id or "").strip()
        digest = sha256(f"{source_id}\0{content}".encode("utf-8")).hexdigest()
        handle_id = self._root_handles.get(digest) or self._handle_for(digest, 0)
        self._root_handles[digest] = handle_id
        self._records.setdefault(handle_id, {
            "digest": digest,
            "offset": 0,
            "content": content,
            "source_evidence_id": source_id,
            "title": str(title or source_id or "Spilled prompt content")[:500],
            "locator": deepcopy(locator or {}),
        })
        return {
            "handle_id": handle_id,
            "source_evidence_id": source_id or None,
            "title": str(title or source_id or "Spilled prompt content")[:500],
            "estimated_tokens": estimate_tokens(content),
            "retrieval_tool": "get_evidence",
            "citation_allowed": False,
        }

    def resolve(self, handle_id: str) -> dict[str, Any] | None:
        record = self._records.get(handle_id)
        if record is None:
            return None
        content = str(record["content"])
        offset = int(record["offset"])
        remaining = content[offset:]
        chunk = _truncate_tokens(remaining, self.chunk_tokens)
        end = offset + len(chunk)
        continuation_handle: str | None = None
        if end < len(content):
            continuation_handle = self._handle_for(str(record["digest"]), end)
            self._records.setdefault(continuation_handle, {
                **record,
                "offset": end,
            })
        line_start = content.count("\n", 0, offset) + 1
        line_end = line_start + chunk.count("\n")
        source_id = str(record.get("source_evidence_id") or "")
        return {
            "evidence_id": source_id or handle_id,
            "source_type": "context_spill",
            "title": record["title"],
            "content": chunk,
            "spill_handle_id": handle_id,
            "continuation_handle": continuation_handle,
            "content_complete": continuation_handle is None,
            "line_start": line_start,
            "line_end": line_end,
            "metadata": {
                **deepcopy(record.get("locator") or {}),
                "citation_allowed": False,
            },
        }


_BULKY_SECTIONS = {
    "mandatory_method_documents": 0.25,
    "ranked_log_evidence": 0.30,
    "prior_rounds": 0.10,
    "search_observations": 0.20,
    "fault_tree_items": 0.15,
}
_IDENTITY_KEYS = {
    "id", "evidence_id", "document_id", "method_document_id", "item_id",
    "title", "label", "role", "version", "section", "status", "attempted",
    "line_start", "line_end", "source_file", "artifact_id", "artifact_source",
    "pattern_id", "recommended_tools", "content_handle", "content_tokens",
    "content_truncated",
}


class ContextGovernor:
    """Admit prompt sections by token budget and expose every content spill."""

    def __init__(self, policy: ContextWindowPolicy) -> None:
        self.policy = policy
        self._compactions = 0

    @staticmethod
    def _source_id(item: dict[str, Any]) -> str | None:
        for key in ("evidence_id", "id", "document_id", "method_document_id", "item_id"):
            if item.get(key):
                return str(item[key])
        return None

    @staticmethod
    def _title(item: dict[str, Any]) -> str | None:
        for key in ("title", "label", "source_file", "section"):
            if item.get(key):
                return str(item[key])
        return None

    def _compact_content(
        self,
        value: Any,
        *,
        preview_tokens: int,
        spill_store: EvidenceSpillStore,
        parent: dict[str, Any] | None = None,
    ) -> Any:
        if isinstance(value, list):
            return [
                self._compact_content(
                    item,
                    preview_tokens=preview_tokens,
                    spill_store=spill_store,
                    parent=item if isinstance(item, dict) else parent,
                )
                for item in value
            ]
        if not isinstance(value, dict):
            return deepcopy(value)
        source = value
        rendered: dict[str, Any] = {}
        for key, item in source.items():
            if key == "content" and isinstance(item, str):
                token_count = estimate_tokens(item)
                if token_count > preview_tokens:
                    descriptor = spill_store.spill(
                        item,
                        source_evidence_id=self._source_id(source),
                        title=self._title(source),
                        locator={
                            key: deepcopy(source.get(key))
                            for key in ("source_file", "line_start", "line_end", "artifact_id")
                            if source.get(key) is not None
                        },
                    )
                    rendered[key] = _truncate_tokens(item, preview_tokens) + "…[context spill]"
                    rendered["content_handle"] = descriptor["handle_id"]
                    rendered["content_tokens"] = token_count
                    rendered["content_truncated"] = True
                    self._compactions += 1
                else:
                    rendered[key] = item
                continue
            rendered[key] = self._compact_content(
                item,
                preview_tokens=preview_tokens,
                spill_store=spill_store,
                parent=source,
            )
        return rendered

    def _identity_skeleton(self, item: Any) -> Any:
        if not isinstance(item, dict):
            return item
        skeleton = {
            key: deepcopy(value)
            for key, value in item.items()
            if key in _IDENTITY_KEYS
        }
        if item.get("description"):
            skeleton["description"] = _truncate_tokens(str(item["description"]), 96)
        if item.get("recommended_search_terms"):
            skeleton["recommended_search_terms"] = item["recommended_search_terms"][:8]
        return skeleton or {"context_compacted": True}

    def _fit_section(
        self,
        name: str,
        value: Any,
        allocation: int,
        spill_store: EvidenceSpillStore,
    ) -> tuple[Any, dict[str, Any]]:
        original_tokens = estimate_tokens(value)
        item_count = len(value) if isinstance(value, list) else None
        per_item = allocation // max(1, item_count or 1)
        preview_tokens = min(
            self.policy.max_item_tokens,
            max(64, min(self.policy.preview_tokens, int(per_item * 0.65))),
        )
        compacted = self._compact_content(
            value,
            preview_tokens=preview_tokens,
            spill_store=spill_store,
        )
        omitted = 0
        required_identity = name in {"mandatory_method_documents", "fault_tree_items"}
        if estimate_tokens(compacted) > allocation and isinstance(compacted, list):
            if required_identity:
                compacted = [self._identity_skeleton(item) for item in compacted]
                self._compactions += 1
            else:
                kept: list[Any] = []
                for item in compacted:
                    candidate = [*kept, item]
                    if kept and estimate_tokens(candidate) > allocation:
                        break
                    kept.append(item)
                omitted = max(0, len(compacted) - len(kept))
                compacted = kept
                if omitted:
                    compacted.append({
                        "context_compaction": {
                            "omitted_items": omitted,
                            "reason": "section_token_budget",
                            "retrieval_hint": "Use search_log, search_knowledge, or get_evidence for more evidence.",
                        }
                    })
                    self._compactions += 1
        kept_tokens = estimate_tokens(compacted)
        return compacted, {
            "original_tokens": original_tokens,
            "kept_tokens": kept_tokens,
            "allocation_tokens": allocation,
            "original_items": item_count,
            "omitted_items": omitted,
        }

    def govern(
        self,
        prompt: dict[str, Any],
        *,
        spill_store: EvidenceSpillStore,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        self._compactions = 0
        budget = self.policy.input_budget_tokens
        base = {key: value for key, value in prompt.items() if key not in _BULKY_SECTIONS}
        base_tokens = estimate_tokens(base)
        available = max(1_024, budget - base_tokens - 512)
        governed = deepcopy(base)
        section_metrics: dict[str, Any] = {}
        handles_before = len(spill_store.handle_ids)
        for name, ratio in _BULKY_SECTIONS.items():
            if name not in prompt:
                continue
            allocation = max(256, int(available * ratio))
            governed[name], section_metrics[name] = self._fit_section(
                name,
                prompt[name],
                allocation,
                spill_store,
            )
        estimated = estimate_tokens(governed)
        metrics = {
            "context_window_tokens": self.policy.context_window_tokens,
            "reserved_output_tokens": self.policy.reserved_output_tokens,
            "safety_margin_tokens": self.policy.safety_margin_tokens,
            "input_budget_tokens": budget,
            "estimated_input_tokens": estimated,
            "estimated_occupancy": round(
                estimated / self.policy.context_window_tokens, 6
            ),
            "within_budget": estimated <= budget,
            "compaction_count": self._compactions,
            "spill_handle_count": len(spill_store.handle_ids) - handles_before,
            "spill_handle_total": len(spill_store.handle_ids),
            "sections": section_metrics,
        }
        governed["context_governance"] = {
            "input_budget_tokens": budget,
            "estimated_input_tokens": estimated,
            "compaction_applied": self._compactions > 0,
            "spill_handle_count": len(spill_store.handle_ids),
            "instruction": (
                "content_handle is a retrieval locator, not evidence. Use get_evidence "
                "to read it; cite only the evidence_id returned by the tool."
            ),
        }
        metrics["estimated_input_tokens"] = estimate_tokens(governed)
        metrics["estimated_occupancy"] = round(
            metrics["estimated_input_tokens"] / self.policy.context_window_tokens,
            6,
        )
        metrics["within_budget"] = metrics["estimated_input_tokens"] <= budget
        return governed, metrics


def configured_context_policy(provider: Any) -> ContextWindowPolicy:
    settings = get_settings()
    profile = getattr(provider, "profile", None)
    config = json_loads(profile.config_json, {}) if profile else {}
    configured_output = int(config.get("max_tokens") or 0)
    context_window = int(
        config.get("context_window_tokens")
        or settings.diagnostic_context_window_tokens
    )
    requested_reserve = int(
        config.get("context_reserved_output_tokens")
        or configured_output
        or settings.diagnostic_context_reserved_output_tokens
    )
    # A legacy profile may have a very large max_tokens but no context-window
    # metadata. Keep startup compatible while leaving at least 1024 input tokens;
    # the settings UI now exposes both values so operators can enter the real limit.
    reserve = min(
        requested_reserve,
        max(256, context_window - settings.diagnostic_context_safety_margin_tokens - 1_024),
    )
    return ContextWindowPolicy(
        context_window_tokens=context_window,
        reserved_output_tokens=reserve,
        safety_margin_tokens=settings.diagnostic_context_safety_margin_tokens,
        target_occupancy=settings.diagnostic_context_target_occupancy,
        max_item_tokens=settings.diagnostic_context_max_item_tokens,
        preview_tokens=settings.diagnostic_context_preview_tokens,
        spill_chunk_tokens=settings.diagnostic_context_spill_chunk_tokens,
    )
