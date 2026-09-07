"""Adapt versioned retrieval hits into agent search candidates."""
from typing import Any
from sqlalchemy.orm import Session
from app.services.diagnostic_scope import knowledge_matches_joint_diagnostic_scope
from app.services.rag import retriever


def knowledge_candidates(
    db: Session,
    query: str,
    *,
    case_id: str,
    device_type: str | None,
    limit: int,
    joint_diagnostic_scope: bool = False,
    knowledge_view: list[str] | None = None,
) -> list[dict[str, Any]]:
    hits = retriever.search(
        query,
        db=db,
        case_id=case_id,
        device_type=device_type,
        top_k=limit * 2 if joint_diagnostic_scope else limit,
        include_code_symbols=False,
        apply_models=False,
        knowledge_view=knowledge_view,
    )
    return [
        {
            "evidence_id": hit.evidence_id,
            "source_type": hit.source_type,
            "title": hit.title,
            "content": hit.content,
            "source_score": hit.score,
            "metadata": hit.metadata,
            "paths": [],
        }
        for hit in hits
        if (
            not joint_diagnostic_scope
            or knowledge_matches_joint_diagnostic_scope(
                str(hit.metadata.get("device_type") or "") or None
            )
        )
    ][:limit]
