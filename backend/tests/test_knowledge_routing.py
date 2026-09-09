import asyncio
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.api import knowledge_routing as knowledge_routing_api
from app.api.routes import router
from app.core.db import Base, configure_sqlite_engine, get_db
from app.core.utils import json_dumps, json_loads, sha256_file
from app.mcp.contracts import MCPPrincipal, MCPToolCallContext, MCPToolError
from app.mcp.debugplatform_registry import create_debugplatform_mcp_registry
from app.models import (
    Artifact,
    KnowledgeDocument,
    KnowledgeDocumentCategory,
    KnowledgeRevision,
    ModelProfile,
    UserAccount,
)
from app.model_access_models import ModelProfileAccess
from app.services.model_access import chat_model_snapshot
from app.services import knowledge_routing
from app.services.knowledge_routing import route_markdown_knowledge_job
from app.services.knowledge_taxonomy import seed_knowledge_categories
from app.services.storage import StorageService


MARKDOWN = """# AP UDM restart method

## Symptom

The AP UDM process exits and its listen port disappears.

## Diagnostic flow

Inspect signal 11, Advertise failures and the GW heartbeat timeout.

token=must-not-reach-the-model
"""


class _Provider:
    model_name = "synthetic-routing-model"

    async def generate_json(
        self,
        system: str,
        user: str,
        schema_name: str = "diagnosis",
        purpose: str = "case_diagnosis",
    ) -> dict:
        assert "untrusted" in system
        assert schema_name == "knowledge_routing"
        assert purpose == "knowledge_routing"
        assert "must-not-reach-the-model" not in user
        assert "token=<MASKED>" in user
        return {
            "decisions": [{
                "document_key": "DOC-routing",
                "category_id": "KCAT-history-fault-tree",
                "device_type": "AP",
                "module": "UDM",
                "confidence": 0.94,
                "rationale": "The document defines an AP diagnostic decision flow.",
            }],
        }


class _Context:
    def update(self, progress: int, message: str = "") -> None:
        self.progress = progress
        self.message = message

    def raise_if_cancelled(self) -> None:
        return None

    def complete_in_transaction(self, db, result, message: str = "Completed") -> None:
        self.result = result
        self.message = message


def _factory(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'knowledge-routing.db'}",
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


def _mcp_context(role: str = "ADMIN") -> MCPToolCallContext:
    return MCPToolCallContext(
        principal=MCPPrincipal(subject="USER-admin", claims={"role": role}),
        request_id="knowledge-routing-test",
        protocol_version="2025-11-25",
    )


def _call(registry, name: str, arguments: dict, *, role: str = "ADMIN"):
    return asyncio.run(
        registry.call_tool(name, arguments, _mcp_context(role))
    )


def _seed_artifact(
    factory,
    isolated_storage: StorageService,
    *,
    artifact_id: str,
    document_id: str,
    reasoning_owner: str,
) -> None:
    path = isolated_storage.artifact_dir(artifact_id) / "knowledge.md"
    # Keep the fixture byte-stable across Windows and POSIX so the assertion
    # below verifies that routing never rewrites the Markdown body.
    path.write_bytes(MARKDOWN.encode("utf-8"))
    with factory() as db:
        snapshot = {}
        if reasoning_owner == "platform_llm":
            db.add(UserAccount(id="USER-admin", username="routing-admin", display_name="Routing admin", role="ADMIN"))
            profile = ModelProfile(id="MODEL-routing", name="Synthetic routing", task_type="chat",
                provider="openai_compatible", mode="api", model_name="synthetic-routing-model", enabled=True)
            db.add(profile)
            db.flush()
            db.add(ModelProfileAccess(profile_id=profile.id, owner_id="USER-admin", visibility="PRIVATE"))
            db.flush()
            snapshot = chat_model_snapshot(db, {"id": "USER-admin", "role": "ADMIN"}, profile)
        db.add(Artifact(
            id=artifact_id,
            case_id=None,
            kind="knowledge_routing_source",
            original_name="knowledge.md",
            stored_path=isolated_storage.storage_key(path),
            sha256=sha256_file(path),
            size_bytes=path.stat().st_size,
            status="UPLOADED",
            metadata_json=json_dumps({
                "document_id": document_id,
                "relative_path": "knowledge.md",
                "reasoning_owner": reasoning_owner,
                "model_profile_id": "MODEL-routing",
                "model_snapshot": snapshot,
                "model_egress_consent": True,
                "trust_level": "MEDIUM",
                "confidentiality": "INTERNAL",
                "created_by": "USER-admin",
            }),
        ))
        db.commit()


def test_platform_model_routes_markdown_to_a_governed_draft(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, factory = _factory(tmp_path)
    isolated_storage = StorageService(tmp_path / "storage")
    with factory() as db:
        seed_knowledge_categories(db)
    _seed_artifact(
        factory,
        isolated_storage,
        artifact_id="ART-routing",
        document_id="DOC-routing",
        reasoning_owner="platform_llm",
    )
    monkeypatch.setattr(knowledge_routing, "SessionLocal", factory)
    monkeypatch.setattr(knowledge_routing, "storage", isolated_storage)
    monkeypatch.setattr(
        knowledge_routing,
        "get_llm_provider",
        lambda _profile: _Provider(),
    )

    context = _Context()
    result = route_markdown_knowledge_job(
        context,
        "ART-routing",
        "DOC-routing",
    )
    assert result["routing_status"] == "APPLIED"
    assert result["category_id"] == "KCAT-history-fault-tree"
    assert result["category_code"] == "history.fault_trees"
    assert result["category_path"].endswith(" / 故障树")
    assert result["confidence"] == pytest.approx(0.94)
    assert context.result == result
    with factory() as db:
        document = db.get(KnowledgeDocument, "DOC-routing")
        assert document is not None
        assert document.content == MARKDOWN
        assert document.source_type == "fault_tree"
        assert document.device_type == "AP"
        assert document.module == "UDM"
        assert document.review_status == "DRAFT"
        assert document.active is False
        link = db.get(KnowledgeDocumentCategory, document.id)
        assert link.category_id == "KCAT-history-fault-tree"
        routing = json_loads(document.metadata_json, {})["knowledge_routing"]
        assert routing["reasoning_owner"] == "platform_llm"
        assert routing["human_review_required"] is True
        assert db.scalar(select(KnowledgeRevision).where(
            KnowledgeRevision.document_id == document.id
        )) is not None
    engine.dispose()


def test_host_cli_reads_masked_context_and_atomically_applies_routing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, factory = _factory(tmp_path)
    isolated_storage = StorageService(tmp_path / "storage")
    with factory() as db:
        seed_knowledge_categories(db)
    _seed_artifact(
        factory,
        isolated_storage,
        artifact_id="ART-host-routing",
        document_id="DOC-host-routing",
        reasoning_owner="host_cli",
    )
    monkeypatch.setattr(knowledge_routing, "SessionLocal", factory)
    monkeypatch.setattr(knowledge_routing, "storage", isolated_storage)
    route_markdown_knowledge_job(
        _Context(),
        "ART-host-routing",
        "DOC-host-routing",
    )
    with factory() as db:
        rejected = db.get(KnowledgeDocument, "DOC-host-routing")
        assert rejected is not None
        rejected.review_status = "REJECTED"
        db.commit()

    registry = create_debugplatform_mcp_registry(session_factory=factory)
    with pytest.raises(MCPToolError, match="role required"):
        _call(
            registry,
            "debug_get_knowledge_routing_context",
            {
                "document_ids": ["DOC-host-routing"],
                "consent_host_model_data": True,
            },
            role="VIEWER",
        )
    routing_context = _call(
        registry,
        "debug_get_knowledge_routing_context",
        {
            "document_ids": ["DOC-host-routing"],
            "consent_host_model_data": True,
        },
    )
    item = routing_context["documents"][0]
    assert routing_context["inference_owner"] == "host_cli"
    assert routing_context["backend_chat_calls"] == 0
    assert "must-not-reach-the-model" not in item["excerpt"]
    assert "token=<MASKED>" in item["excerpt"]

    applied = _call(
        registry,
        "debug_apply_knowledge_routing",
        {
            "decisions": [{
                "document_id": item["document_id"],
                "expected_lock_version": item["expected_lock_version"],
                "content_sha256": item["content_sha256"],
                "category_id": "KCAT-diagnosis-protocol",
                "device_type": "AP",
                "module": "UDM",
                "confidence": 0.91,
                "rationale": "The Markdown describes a reusable UDM protocol check.",
            }],
            "client_model_claim": "codex-test-model",
            "confirm_draft_update": True,
        },
    )
    assert applied["updated"] == 1
    assert applied["backend_chat_calls"] == 0
    assert applied["documents"][0]["category_code"] == "diagnosis.protocol_rules"
    assert applied["documents"][0]["source_type"] == "protocol_rule"
    assert applied["documents"][0]["review_status"] == "DRAFT"
    with factory() as db:
        routed = db.get(KnowledgeDocument, "DOC-host-routing")
        assert routed is not None
        assert routed.review_status == "DRAFT"
        assert routed.active is False
    with pytest.raises(MCPToolError, match="changed since"):
        _call(
            registry,
            "debug_apply_knowledge_routing",
            {
                "decisions": [{
                    "document_id": item["document_id"],
                    "expected_lock_version": item["expected_lock_version"],
                    "content_sha256": item["content_sha256"],
                    "category_id": "KCAT-diagnosis-protocol",
                    "confidence": 0.91,
                    "rationale": "Stale replay must be rejected.",
                }],
                "confirm_draft_update": True,
            },
        )
    engine.dispose()


def test_multi_markdown_api_stages_one_host_job_per_document(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, factory = _factory(tmp_path)
    isolated_storage = StorageService(tmp_path / "storage")
    with factory() as db:
        seed_knowledge_categories(db)
    monkeypatch.setattr(knowledge_routing_api, "storage", isolated_storage)
    monkeypatch.setattr(knowledge_routing, "storage", isolated_storage)
    monkeypatch.setattr(knowledge_routing, "SessionLocal", factory)

    with TestClient(_admin_app(factory)) as client:
        response = client.post(
            "/api/v1/knowledge-routing/import",
            data={
                "reasoning_owner": "host_cli",
                "relative_paths_json": '["rules/one.md", "methods/two.markdown"]',
            },
            files=[
                ("files", ("one.md", "# Rule one\n\nAP timeout", "text/markdown")),
                ("files", ("two.markdown", "# Method two\n\nGW check", "text/markdown")),
            ],
        )
        assert response.status_code == 202, response.text
        payload = response.json()
        assert payload["reasoning_owner"] == "host_cli"
        assert payload["file_count"] == 2
        assert len({item["document_id"] for item in payload["items"]}) == 2
        assert all(item["job"]["kind"] == "route_markdown_knowledge" for item in payload["items"])
    with factory() as db:
        artifacts = list(db.scalars(select(Artifact).where(
            Artifact.kind == "knowledge_routing_source"
        )).all())
        assert len(artifacts) == 2
        assert {
            json_loads(item.metadata_json, {})["relative_path"] for item in artifacts
        } == {"rules/one.md", "methods/two.markdown"}
    engine.dispose()
