"""Build a private domain-graph generation for a validated publication snapshot."""
from app.core.utils import json_dumps, stable_id
from app.models import KnowledgeEntity, KnowledgeEntityMention, KnowledgeRelation
from app.services.knowledge_graph import _chunk_for_text, _document_facts, _source_signature
from app.services.job_progress import report_progress


def stage_graph(session_factory, documents, chunks_by_document, generation_id, ctx) -> dict:
    report_progress(ctx, 60, "正在构建对应的知识关联", stage="构建知识关联", stage_index=3, stage_count=4,
        completed_units=0, total_units=len(documents), unit="份文档")
    facts = []
    definitions = {}
    for document in documents:
        ctx.raise_if_cancelled()
        value = _document_facts(document)
        facts.append((document, value))
        for key, entity in value["entities"].items():
            definitions.setdefault(key, entity)
    logical = {key: stable_id("KENT", *key) for key in definitions}
    ids = {key: stable_id("KENTREV", logical[key], generation_id) for key in definitions}
    with session_factory() as db:
        for key, entity in definitions.items():
            db.add(KnowledgeEntity(id=ids[key], logical_id=logical[key], generation_id=generation_id,
                                   entity_type=entity["entity_type"], canonical_name=entity["canonical_name"],
                                   normalized_name=entity["normalized_name"], aliases_json="[]",
                                   metadata_json=json_dumps({"extractor": "deterministic_knowledge_graph_v1"})))
        db.commit()
    mentions = relations = 0
    for index, (document, value) in enumerate(facts):
        ctx.raise_if_cancelled()
        with session_factory() as db:
            chunks = chunks_by_document.get(document.id, [])
            selected_chunks = {}
            for key, entity in value["entities"].items():
                chunk = _chunk_for_text(chunks, entity["canonical_name"])
                selected_chunks[key] = chunk
                db.add(KnowledgeEntityMention(
                    id=stable_id("KMENT", generation_id, logical[key], document.id, chunk.id if chunk else ""),
                    generation_id=generation_id, entity_id=ids[key], document_id=document.id,
                    chunk_id=chunk.id if chunk else None, excerpt=entity["excerpt"], confidence=entity["confidence"],
                    metadata_json=json_dumps({"source_type": document.source_type, "document_version": document.version})))
                mentions += 1
            for relation in value["relations"]:
                source, target = relation["source"], relation["target"]
                chunk = selected_chunks.get(target)
                relation_id = stable_id("KREL", logical[source], logical[target], relation["relation_type"])
                db.add(KnowledgeRelation(
                    id=stable_id("KRELREV", relation_id, generation_id, document.id), logical_id=relation_id,
                    generation_id=generation_id, source_entity_id=ids[source], target_entity_id=ids[target],
                    relation_type=relation["relation_type"], evidence_document_id=document.id,
                    evidence_chunk_id=chunk.id if chunk else None, confidence=relation["confidence"],
                    metadata_json=json_dumps({"excerpt": relation["excerpt"], "document_version": document.version})))
                relations += 1
            db.commit()
        report_progress(ctx, 60 + int(30 * (index + 1) / max(1, len(facts))), "正在保存知识关联",
            stage="构建知识关联", stage_index=3, stage_count=4,
            completed_units=index + 1, total_units=len(facts), unit="份文档")
    return {"generation_id": generation_id, "entities": len(ids), "mentions": mentions, "relations": relations,
            "documents": len(documents), "source_signature": _source_signature(documents),
            "extractor": "deterministic_knowledge_graph_v1"}
