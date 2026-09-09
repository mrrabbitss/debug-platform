"""Category and version filters shared by lexical and graph retrieval."""
from sqlalchemy import select

from app.models import Case, KnowledgeDocument
from app.services.workbench import case_category, case_knowledge, matches_category
from app.services.workbench_snapshot import library_material, load_knowledge


def scope_rows(db, rows, dense_scores, search_terms, case_id):
    frozen = case_knowledge.get()
    if frozen is not None:
        _, rows = load_knowledge(db, frozen)
    else:
        rows.extend((chunk, doc) for doc, chunks in library_material(db) for chunk in chunks)
    rows = [(chunk, doc) for chunk, doc in rows if doc.active and doc.review_status == "ACTIVE"
            and doc.confidentiality in {"PUBLIC", "INTERNAL"}]
    if search_terms:
        rows = [(chunk, doc) for chunk, doc in rows if chunk.id in dense_scores or any(
            term.casefold() in (doc.title + " " + (chunk.heading or "") + " " + chunk.content).casefold()
            for term in search_terms)]
    case = db.get(Case, case_id) if case_id else None
    category = (case_category.get() or case.problem_category) if case else None
    selected = [(chunk, doc) for chunk, doc in rows if matches_category(doc, category)]
    return selected or rows, category


def reference_metadata(document, category):
    return {"cross_category_reason": ("当前类别及通用知识未命中，补充其他类别资料并需核对适用性"
        if not matches_category(document, category) else None)}


def scope_graph(db, candidates, case):
    if case_knowledge.get() is not None:
        documents, _ = load_knowledge(db, case_knowledge.get())
    else:
        documents = list(db.scalars(select(KnowledgeDocument).where(
            KnowledgeDocument.active.is_(True), KnowledgeDocument.review_status == "ACTIVE",
            KnowledgeDocument.confidentiality.in_(["PUBLIC", "INTERNAL"]))))
    by_id = {doc.id: doc for doc in documents}
    candidates = [item for item in candidates if item.get("metadata", {}).get("document_id") in by_id
        and item["metadata"].get("document_version") == by_id[item["metadata"]["document_id"]].version]
    category = case_category.get() or case.problem_category
    selected = [item for item in candidates if matches_category(by_id[item["metadata"]["document_id"]], category)]
    candidates = selected or candidates
    for item in candidates:
        item["metadata"].update(reference_metadata(by_id[item["metadata"]["document_id"]], category))
    paths = {item["metadata"].get("graph_entity_id", item["metadata"]["document_id"]): {
        "path_type": "domain_knowledge_graph", "entity_id": item["metadata"].get("graph_entity_id"),
        "steps": item["paths"]} for item in candidates if item.get("paths")}
    return candidates, list(paths.values())
