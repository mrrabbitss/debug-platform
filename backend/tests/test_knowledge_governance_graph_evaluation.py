from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.api.routes import router
from app.core.db import Base, configure_sqlite_engine, get_db
from app.models import (
    AgentMemory,
    AnalysisRun,
    Case,
    DiagnosisFeedback,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeEntity,
    KnowledgeGraphState,
    KnowledgeRelation,
    RetrievalEvaluationCase,
    RetrievalEvaluationDataset,
    RetrievalEvaluationRun,
)
from app.services import knowledge_graph, retrieval_evaluation
from app.services.knowledge_graph import (
    rebuild_domain_graph_job,
    search_domain_graph,
)
from app.services.knowledge_governance import (
    advance_document_version,
    mark_domain_graph_stale,
)
from app.services.knowledge_taxonomy import seed_knowledge_categories
from app.services.model_profiles import seed_model_profiles
from app.services.retrieval_evaluation import (
    calculate_ranking_metrics,
    run_retrieval_evaluation_job,
)


class _Context:
    def update(self, progress: int, message: str = "") -> None:
        self.progress = progress
        self.message = message

    def raise_if_cancelled(self) -> None:
        return None

    def complete_in_transaction(
        self,
        db,
        result,
        message: str = "Completed",
    ) -> None:
        self.result = result
        self.message = message


def _factory(tmp_path: Path, name: str):
    engine = create_engine(
        f"sqlite:///{tmp_path / name}",
        connect_args={"check_same_thread": False},
    )
    configure_sqlite_engine(engine)
    Base.metadata.create_all(bind=engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def _admin_app(factory) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def set_principal(request: Request, call_next):
        request.state.principal = {
            "id": "USER-admin",
            "username": "admin",
            "role": "ADMIN",
            "type": "user_token",
        }
        return await call_next(request)

    def override_db():
        with factory() as db:
            yield db

    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = override_db
    return app


def test_knowledge_review_revision_rollback_and_feedback_gate(
    tmp_path: Path,
) -> None:
    engine, factory = _factory(tmp_path, "governance.db")
    with factory() as db:
        seed_knowledge_categories(db)
        seed_model_profiles(db)
        db.add(Case(
            id="CASE-feedback",
            title="AP authentication timeout",
            device_type="AP",
            device_model="AP-test",
            firmware_version="V1",
        ))
        db.add(AnalysisRun(
            id="ANL-feedback",
            case_id="CASE-feedback",
            status="COMPLETED",
            result_json='{"root_causes":[]}',
        ))
        db.commit()

    with TestClient(_admin_app(factory)) as client:
        created = client.post("/api/v1/knowledge", json={
            "title": "Authentication timeout",
            "source_type": "fault_case",
            "content": (
                "# Symptom\nAUTH_TIMEOUT\n\n"
                "# Root cause\nKey mismatch\n\n"
                "# Solution\nReplace the shared key"
            ),
        })
        assert created.status_code == 200, created.text
        document = created.json()
        assert document["review_status"] == "DRAFT"
        assert document["active"] is False
        assert document["version"] == 1
        assert document["lock_version"] == 1

        revisions = client.get(
            f"/api/v1/knowledge/{document['id']}/revisions"
        ).json()
        assert [item["version"] for item in revisions] == [1]

        submitted = client.post(
            f"/api/v1/knowledge/{document['id']}/review/submit",
            json={"expected_lock_version": 1, "comment": "Ready"},
        )
        assert submitted.status_code == 200
        assert submitted.json()["review_status"] == "IN_REVIEW"

        approved = client.post(
            f"/api/v1/knowledge/{document['id']}/review/approve",
            json={"expected_lock_version": 2, "comment": "Verified"},
        )
        assert approved.status_code == 200
        assert approved.json()["review_status"] == "ACTIVE"
        assert approved.json()["active"] is True

        updated = client.patch(
            f"/api/v1/knowledge/{document['id']}",
            json={
                "expected_lock_version": 3,
                "title": "Updated authentication timeout",
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["review_status"] == "DRAFT"
        assert updated.json()["active"] is False
        assert updated.json()["version"] == 2
        assert updated.json()["lock_version"] == 4

        missing_precondition = client.patch(
            f"/api/v1/knowledge/{document['id']}",
            json={"title": "Unversioned edit"},
        )
        assert missing_precondition.status_code == 422

        stale_update = client.patch(
            f"/api/v1/knowledge/{document['id']}",
            json={
                "expected_lock_version": 3,
                "title": "Stale edit",
            },
        )
        assert stale_update.status_code == 409

        rollback = client.post(
            f"/api/v1/knowledge/{document['id']}/revisions/1/rollback",
            json={"expected_lock_version": 4},
        )
        assert rollback.status_code == 200, rollback.text
        assert rollback.json()["version"] == 3
        restored = client.get(
            f"/api/v1/knowledge/{document['id']}"
        ).json()
        assert restored["title"] == "Authentication timeout"
        assert restored["review_status"] == "DRAFT"

        feedback = client.post(
            "/api/v1/cases/CASE-feedback/diagnosis-feedback",
            json={
                "analysis_run_id": "ANL-feedback",
                "verdict": "PARTIAL",
                "root_cause_correct": False,
                "evidence_correct": True,
                "comment": "Evidence is useful but the root cause is wrong.",
                "corrections": {
                    "root_cause": "Shared key mismatch",
                    "solution": "Replace the shared key",
                    "evidence": "AUTH_TIMEOUT follows the key exchange.",
                },
            },
        )
        assert feedback.status_code == 200, feedback.text
        feedback_id = feedback.json()["id"]
        assert feedback.json()["status"] == "SUBMITTED"

        premature = client.post(
            f"/api/v1/cases/CASE-feedback/diagnosis-feedback/"
            f"{feedback_id}/incorporate"
        )
        assert premature.status_code == 409
        reviewed = client.post(
            f"/api/v1/cases/CASE-feedback/diagnosis-feedback/"
            f"{feedback_id}/review",
            json={"action": "APPROVE", "comment": "Confirmed by engineer"},
        )
        assert reviewed.status_code == 200
        incorporated = client.post(
            f"/api/v1/cases/CASE-feedback/diagnosis-feedback/"
            f"{feedback_id}/incorporate"
        )
        assert incorporated.status_code == 200, incorporated.text
        assert incorporated.json()["document"]["review_status"] == "DRAFT"

    with factory() as db:
        feedback_row = db.get(DiagnosisFeedback, feedback_id)
        incorporated_document = db.get(
            KnowledgeDocument,
            feedback_row.incorporated_document_id,
        )
        assert feedback_row.status == "INCORPORATED"
        assert incorporated_document.active is False
        assert incorporated_document.review_status == "DRAFT"
    engine.dispose()


def test_knowledge_optimistic_lock_is_enforced_by_the_database(
    tmp_path: Path,
) -> None:
    engine, factory = _factory(tmp_path, "knowledge-lock.db")
    with factory() as db:
        db.add(KnowledgeDocument(
            id="DOC-lock",
            title="Concurrent edit",
            content="original",
            active=False,
            review_status="DRAFT",
        ))
        db.commit()

    with factory() as first, factory() as second:
        first_document = first.get(KnowledgeDocument, "DOC-lock")
        second_document = second.get(KnowledgeDocument, "DOC-lock")
        first_document.content = "first writer"
        advance_document_version(
            first,
            first_document,
            created_by="writer-one",
            change_summary="First writer",
        )
        first.commit()

        second_document.content = "second writer"
        with pytest.raises(
            ValueError,
            match="changed since it was loaded",
        ):
            advance_document_version(
                second,
                second_document,
                created_by="writer-two",
                change_summary="Second writer",
            )

    with factory() as db:
        document = db.get(KnowledgeDocument, "DOC-lock")
        assert document.content == "first writer"
        assert document.version == 2
        assert document.lock_version == 2
    engine.dispose()


def test_domain_graph_build_search_and_failed_rebuild_are_atomic(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine, factory = _factory(tmp_path, "domain-graph.db")
    with factory() as db:
        document = KnowledgeDocument(
            id="DOC-graph",
            title="AP authentication failure",
            source_type="fault_case",
            device_type="AP",
            module="WLAN",
            content=(
                "# Symptom\nAUTH_TIMEOUT occurs\n\n"
                "# Log analysis\nWLAN handshake timeout\n\n"
                "# Root cause\nShared key mismatch\n\n"
                "# Solution\nReplace the shared key"
            ),
            active=True,
            review_status="ACTIVE",
        )
        db.add(document)
        db.flush()
        db.add_all([
            KnowledgeChunk(
                id="CHK-graph-symptom",
                document_id=document.id,
                chunk_index=0,
                heading="Symptom",
                content="AUTH_TIMEOUT occurs",
            ),
            KnowledgeChunk(
                id="CHK-graph-solution",
                document_id=document.id,
                chunk_index=1,
                heading="Solution",
                content="Replace the shared key",
            ),
        ])
        db.commit()

    monkeypatch.setattr(knowledge_graph, "SessionLocal", factory)
    result = rebuild_domain_graph_job(_Context())
    assert result["documents"] == 1
    assert result["entities"] >= 4
    assert result["relations"] >= 3

    with factory() as db:
        state = db.get(KnowledgeGraphState, "domain")
        first_generation = state.active_generation_id
        search = search_domain_graph(
            db,
            "AUTH_TIMEOUT shared key",
            top_k=10,
            max_hops=2,
        )
        assert search["generation_id"] == first_generation
        assert search["documents"]
        assert any(
            item["metadata"]["document_id"] == "DOC-graph"
            for item in search["documents"]
        )
        assert any(
            edge["relation_type"] == "RESOLVED_BY"
            for edge in search["edges"]
        )
        assert any(
            edge["relation_type"] == "CAUSED_BY"
            for edge in search["edges"]
        )
        entity_ids = set(db.scalars(select(KnowledgeEntity.id)))
        relation_ids = set(db.scalars(select(KnowledgeRelation.id)))

    original_extraction = knowledge_graph._document_facts
    source_changed = False

    def change_source_during_build(document):
        nonlocal source_changed
        facts = original_extraction(document)
        if not source_changed:
            source_changed = True
            with factory() as db:
                changed = db.get(KnowledgeDocument, document.id)
                changed.content += "\n\n# Validation\nConfirmed on AP-test"
                changed.version += 1
                changed.lock_version += 1
                mark_domain_graph_stale(
                    db,
                    "knowledge changed during graph build test",
                )
                db.commit()
        return facts

    monkeypatch.setattr(
        knowledge_graph,
        "_document_facts",
        change_source_during_build,
    )
    with pytest.raises(
        RuntimeError,
        match="Reviewed knowledge changed during graph build",
    ):
        rebuild_domain_graph_job(_Context())

    with factory() as db:
        state = db.get(KnowledgeGraphState, "domain")
        assert state.active_generation_id == first_generation
        assert state.status == "STALE"
        assert set(db.scalars(select(KnowledgeEntity.id))) == entity_ids
        assert set(db.scalars(select(KnowledgeRelation.id))) == relation_ids

    def fail_extraction(document):
        raise RuntimeError("synthetic graph build failure")

    monkeypatch.setattr(knowledge_graph, "_document_facts", fail_extraction)
    with pytest.raises(RuntimeError, match="synthetic graph build failure"):
        rebuild_domain_graph_job(_Context())

    with factory() as db:
        state = db.get(KnowledgeGraphState, "domain")
        assert state.active_generation_id == first_generation
        assert state.status == "STALE"
        assert set(db.scalars(select(KnowledgeEntity.id))) == entity_ids
        assert set(db.scalars(select(KnowledgeRelation.id))) == relation_ids
    engine.dispose()


def test_retrieval_evaluation_metrics_and_job_do_not_write_memory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    metrics = calculate_ranking_metrics(
        ["A", "B"],
        ["X", "B", "A"],
        top_k=3,
    )
    assert metrics["recall_at_k"] == 1.0
    assert metrics["precision_at_k"] == round(2 / 3, 6)
    assert metrics["mrr"] == 0.5

    engine, factory = _factory(tmp_path, "evaluation.db")
    with factory() as db:
        seed_model_profiles(db)
        db.add(Case(
            id="CASE-eval",
            title="Evaluation case",
            device_type="AP",
        ))
        db.add(KnowledgeDocument(
            id="DOC-eval",
            title="AUTH_TIMEOUT runbook",
            source_type="fault_case",
            device_type="AP",
            content="AUTH_TIMEOUT is caused by a shared key mismatch.",
            active=True,
            review_status="ACTIVE",
        ))
        db.flush()
        db.add(KnowledgeChunk(
            id="CHK-eval",
            document_id="DOC-eval",
            chunk_index=0,
            content="AUTH_TIMEOUT is caused by a shared key mismatch.",
            metadata_json="{}",
        ))
        db.add(RetrievalEvaluationDataset(
            id="ESET-eval",
            name="Authentication retrieval",
        ))
        db.flush()
        db.add(RetrievalEvaluationCase(
            id="ECASE-eval",
            dataset_id="ESET-eval",
            case_id="CASE-eval",
            query="AUTH_TIMEOUT shared key mismatch",
            expected_evidence_json='["CHK-eval"]',
            expected_root_causes_json='["shared key mismatch"]',
            modules_json='["knowledge"]',
            top_k=5,
            max_hops=1,
        ))
        db.add(RetrievalEvaluationRun(
            id="ERUN-eval",
            dataset_id="ESET-eval",
            status="QUEUED",
        ))
        db.commit()

    monkeypatch.setattr(retrieval_evaluation, "SessionLocal", factory)
    result = run_retrieval_evaluation_job(_Context(), "ERUN-eval")
    assert result["metrics"]["recall_at_k"] == 1.0
    assert result["metrics"]["root_cause_top_k"] == 1.0

    with factory() as db:
        run = db.get(RetrievalEvaluationRun, "ERUN-eval")
        assert run.status == "COMPLETED"
        assert db.scalar(select(func.count(AgentMemory.id))) == 0
    engine.dispose()
