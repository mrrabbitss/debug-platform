"""Explainable duplicate/conflict hints; never silently merge or publish model knowledge."""
import re

from sqlalchemy import select

from app.core.utils import json_loads, mask_sensitive
from app.models import KnowledgeDocument
from app.services.knowledge_compiler import compile_markdown, digest
from app.services.knowledge_drafts import draft_for_document


def _terms(text: str) -> set[str]:
    normalized = re.sub(r"\s+", " ", text[:32000].casefold())
    words = set(re.findall(r"[a-z0-9_]{2,}", normalized))
    words.update(normalized[index:index + 3] for index in range(len(normalized) - 2)
                 if any('\u4e00' <= char <= '\u9fff' for char in normalized[index:index + 3]))
    return words


def quality_report(db, document: KnowledgeDocument, principal: dict | None = None) -> dict:
    draft = draft_for_document(db, document.id, author=str(principal.get("id") or "") if principal else None)
    snapshot = json_loads(draft.snapshot_json, {}) if draft and draft.status not in {"PUBLISHED", "ARCHIVED"} else {}
    content = snapshot.get("content", document.content)
    title = snapshot.get("title", document.title)
    compiled = compile_markdown(content)
    terms = _terms(content)
    findings = []
    from app.services.knowledge_access import visible_knowledge_clause
    candidates = list(db.scalars(select(KnowledgeDocument).where(KnowledgeDocument.id != document.id,
                                visible_knowledge_clause(principal) if principal else True)
                                .order_by(KnowledgeDocument.id).limit(2001)))
    for other in candidates[:2000]:
        exact = digest(other.content) == compiled["content_sha256"]
        other_terms = _terms(other.content)
        score = len(terms & other_terms) / max(1, len(terms | other_terms))
        same_title = title.strip().casefold() == other.title.strip().casefold()
        if exact or score >= 0.7 or same_title:
            numeric_changed = set(re.findall(r"\d+(?:\.\d+)?", content)) != set(re.findall(r"\d+(?:\.\d+)?", other.content))
            polarity_changed = bool(re.search(r"禁止|不应|不得|不得不|\bnot\b|\bnever\b", content, re.I)) != bool(re.search(r"禁止|不应|不得|不得不|\bnot\b|\bnever\b", other.content, re.I))
            findings.append({"document_id": other.id, "version": other.version, "title": mask_sensitive(other.title),
                             "review_status": other.review_status,
                             "kind": "EXACT_DUPLICATE" if exact else "POTENTIAL_CONFLICT" if numeric_changed or polarity_changed else "NEAR_DUPLICATE",
                             "similarity": round(score, 4), "numeric_difference": numeric_changed,
                             "polarity_difference": polarity_changed, "human_review_required": True})
    directions = sorted({hint for row in compiled["sections"] for hint in row["direction_hints"] if hint != "unclassified"})
    from app.services.diagnostic_methods import DiagnosticMethodDocument, compile_diagnostic_patterns, _document_role
    from app.services.fault_tree_coverage import compile_fault_tree_items
    source_type = snapshot.get("source_type", document.source_type)
    method = DiagnosticMethodDocument(id=document.id, title=title, source_type=source_type,
        version=document.version, device_type=document.device_type, module=document.module,
        content=content, content_sha256=compiled["content_sha256"], role=_document_role(source_type))
    patterns = compile_diagnostic_patterns([method])
    nodes = compile_fault_tree_items([method])
    return {"document_id": document.id, "content_sha256": compiled["content_sha256"],
            "patterns": [row.public_snapshot() for row in patterns],
            "fault_tree_nodes": [row.public_snapshot() for row in nodes],
            "compilation_review_required": True,
            "draft_version": draft.version if snapshot else None, "compiler": compiled,
            "method_outline": [{"section_id": row["id"], "heading": row["heading"], "directions": row["direction_hints"]}
                               for row in compiled["sections"]],
            "direction_hints": directions, "findings": findings[:100], "finding_count": len(findings),
            "comparison_count": min(len(candidates), 2000), "comparison_truncated": len(candidates) > 2000,
            "similarity_character_limit": 32000, "exact_hash_uses_full_content": True,
            "automatic_merge": False, "conflicts_are_advisory": True,
            "missing_method_sections": [name for name in ("log_analysis", "fault_tree", "solution", "verification") if name not in directions]}
