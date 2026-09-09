"""New workbench invariants: fixed versions, bundle reads and category scope."""
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.db import Base
from app.core.utils import json_dumps, json_loads
from app.models import Case, KnowledgeChunk, KnowledgeDocument, ModelProfile
from app.services import workbench
from app.services.workbench_snapshot import capture_knowledge, load_knowledge
from app.services.diagnostic_methods import load_applicable_diagnostic_methods
from app.services.diagnostic_tools import DiagnosticToolEnvironment, ReadDiagnosticDocumentsInput, _read_handler
from app.services.rag import retriever
from app.workbench_models import WorkbenchRecord


@pytest.fixture
def store(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'workbench.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        db.add(Case(id="CASE-test", title="synthetic network", device_type="AP", problem_category="network"))
        db.commit()
    yield factory
    engine.dispose()


def add_document(db, key, content, category="network", role="log_analysis", **meta):
    doc = KnowledgeDocument(id=key, title=key, content=content, source_type="analysis_skill",
        active=True, review_status="ACTIVE", confidentiality="INTERNAL", metadata_json=json_dumps({
            "problem_categories": [category], "knowledge_role": role, **meta}))
    db.add(doc)
    db.flush()
    db.add(KnowledgeChunk(id="CHK-"+key, document_id=key, document_version=doc.version,
        chunk_index=0, heading="Synthetic", content=content, token_estimate=10))
    db.flush()
    return doc


def test_snapshot_retains_old_content_category_template_and_chunk_ids(store):
    with store() as db:
        doc = add_document(db, "DOC-original", "SYNTHETIC_OLD_PATTERN")
        case = db.get(Case, "CASE-test")
        config = workbench.resolve_configuration(db, workbench.capture_configuration(db, case))
        same = workbench.capture_configuration(db, case)
        assert same["workbench_snapshot_id"] == config["workbench_snapshot_id"]
        doc.content, doc.version = "SYNTHETIC_NEW_PATTERN", 2
        case.problem_category = "connection"
        db.get(KnowledgeChunk, "CHK-DOC-original").content = "SYNTHETIC_NEW_PATTERN"
        db.commit()
        with workbench.use_configuration(config):
            hits = retriever.search("SYNTHETIC_OLD_PATTERN", db=db, case_id=case.id, apply_models=False)
            assert any(hit.evidence_id == "CHK-DOC-original" and "OLD" in hit.content for hit in hits)
            assert not retriever.search("SYNTHETIC_NEW_PATTERN", db=db, case_id=case.id, apply_models=False)
            assert workbench.case_category.get() == "network"
            assert "组网总览" in workbench.case_template.get()["content"]
        assert workbench.case_category.get() is None


def test_root_read_includes_reviewed_dependencies_and_marks_missing(store):
    with store() as db:
        manifest = [{"path": "bundle/SKILL.md", "references": ["references/detail.md", "missing.md"]},
                    {"path": "bundle/references/detail.md", "references": ["../SKILL.md"]}]
        add_document(db, "DOC-root", "# 总领\nRead references/detail.md", bundle_id="BUNDLE", source_paths=["bundle/SKILL.md"], bundle_manifest=manifest)
        dependency = add_document(db, "DOC-dependency", "DEPENDENCY_ONLY_PATTERN", category="connection", bundle_id="BUNDLE",
            source_paths=["bundle/references/detail.md"], bundle_manifest=manifest)
        dependency.source_type = "document"
        db.flush()
        case = db.get(Case, "CASE-test")
        methods = load_applicable_diagnostic_methods(db, case)
        root = next(item for item in methods if item.id == "DOC-root")
        assert root.dependency_ids == ["DOC-dependency"]
        assert root.unresolved_references == ["bundle/missing.md"]
        output = _read_handler(DiagnosticToolEnvironment(case=case, methods=methods, patterns=[]), None,
            ReadDiagnosticDocumentsInput(document_ids=["DOC-root"]))
        assert [doc["id"] for doc in output.documents] == ["DOC-root", "DOC-dependency"]
        assert output.documents[1]["selection_reason"]


def test_unconfirmed_library_excluded_and_confirmed_case_is_guidance(store):
    with store() as db:
        record = WorkbenchRecord(id="WB-library", kind="library", payload_json=json_dumps({"title":"synthetic case",
            "content":"REVIEWED_LIBRARY_PATTERN", "problem_category":"network", "status":"PENDING"}))
        db.add(record)
        db.flush()
        assert not capture_knowledge(db)
        value = json_loads(record.payload_json, {})
        value["status"] = "CONFIRMED"
        record.payload_json = json_dumps(value)
        db.flush()
        references = capture_knowledge(db)
        docs, rows = load_knowledge(db, references)
        assert len(docs) == 1 and docs[0].source_type == "fault_case"
        assert rows and json_loads(docs[0].metadata_json, {})["human_confirmed"]


def test_unknown_cross_category_and_explicit_false_defaults(store):
    from app.schemas import CaseCreate
    assert CaseCreate(title="new", device_type="AP").model_egress_approved is True
    assert CaseCreate(title="old", device_type="AP", model_egress_approved=False).model_egress_approved is False
    with store() as db:
        doc = add_document(db, "DOC-connection", "TERMINAL_ONLY", category="connection")
        assert workbench.matches_category(doc, "unknown")
        assert not workbench.matches_category(doc, "network")
        with pytest.raises(ValueError):
            workbench.validate_case_options(db, {"problem_category":"not-a-category"})


def test_tampered_or_missing_snapshot_fails_closed(store):
    with store() as db:
        add_document(db, "DOC-fixed", "PINNED")
        refs = capture_knowledge(db)
        db.get(WorkbenchRecord, refs[0]).payload_json = "{}"
        db.flush()
        with pytest.raises(ValueError, match="校验"):
            load_knowledge(db, refs)


def test_host_snapshot_keeps_published_methods_after_edits(store):
    from app.services.host_diagnostic_runtime import load_host_diagnostic_snapshot, host_diagnostic_context
    with store() as db:
        add_document(db, "DOC-host", "# Logs\n`ORIGINAL_PATTERN`")
        db.commit()
    before = load_host_diagnostic_snapshot("CASE-test", session_factory=store)
    with store() as db:
        doc = db.get(KnowledgeDocument, "DOC-host")
        doc.content, doc.version = "# Logs\n`CHANGED_PATTERN`", 2
        db.commit()
    after = load_host_diagnostic_snapshot("CASE-test", session_factory=store, configuration=before.configuration)
    assert after.method_manifest_hash == before.method_manifest_hash
    assert host_diagnostic_context(after)["report_template"]["content"]
    assert host_diagnostic_context(after)["backend_chat_allowed"] is False


def test_graph_candidates_keep_only_snapshot_versions_and_explain_fallback(store):
    from app.services.workbench_retrieval import scope_graph
    with store() as db:
        doc = add_document(db, "DOC-graph", "GRAPH_TERM", category="connection")
        case = db.get(Case, "CASE-test")
        references = capture_knowledge(db)
        candidates = [{"metadata":{"document_id":doc.id,"document_version":1},"paths":[],"evidence_id":"old"},
                      {"metadata":{"document_id":doc.id,"document_version":2},"paths":[],"evidence_id":"new"}]
        doc.version = 2
        db.flush()
        with workbench.use_configuration({"knowledge_snapshot":references,"problem_category":"network"}):
            result, _ = scope_graph(db, candidates, case)
            assert [item["evidence_id"] for item in result] == ["old"]
            assert result[0]["metadata"]["cross_category_reason"]


def test_default_template_selection_applies_only_to_new_runs(store):
    with store() as db:
        first = add_document(db, "DOC-template-a", "# TEMPLATE_A", role="report_template")
        second = add_document(db, "DOC-template-b", "# TEMPLATE_B", role="report_template")
        case = db.get(Case, "CASE-test")
        old = workbench.resolve_configuration(db, workbench.capture_configuration(db, case))
        assert old["report_template"]["id"] == second.id
        db.add(WorkbenchRecord(id="template-network", kind="template_default", payload_json=json_dumps({"document_id":first.id})))
        db.flush()
        new = workbench.resolve_configuration(db, workbench.capture_configuration(db, case))
        assert new["report_template"]["id"] == first.id
        assert old["report_template"]["id"] == second.id
        assert new["workbench_snapshot_id"] != old["workbench_snapshot_id"]


def test_selected_model_context_does_not_change_global_profile(store, monkeypatch):
    with store() as db:
        for key in ("MODEL-a", "MODEL-b"):
            db.add(ModelProfile(id=key, name=key, task_type="chat", mode="api", provider="openai_compatible", model_name=key,
                enabled=True, is_active=key=="MODEL-a"))
        db.commit()
        case = db.get(Case,"CASE-test")
        case.chat_profile_id = "MODEL-b"
        config = workbench.resolve_configuration(db,workbench.capture_configuration(db,case))
        db.commit()
    monkeypatch.setattr(workbench, "SessionLocal", store)
    with workbench.use_configuration(config):
        assert workbench.selected_profile().id == "MODEL-b"
    assert workbench.selected_profile() is None
    with store() as db:
        assert db.get(ModelProfile,"MODEL-a").is_active
        assert not db.get(ModelProfile,"MODEL-b").is_active


def test_migration_preserves_explicit_old_egress_value(tmp_path):
    from alembic import command
    from alembic.config import Config
    from pathlib import Path
    from sqlalchemy import text
    path = tmp_path / "migration.db"
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.attributes["database_url"] = f"sqlite:///{path}"
    command.upgrade(config,"0021")
    engine = create_engine(config.attributes["database_url"])
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO cases (id,title,device_type,description,status,severity,model_egress_approved,created_at,updated_at) VALUES ('old','old','AP','','DRAFT','UNKNOWN',0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
    command.upgrade(config,"head")
    with engine.connect() as connection:
        row = connection.execute(text("SELECT problem_category,chat_profile_id,model_egress_approved FROM cases WHERE id='old'")).one()
        assert tuple(row)==("unknown",None,0)
    engine.dispose()
