from __future__ import annotations

from collections import defaultdict
import hashlib
import re
from typing import Any

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.core.utils import json_dumps, json_loads, new_id, stable_id, utcnow
from app.models import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeEntity,
    KnowledgeEntityMention,
    KnowledgeGraphState,
    KnowledgeRelation,
)
from app.services.jobs import JobContext
from app.services.knowledge_methods import (
    STRUCTURED_SOURCE_TYPES,
    parse_markdown_sections,
)
from app.services.rag import tokenize


GRAPH_STATE_ID = "domain"
EVENT_CODE_PATTERN = re.compile(
    r"\b[A-Z][A-Z0-9]{1,31}(?:[_-][A-Z0-9]{2,32})+\b"
)
LIST_PREFIX = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)、]\s*)")
SECTION_ENTITY_MAPPING = {
    "error_form": ("SYMPTOM", "HAS_SYMPTOM"),
    "log_analysis": ("LOG_PATTERN", "HAS_LOG_PATTERN"),
    "localization": ("DIAGNOSIS_STEP", "HAS_DIAGNOSIS_STEP"),
    "solution": ("SOLUTION", "RESOLVED_BY"),
    "validation": ("VALIDATION", "VERIFIED_BY"),
    "scope": ("SCOPE", "APPLIES_TO"),
}
RELATION_WEIGHTS = {
    "CAUSED_BY": 1.0,
    "RESOLVED_BY": 1.0,
    "HAS_SYMPTOM": 1.0,
    "HAS_LOG_PATTERN": 0.95,
    "HAS_DIAGNOSIS_STEP": 0.9,
    "MENTIONS_EVENT_CODE": 0.9,
    "VERIFIED_BY": 0.85,
    "APPLIES_TO": 0.8,
}
ROOT_CAUSE_HEADING_TERMS = (
    "根因",
    "故障原因",
    "问题原因",
    "root cause",
    "failure cause",
)


def _normalize_name(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


def _clean_items(text: str, *, limit: int = 30) -> list[str]:
    items: list[str] = []
    seen: set[str] = set()
    for raw_line in text.splitlines():
        line = LIST_PREFIX.sub("", raw_line).strip()
        line = re.sub(r"\s+", " ", line).strip(" :：")
        if not line or line.startswith(("#", "```", "~~~")):
            continue
        if len(line) > 512:
            line = line[:509] + "..."
        normalized = _normalize_name(line)
        if len(normalized) < 2 or normalized in seen:
            continue
        seen.add(normalized)
        items.append(line)
        if len(items) >= limit:
            break
    return items


def _entity_key(entity_type: str, name: str) -> tuple[str, str]:
    return entity_type, _normalize_name(name)


def _explicit_root_cause_items(content: str) -> list[str]:
    sections: list[str] = []
    buffer: list[str] = []
    collecting = False
    fence_marker: str | None = None
    for line in content.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        stripped = line.lstrip()
        marker = stripped[:3] if stripped.startswith(("```", "~~~")) else None
        if marker:
            if fence_marker is None:
                fence_marker = marker
            elif marker == fence_marker:
                fence_marker = None
            if collecting:
                buffer.append(line)
            continue
        heading = (
            re.match(r"^#{1,6}\s+(.+?)\s*$", line)
            if fence_marker is None
            else None
        )
        if heading:
            if collecting and buffer:
                sections.append("\n".join(buffer))
            normalized = _normalize_name(heading.group(1))
            collecting = any(
                term in normalized for term in ROOT_CAUSE_HEADING_TERMS
            )
            buffer = []
        elif collecting:
            buffer.append(line)
    if collecting and buffer:
        sections.append("\n".join(buffer))

    items = _clean_items("\n".join(sections))
    inline_pattern = re.compile(
        r"^(?:根因|故障原因|问题原因|root cause)\s*[:：]\s*(.+)$",
        re.IGNORECASE,
    )
    for line in content.splitlines():
        cleaned = LIST_PREFIX.sub("", line).strip()
        match = inline_pattern.match(cleaned)
        if match:
            items.extend(_clean_items(match.group(1), limit=5))
    return list(dict.fromkeys(items))[:30]


def _document_facts(document: KnowledgeDocument) -> dict[str, Any]:
    entities: dict[tuple[str, str], dict[str, Any]] = {}
    relations: list[dict[str, Any]] = []

    def add_entity(
        entity_type: str,
        name: str | None,
        *,
        confidence: float = 1.0,
        excerpt: str | None = None,
    ) -> tuple[str, str] | None:
        if not name:
            return None
        canonical = re.sub(r"\s+", " ", str(name).strip())
        if not canonical:
            return None
        canonical = canonical[:512]
        key = _entity_key(entity_type, canonical)
        existing = entities.get(key)
        if existing is None or confidence > existing["confidence"]:
            entities[key] = {
                "entity_type": entity_type,
                "canonical_name": canonical,
                "normalized_name": key[1],
                "confidence": confidence,
                "excerpt": (excerpt or canonical)[:2000],
            }
        return key

    def add_relation(
        source: tuple[str, str] | None,
        target: tuple[str, str] | None,
        relation_type: str,
        *,
        confidence: float,
        excerpt: str,
    ) -> None:
        if source and target and source != target:
            relations.append({
                "source": source,
                "target": target,
                "relation_type": relation_type,
                "confidence": confidence,
                "excerpt": excerpt[:2000],
            })

    topic_type = (
        "SYMPTOM"
        if document.source_type in STRUCTURED_SOURCE_TYPES
        else "KNOWLEDGE_TOPIC"
    )
    topic = add_entity(topic_type, document.title, excerpt=document.title)
    for field, entity_type in (
        (document.device_type, "DEVICE_TYPE"),
        (document.device_model, "DEVICE_MODEL"),
        (document.firmware_range, "FIRMWARE"),
        (document.module, "MODULE"),
    ):
        target = add_entity(entity_type, field, excerpt=str(field or ""))
        add_relation(
            topic,
            target,
            "APPLIES_TO",
            confidence=1.0,
            excerpt=str(field or ""),
        )

    if document.source_type in STRUCTURED_SOURCE_TYPES:
        parsed = parse_markdown_sections(document.content)
        for section_key, (entity_type, relation_type) in (
            SECTION_ENTITY_MAPPING.items()
        ):
            for item in _clean_items(
                str(parsed["sections"].get(section_key, ""))
            ):
                target = add_entity(
                    entity_type,
                    item,
                    confidence=0.9,
                    excerpt=item,
                )
                add_relation(
                    topic,
                    target,
                    relation_type,
                    confidence=0.9,
                    excerpt=item,
                )
        for item in _explicit_root_cause_items(document.content):
            target = add_entity(
                "ROOT_CAUSE",
                item,
                confidence=0.95,
                excerpt=item,
            )
            add_relation(
                topic,
                target,
                "CAUSED_BY",
                confidence=0.95,
                excerpt=item,
            )

    codes = sorted(set(EVENT_CODE_PATTERN.findall(
        f"{document.title}\n{document.content}"
    )))[:100]
    for code in codes:
        target = add_entity("EVENT_CODE", code, excerpt=code)
        add_relation(
            topic,
            target,
            "MENTIONS_EVENT_CODE",
            confidence=0.95,
            excerpt=code,
        )

    unique_relations: dict[
        tuple[tuple[str, str], tuple[str, str], str],
        dict[str, Any],
    ] = {}
    for relation in relations:
        key = (
            relation["source"],
            relation["target"],
            relation["relation_type"],
        )
        current = unique_relations.get(key)
        if current is None or relation["confidence"] > current["confidence"]:
            unique_relations[key] = relation
    return {
        "entities": entities,
        "relations": list(unique_relations.values())[:300],
    }


def _source_signature(documents: list[KnowledgeDocument]) -> str:
    digest = hashlib.sha256()
    for document in sorted(documents, key=lambda item: item.id):
        digest.update(
            (
                f"{document.id}\0{document.version}\0"
                f"{document.lock_version}\0{document.review_status}\0"
            ).encode("utf-8")
        )
        digest.update(hashlib.sha256(document.content.encode("utf-8")).digest())
    return digest.hexdigest()


def _chunk_for_text(
    chunks: list[KnowledgeChunk],
    value: str,
) -> KnowledgeChunk | None:
    normalized = _normalize_name(value)
    for chunk in chunks:
        if normalized and normalized in _normalize_name(chunk.content):
            return chunk
    return chunks[0] if chunks else None


def _cleanup_generation(db: Session, generation_id: str) -> None:
    db.execute(delete(KnowledgeRelation).where(
        KnowledgeRelation.generation_id == generation_id
    ))
    db.execute(delete(KnowledgeEntityMention).where(
        KnowledgeEntityMention.generation_id == generation_id
    ))
    db.execute(delete(KnowledgeEntity).where(
        KnowledgeEntity.generation_id == generation_id
    ))


def rebuild_domain_graph_job(ctx: JobContext) -> dict[str, Any]:
    generation_id = new_id("KGEN")
    previous_generation: str | None = None
    build_marker_json = ""
    source_signature = ""
    try:
        with SessionLocal() as db:
            state = db.get(KnowledgeGraphState, GRAPH_STATE_ID)
            if not state:
                state = KnowledgeGraphState(
                    id=GRAPH_STATE_ID,
                    status="NOT_BUILT",
                )
                db.add(state)
                db.flush()
            previous_generation = state.active_generation_id
            build_metadata = json_loads(state.metadata_json, {})
            build_metadata.pop("stale_reason", None)
            build_metadata.pop("stale_at", None)
            build_metadata.update({
                "building_generation_id": generation_id,
                "build_started_at": utcnow().isoformat(),
            })
            build_marker_json = json_dumps(build_metadata)
            state.building_generation_id = generation_id
            state.status = "BUILDING"
            state.metadata_json = build_marker_json
            state.error_message = None
            db.commit()

        ctx.update(5, "Reading active reviewed knowledge")
        with SessionLocal() as db:
            documents = list(db.scalars(
                select(KnowledgeDocument)
                .where(
                    KnowledgeDocument.active.is_(True),
                    KnowledgeDocument.review_status == "ACTIVE",
                )
                .order_by(KnowledgeDocument.id)
            ).all())
            chunks_by_document: dict[str, list[KnowledgeChunk]] = defaultdict(list)
            if documents:
                for chunk in db.scalars(
                    select(KnowledgeChunk)
                    .where(KnowledgeChunk.document_id.in_(
                        [document.id for document in documents]
                    ))
                    .order_by(
                        KnowledgeChunk.document_id,
                        KnowledgeChunk.chunk_index,
                    )
                ).all():
                    chunks_by_document[chunk.document_id].append(chunk)
            source_signature = _source_signature(documents)

        entity_definitions: dict[tuple[str, str], dict[str, Any]] = {}
        document_facts: list[
            tuple[KnowledgeDocument, dict[str, Any]]
        ] = []
        for index, document in enumerate(documents):
            ctx.raise_if_cancelled()
            facts = _document_facts(document)
            document_facts.append((document, facts))
            for key, entity in facts["entities"].items():
                entity_definitions.setdefault(key, entity)
            if index % 25 == 0:
                ctx.update(
                    5 + int(20 * (index + 1) / max(len(documents), 1)),
                    f"Extracted graph facts: {index + 1}/{len(documents)}",
                )

        entity_ids: dict[tuple[str, str], str] = {}
        logical_ids: dict[tuple[str, str], str] = {}
        pending_entities: list[KnowledgeEntity] = []
        with SessionLocal() as db:
            for key in sorted(entity_definitions):
                definition = entity_definitions[key]
                logical_id = stable_id("KENT", *key)
                entity_id = stable_id(
                    "KENTREV",
                    logical_id,
                    generation_id,
                )
                logical_ids[key] = logical_id
                entity_ids[key] = entity_id
                pending_entities.append(KnowledgeEntity(
                    id=entity_id,
                    logical_id=logical_id,
                    generation_id=generation_id,
                    entity_type=definition["entity_type"],
                    canonical_name=definition["canonical_name"],
                    normalized_name=definition["normalized_name"],
                    aliases_json="[]",
                    metadata_json=json_dumps({
                        "extractor": "deterministic_knowledge_graph_v1",
                    }),
                ))
                if len(pending_entities) >= 500:
                    db.add_all(pending_entities)
                    db.commit()
                    pending_entities = []
            if pending_entities:
                db.add_all(pending_entities)
                db.commit()

        ctx.update(35, "Linking entities to reviewed evidence")
        mention_count = 0
        relation_count = 0
        with SessionLocal() as db:
            pending_mentions: list[KnowledgeEntityMention] = []
            pending_relations: list[KnowledgeRelation] = []
            for index, (document, facts) in enumerate(document_facts):
                ctx.raise_if_cancelled()
                chunks = chunks_by_document.get(document.id, [])
                mention_chunk: dict[tuple[str, str], KnowledgeChunk | None] = {}
                for key, entity in facts["entities"].items():
                    chunk = _chunk_for_text(
                        chunks,
                        entity["canonical_name"],
                    )
                    mention_chunk[key] = chunk
                    pending_mentions.append(KnowledgeEntityMention(
                        id=stable_id(
                            "KMENT",
                            generation_id,
                            logical_ids[key],
                            document.id,
                            chunk.id if chunk else "",
                        ),
                        generation_id=generation_id,
                        entity_id=entity_ids[key],
                        document_id=document.id,
                        chunk_id=chunk.id if chunk else None,
                        excerpt=entity["excerpt"],
                        confidence=entity["confidence"],
                        metadata_json=json_dumps({
                            "source_type": document.source_type,
                        }),
                    ))
                    mention_count += 1
                for relation in facts["relations"]:
                    source_key = relation["source"]
                    target_key = relation["target"]
                    evidence_chunk = mention_chunk.get(target_key)
                    logical_id = stable_id(
                        "KREL",
                        logical_ids[source_key],
                        logical_ids[target_key],
                        relation["relation_type"],
                    )
                    pending_relations.append(KnowledgeRelation(
                        id=stable_id(
                            "KRELREV",
                            logical_id,
                            generation_id,
                            document.id,
                        ),
                        logical_id=logical_id,
                        generation_id=generation_id,
                        source_entity_id=entity_ids[source_key],
                        target_entity_id=entity_ids[target_key],
                        relation_type=relation["relation_type"],
                        evidence_document_id=document.id,
                        evidence_chunk_id=(
                            evidence_chunk.id if evidence_chunk else None
                        ),
                        confidence=relation["confidence"],
                        metadata_json=json_dumps({
                            "excerpt": relation["excerpt"],
                        }),
                    ))
                    relation_count += 1
                if (
                    len(pending_mentions) + len(pending_relations) >= 1000
                    or index == len(document_facts) - 1
                ):
                    db.add_all(pending_mentions)
                    db.add_all(pending_relations)
                    db.commit()
                    pending_mentions = []
                    pending_relations = []
                if index % 25 == 0:
                    ctx.update(
                        35 + int(
                            50 * (index + 1) / max(len(document_facts), 1)
                        ),
                        f"Linked graph evidence: {index + 1}/{len(document_facts)}",
                    )

        result = {
            "generation_id": generation_id,
            "previous_generation_id": previous_generation,
            "documents": len(documents),
            "entities": len(entity_ids),
            "mentions": mention_count,
            "relations": relation_count,
            "extractor": "deterministic_knowledge_graph_v1",
            "source_signature": source_signature,
        }
        with SessionLocal() as db:
            current_documents = list(db.scalars(
                select(KnowledgeDocument)
                .where(
                    KnowledgeDocument.active.is_(True),
                    KnowledgeDocument.review_status == "ACTIVE",
                )
                .order_by(KnowledgeDocument.id)
            ).all())
            if _source_signature(current_documents) != source_signature:
                raise RuntimeError(
                    "Reviewed knowledge changed during graph build; rebuild again"
                )
            expected_generation = (
                KnowledgeGraphState.active_generation_id.is_(None)
                if previous_generation is None
                else KnowledgeGraphState.active_generation_id
                == previous_generation
            )
            published = db.execute(
                update(KnowledgeGraphState)
                .where(
                    KnowledgeGraphState.id == GRAPH_STATE_ID,
                    KnowledgeGraphState.building_generation_id
                    == generation_id,
                    KnowledgeGraphState.metadata_json == build_marker_json,
                    expected_generation,
                )
                .values(
                    active_generation_id=generation_id,
                    building_generation_id=None,
                    status="READY",
                    metadata_json=json_dumps({
                        **result,
                        "published_at": utcnow().isoformat(),
                    }),
                    error_message=None,
                    updated_at=utcnow(),
                )
            )
            if published.rowcount != 1:
                db.rollback()
                raise RuntimeError(
                    "Domain graph build was superseded by another generation"
                )
            ctx.complete_in_transaction(
                db,
                result,
                message="Domain knowledge graph rebuilt",
            )
            db.commit()
            retained_generations = [generation_id]
            if (
                previous_generation
                and previous_generation != generation_id
            ):
                retained_generations.append(previous_generation)
            db.execute(delete(KnowledgeRelation).where(
                KnowledgeRelation.generation_id.not_in(retained_generations)
            ))
            db.execute(delete(KnowledgeEntityMention).where(
                KnowledgeEntityMention.generation_id.not_in(
                    retained_generations
                )
            ))
            db.execute(delete(KnowledgeEntity).where(
                KnowledgeEntity.generation_id.not_in(retained_generations)
            ))
            db.commit()
        return result
    except Exception as exc:
        with SessionLocal() as db:
            state = db.get(KnowledgeGraphState, GRAPH_STATE_ID)
            if state and state.building_generation_id == generation_id:
                state.building_generation_id = None
                state.status = (
                    "STALE" if state.active_generation_id else "FAILED"
                )
                state.error_message = str(exc)[:4000]
            if not state or state.active_generation_id != generation_id:
                _cleanup_generation(db, generation_id)
            db.commit()
        raise


def domain_graph_status(
    db: Session,
    *,
    include_counts: bool = True,
) -> dict[str, Any]:
    state = db.get(KnowledgeGraphState, GRAPH_STATE_ID)
    if not state:
        return {
            "status": "NOT_BUILT",
            "active_generation_id": None,
            "building_generation_id": None,
            "entities": 0,
            "relations": 0,
            "metadata": {},
            "error": None,
        }
    generation_id = state.active_generation_id
    entity_count = 0
    relation_count = 0
    if generation_id and include_counts:
        entity_count = int(db.scalar(
            select(func.count(KnowledgeEntity.id)).where(
                KnowledgeEntity.generation_id == generation_id
            )
        ) or 0)
        relation_count = int(db.scalar(
            select(func.count(KnowledgeRelation.id)).where(
                KnowledgeRelation.generation_id == generation_id
            )
        ) or 0)
    return {
        "status": state.status,
        "active_generation_id": generation_id,
        "building_generation_id": state.building_generation_id,
        "entities": entity_count,
        "relations": relation_count,
        "metadata": json_loads(state.metadata_json, {}),
        "error": state.error_message,
    }


def search_domain_graph(
    db: Session,
    query: str,
    *,
    top_k: int = 12,
    max_hops: int = 2,
) -> dict[str, Any]:
    state = db.get(KnowledgeGraphState, GRAPH_STATE_ID)
    generation_id = state.active_generation_id if state else None
    if not generation_id:
        return {
            "query": query,
            "generation_id": None,
            "status": state.status if state else "NOT_BUILT",
            "nodes": [],
            "edges": [],
            "paths": [],
            "documents": [],
        }
    terms = sorted(
        {term for term in tokenize(query) if len(term) >= 2},
        key=len,
        reverse=True,
    )[:12]
    if not terms:
        return {
            "query": query,
            "generation_id": generation_id,
            "status": state.status,
            "nodes": [],
            "edges": [],
            "paths": [],
            "documents": [],
        }
    conditions = []
    for term in terms:
        pattern = f"%{term.casefold()}%"
        conditions.extend([
            KnowledgeEntity.normalized_name.ilike(pattern),
            KnowledgeEntity.canonical_name.ilike(pattern),
        ])
    start_entities = list(db.scalars(
        select(KnowledgeEntity)
        .where(
            KnowledgeEntity.generation_id == generation_id,
            or_(*conditions),
        )
        .limit(max(top_k * 5, 50))
    ).all())
    query_tokens = set(tokenize(query))
    entity_scores: dict[str, float] = {}
    entity_map: dict[str, KnowledgeEntity] = {
        entity.id: entity for entity in start_entities
    }
    entity_paths: dict[str, list[dict[str, Any]]] = {
        entity.id: [] for entity in start_entities
    }
    normalized_query = _normalize_name(query)
    for entity in start_entities:
        entity_tokens = set(tokenize(entity.canonical_name))
        overlap = len(query_tokens & entity_tokens) / max(len(query_tokens), 1)
        exact_bonus = (
            1.0
            if entity.normalized_name in normalized_query
            or normalized_query in entity.normalized_name
            else 0.0
        )
        entity_scores[entity.id] = 1.0 + overlap * 2.0 + exact_bonus

    frontier = set(entity_scores)
    selected_edges: dict[str, KnowledgeRelation] = {}
    for hop in range(1, max_hops + 1):
        if not frontier:
            break
        relations = list(db.scalars(
            select(KnowledgeRelation).where(
                KnowledgeRelation.generation_id == generation_id,
                or_(
                    KnowledgeRelation.source_entity_id.in_(frontier),
                    KnowledgeRelation.target_entity_id.in_(frontier),
                ),
            ).limit(5000)
        ).all())
        next_frontier: set[str] = set()
        touched_ids: set[str] = set()
        for relation in relations:
            touched_ids.add(relation.source_entity_id)
            touched_ids.add(relation.target_entity_id)
        missing_ids = touched_ids.difference(entity_map)
        if missing_ids:
            for entity in db.scalars(
                select(KnowledgeEntity).where(
                    KnowledgeEntity.id.in_(missing_ids),
                    KnowledgeEntity.generation_id == generation_id,
                )
            ).all():
                entity_map[entity.id] = entity
        for relation in relations:
            if relation.source_entity_id in frontier:
                source_id = relation.source_entity_id
                target_id = relation.target_entity_id
                direction = "out"
            else:
                source_id = relation.target_entity_id
                target_id = relation.source_entity_id
                direction = "in"
            base_score = entity_scores.get(source_id, 0.0)
            edge_weight = RELATION_WEIGHTS.get(
                relation.relation_type,
                0.75,
            )
            candidate_score = (
                base_score
                * (0.78 ** hop)
                * edge_weight
                * relation.confidence
            )
            if candidate_score <= entity_scores.get(target_id, 0.0):
                continue
            selected_edges[relation.id] = relation
            entity_scores[target_id] = candidate_score
            entity_paths[target_id] = [
                *entity_paths.get(source_id, []),
                {
                    "edge_id": relation.logical_id,
                    "from": (
                        entity_map[source_id].logical_id
                        if source_id in entity_map
                        else source_id
                    ),
                    "to": (
                        entity_map[target_id].logical_id
                        if target_id in entity_map
                        else target_id
                    ),
                    "relation_type": relation.relation_type,
                    "direction": direction,
                    "evidence_document_id": relation.evidence_document_id,
                    "evidence_chunk_id": relation.evidence_chunk_id,
                },
            ]
            next_frontier.add(target_id)
        frontier = next_frontier

    reached_ids = set(entity_scores)
    mention_rows = db.execute(
        select(
            KnowledgeEntityMention,
            KnowledgeDocument,
            KnowledgeChunk,
        )
        .join(
            KnowledgeDocument,
            KnowledgeEntityMention.document_id == KnowledgeDocument.id,
        )
        .outerjoin(
            KnowledgeChunk,
            KnowledgeEntityMention.chunk_id == KnowledgeChunk.id,
        )
        .where(
            KnowledgeEntityMention.generation_id == generation_id,
            KnowledgeEntityMention.entity_id.in_(reached_ids),
            KnowledgeDocument.active.is_(True),
            KnowledgeDocument.review_status == "ACTIVE",
        )
        .limit(5000)
    ).all()
    candidates: dict[str, dict[str, Any]] = {}
    for mention, document, chunk in mention_rows:
        evidence_id = chunk.id if chunk else document.id
        entity = entity_map.get(mention.entity_id)
        if not entity:
            continue
        score = entity_scores.get(entity.id, 0.0) * mention.confidence
        current = candidates.get(evidence_id)
        graph_path = entity_paths.get(entity.id, [])
        item = {
            "evidence_id": evidence_id,
            "source_type": document.source_type,
            "title": (
                f"{document.title} / {chunk.heading}"
                if chunk and chunk.heading
                else document.title
            ),
            "content": chunk.content if chunk else mention.excerpt,
            "source_score": round(score, 6),
            "metadata": {
                "document_id": document.id,
                "chunk_id": chunk.id if chunk else None,
                "graph_entity_id": entity.logical_id,
                "graph_entity_type": entity.entity_type,
                "graph_entity_name": entity.canonical_name,
                "graph_generation_id": generation_id,
                "graph_status": state.status,
            },
            "paths": graph_path,
        }
        if current is None or score > float(current["source_score"]):
            candidates[evidence_id] = item

    documents = sorted(
        candidates.values(),
        key=lambda item: item["source_score"],
        reverse=True,
    )[:top_k]
    ranked_entity_ids = sorted(
        entity_scores,
        key=entity_scores.get,
        reverse=True,
    )[: max(top_k * 4, 40)]
    nodes = [
        {
            "id": entity_map[entity_id].logical_id,
            "revision_id": entity_id,
            "entity_type": entity_map[entity_id].entity_type,
            "name": entity_map[entity_id].canonical_name,
            "score": round(entity_scores[entity_id], 6),
        }
        for entity_id in ranked_entity_ids
        if entity_id in entity_map
    ]
    edges = [
        {
            "id": relation.logical_id,
            "revision_id": relation.id,
            "source": entity_map[relation.source_entity_id].logical_id,
            "target": entity_map[relation.target_entity_id].logical_id,
            "relation_type": relation.relation_type,
            "confidence": relation.confidence,
            "evidence_document_id": relation.evidence_document_id,
            "evidence_chunk_id": relation.evidence_chunk_id,
        }
        for relation in selected_edges.values()
        if (
            relation.source_entity_id in entity_map
            and relation.target_entity_id in entity_map
        )
    ][:500]
    return {
        "query": query,
        "generation_id": generation_id,
        "status": state.status,
        "nodes": nodes,
        "edges": edges,
        "paths": [
            {
                "entity_id": entity_map[entity_id].logical_id,
                "steps": entity_paths.get(entity_id, []),
            }
            for entity_id in ranked_entity_ids
            if entity_paths.get(entity_id)
        ][:500],
        "documents": documents,
    }


def domain_graph_candidates(
    db: Session,
    query: str,
    *,
    top_k: int,
    max_hops: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    result = search_domain_graph(
        db,
        query,
        top_k=top_k,
        max_hops=max_hops,
    )
    return result["documents"], [
        {
            "path_type": "domain_knowledge_graph",
            **path,
        }
        for path in result["paths"]
    ]
