from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.core.db import Base
from app.models import AgentMemory, AnalysisRun, Case, KnowledgeDocument
from app.services.agentic_search import agentic_search, build_search_plan
from app.services.knowledge import index_document
from app.services.knowledge_taxonomy import (
    seed_knowledge_categories,
    set_document_category,
)
from app.services.memory import (
    extract_memories_from_analysis,
    mark_memories_reused,
    search_memories,
    upsert_memory,
)
from app.services.model_profiles import seed_model_profiles


def test_memory_lifecycle_deduplicates_searches_and_tracks_reuse(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'memory.db'}")
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    with session_factory() as db:
        case = Case(
            id="CASE-memory",
            title="WLAN authentication timeout",
            device_type="AP",
            device_model="AP-test",
            description="Four-way handshake does not start",
        )
        run = AnalysisRun(
            id="RUN-memory",
            case_id=case.id,
            status="COMPLETED",
        )
        db.add(case)
        db.add(run)
        db.commit()

        result = {
            "summary": "Authentication timer was not reset.",
            "hypotheses": [{
                "title": "Authentication retry timer regression",
                "confidence_score": 0.86,
                "supporting_evidence": ["EVT-1"],
            }],
            "recommended_actions": [{
                "priority": "P1",
                "action": "Compare EAP for 192.168.1.10 and AA:BB:CC:DD:EE:FF",
                "reason": "Confirm where authentication stops",
                "expected_result": "The first missing handshake transition is located",
            }],
            "missing_information": ["A successful comparison log is missing"],
            "limitations": ["A previous success does not prove the current root cause"],
            "suspected_modules": ["WLAN", "AUTH"],
            "analysis_engine": "test",
        }
        extracted = extract_memories_from_analysis(db, case, run, result)
        db.commit()
        assert {item.memory_type for item in extracted} == {
            "EPISODIC", "PROCEDURAL", "FAILURE",
        }
        global_procedure = next(
            item for item in extracted if item.memory_type == "PROCEDURAL"
        )
        assert "192.168.1.10" not in global_procedure.content
        assert "AA:BB:CC:DD:EE:FF" not in global_procedure.content
        assert "<IP>" in global_procedure.content
        assert "<MAC>" in global_procedure.content
        db.add(Case(id="CASE-other", title="Other case", description=""))
        db.commit()
        other_case_matches = search_memories(
            db,
            "Authentication retry timer regression",
            case_id="CASE-other",
            limit=20,
        )
        assert all(
            memory.case_id in {None, "CASE-other"}
            for memory, _ in other_case_matches
        )

        first = upsert_memory(
            db,
            memory_type="PROCEDURAL",
            case_id=None,
            source_kind="manual",
            source_id="METHOD-1",
            title="Authentication timeout procedure",
            content="Inspect EAP and handshake timestamps before changing configuration.",
            outcome="SUCCESS",
            confidence=0.9,
        )
        second = upsert_memory(
            db,
            memory_type="PROCEDURAL",
            case_id=None,
            source_kind="manual",
            source_id="METHOD-2",
            title="Authentication timeout procedure",
            content="Inspect EAP and handshake timestamps before changing configuration.",
            outcome="SUCCESS",
            confidence=0.9,
        )
        db.commit()
        assert first.id == second.id
        assert second.occurrence_count == 2

        matches = search_memories(
            db,
            "authentication handshake timestamps",
            case_id=case.id,
            limit=20,
        )
        assert matches
        assert any(memory.id == first.id for memory, _ in matches)
        assert search_memories(
            db,
            "quantum zebra unrelated",
            case_id=case.id,
            limit=20,
        ) == []

        mark_memories_reused(db, {first.id})
        db.commit()
        db.refresh(first)
        assert first.reuse_count == 1
        assert first.last_used_at is not None
    engine.dispose()


def test_agentic_search_hybrid_fusion_and_round_memory(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'agentic.db'}")
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    with session_factory() as db:
        seed_knowledge_categories(db)
        seed_model_profiles(db)
        case = Case(
            id="CASE-agentic",
            title="AP authentication timeout",
            device_type="AP",
            description="EAP succeeds but the four-way handshake is absent",
        )
        db.add(case)
        document = KnowledgeDocument(
            id="DOC-agentic",
            title="WLAN authentication timeout rule",
            source_type="diagnostic_rule",
            device_type="AP",
            module="WLAN",
            content=(
                "When EAP succeeds but the four-way handshake is absent, "
                "inspect the authentication retry timer and hostapd state."
            ),
        )
        db.add(document)
        db.flush()
        set_document_category(db, document.id, "KCAT-diagnosis-product")
        index_document(db, document)
        upsert_memory(
            db,
            memory_type="PROCEDURAL",
            case_id=None,
            source_kind="analysis_method",
            source_id="DOC-method",
            title="WLAN authentication retry procedure",
            content="Compare EAP, retry timer and four-way handshake timestamps.",
            outcome="SUCCESS",
            confidence=0.88,
        )
        db.commit()

        first = agentic_search(
            db,
            case_id=case.id,
            query="WLAN authentication retry handshake timeout",
            requested_modules=["knowledge", "memory"],
            top_k=10,
            max_hops=2,
        )
        assert first["plan"]["selected_modules"] == ["knowledge", "memory"]
        assert {item["stage"] for item in first["trace"]} >= {
            "knowledge",
            "memory",
            "graph_multi_hop",
            "reciprocal_rank_fusion",
            "dense_embedding",
            "reranker",
        }
        source_types = {item["source_type"] for item in first["results"]}
        assert "diagnostic_rule" in source_types
        assert "memory_procedural" in source_types
        assert first["trace"][-1]["status"] == "SKIPPED"

        second = agentic_search(
            db,
            case_id=case.id,
            query="WLAN authentication retry handshake timeout",
            requested_modules=["knowledge", "memory"],
            top_k=20,
            max_hops=2,
        )
        assert any(
            item["source_type"] == "memory_episodic"
            for item in second["results"]
        )
        assert db.scalar(select(func.count(AgentMemory.id)).where(
            AgentMemory.source_kind == "agentic_search"
        )) >= 1
        assert db.scalar(select(func.max(AgentMemory.reuse_count))) >= 1
        repeated_search_memory = db.scalar(select(AgentMemory).where(
            AgentMemory.source_kind == "agentic_search",
            AgentMemory.memory_type == "EPISODIC",
        ))
        assert repeated_search_memory is not None
        assert repeated_search_memory.occurrence_count == 2

        empty = agentic_search(
            db,
            case_id=case.id,
            query="quantum zebra unrelated",
            requested_modules=["code", "commit"],
            top_k=5,
        )
        assert empty["results"] == []
        assert db.scalar(select(func.count(AgentMemory.id)).where(
            AgentMemory.source_kind == "agentic_search",
            AgentMemory.memory_type == "FAILURE",
        )) == 1

    plan = build_search_plan(
        "哪个 commit 引入了这个函数调用回归",
        repository_count=1,
        requested_modules=None,
        max_hops=3,
    )
    assert {"knowledge", "memory", "code", "commit"}.issubset(
        plan["selected_modules"]
    )
    fallback_plan = build_search_plan(
        "WLAN timeout",
        repository_count=0,
        requested_modules=[],
        max_hops=2,
    )
    assert fallback_plan["selected_modules"] == ["knowledge", "memory"]
    assert "回退" in fallback_plan["rationale"][0]
    engine.dispose()


def test_diagnostic_search_uses_joint_gw_ap_knowledge_scope(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'joint-scope.db'}")
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    with session_factory() as db:
        seed_model_profiles(db)
        case = Case(
            id="CASE-joint-scope", title="AP secondary offline",
            device_type="AP", description="Primary gateway heartbeat dependency",
        )
        db.add(case)
        for document_id, device_type in (("DOC-joint-gw", "GW"), ("DOC-joint-ap", "AP")):
            document = KnowledgeDocument(
                id=document_id,
                title=f"{device_type} primary gateway heartbeat dependency",
                source_type="diagnostic_rule",
                device_type=device_type,
                content="Primary gateway heartbeat dependency can make a secondary AP offline.",
                active=True,
                review_status="ACTIVE",
            )
            db.add(document)
            db.flush()
            index_document(db, document)
        db.commit()

        ordinary = agentic_search(
            db, case_id=case.id, query="primary gateway heartbeat dependency",
            requested_modules=["knowledge"], top_k=20, record_memory=False,
        )
        joint = agentic_search(
            db, case_id=case.id, query="primary gateway heartbeat dependency",
            requested_modules=["knowledge"], top_k=20, record_memory=False,
            joint_diagnostic_scope=True, execution_mode="diagnostic_test",
        )

        ordinary_ids = {item["metadata"].get("document_id") for item in ordinary["results"]}
        joint_ids = {item["metadata"].get("document_id") for item in joint["results"]}
        assert "DOC-joint-ap" in ordinary_ids
        assert "DOC-joint-gw" not in ordinary_ids
        assert {"DOC-joint-ap", "DOC-joint-gw"}.issubset(joint_ids)
        assert joint["summary"]["knowledge_scope"] == "GW_AP_JOINT"
    engine.dispose()
