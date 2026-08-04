import asyncio
from io import BytesIO
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from starlette.datastructures import UploadFile

from app.api import knowledge_curation as curation_api
from app.api.routes import router
from app.core.db import Base, configure_sqlite_engine, get_db
from app.core.utils import json_dumps, sha256_file
from app.models import (
    KnowledgeCurationMessage,
    KnowledgeCurationRevision,
    KnowledgeCurationSession,
    KnowledgeCurationSourceFile,
    KnowledgeDocument,
    Job,
    ModelProfile,
)
from app.services import knowledge_curation
from app.services.knowledge_curation import (
    CurationConflict,
    CurationError,
    build_evidence_bundle,
    confirm_curation_session,
    curate_knowledge_folder_job,
    normalize_relative_path,
    persist_curation_uploads,
    preview_curation_source,
    refine_curation_session,
    save_manual_curation_draft,
)
from app.services.knowledge_taxonomy import (
    get_default_category_id,
    seed_knowledge_categories,
)
from app.services.storage import StorageService


SOURCE_TEXT = """AP authentication failed
AUTH_TIMEOUT after key exchange
The shared key did not match
Engineer confirmed the mismatch
Replace the shared key
Restart the WLAN service
Authentication succeeds after the change
No recurrence in the validation window
"""


INITIAL_MARKDOWN = """# AP 认证超时案例

## 错误形式

AP 认证失败并出现 AUTH_TIMEOUT。[SRC-0001:L1-L2]

## 日志分析

超时发生在密钥交换之后。[SRC-0001:L2-L3]

## 错误定位

工程师确认共享密钥不一致。[SRC-0001:L3-L4]

## 解决方案

替换共享密钥并重启 WLAN 服务。[SRC-0001:L5-L6]

## 验证结果

修改后认证成功，观察窗口内未复发。[SRC-0001:L7-L8]

## 适用范围与限制

适用于出现相同认证阶段和证据模式的 AP；其他场景待确认。

## 来源证据

- 原始分析记录与日志：[SRC-0001:L1-L8]
"""


REFINED_MARKDOWN = INITIAL_MARKDOWN.replace(
    "替换共享密钥并重启 WLAN 服务。",
    "先备份配置，再替换共享密钥并重启 WLAN 服务。",
)


class _Provider:
    provider_id = "openai_compatible"
    model_name = "test-curation-model"
    is_mock = False

    async def generate_json(
        self,
        system: str,
        user: str,
        schema_name: str = "diagnosis",
        purpose: str = "case_diagnosis",
    ):
        assert "SRC-0001" in user
        if schema_name == "knowledge_case_curation":
            return {
                "title": "AP 认证超时案例",
                "markdown": INITIAL_MARKDOWN,
                "change_summary": "根据日志和人工分析生成初稿",
                "open_questions": [],
                "citations": ["SRC-0001"],
                "device_type": "AP",
                "module": "WLAN",
            }
        assert schema_name == "knowledge_case_refinement"
        return {
            "assistant_message": "已增加修改前备份配置的操作，并保留原始证据引用。",
            "revised_markdown": REFINED_MARKDOWN,
            "change_summary": "补充配置备份步骤",
            "open_questions": [],
            "citations": ["SRC-0001"],
        }

    async def generate_text(self, system: str, user: str, purpose: str = "case_assistance"):
        return "unused"


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
        f"sqlite:///{tmp_path / 'curation.db'}",
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


def _seed_session(factory, isolated_storage: StorageService) -> str:
    session_id = "KCUR-test"
    source_path = isolated_storage.curation_dir(session_id) / "sources" / "case" / "analysis.txt"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(SOURCE_TEXT, encoding="utf-8")
    with factory() as db:
        seed_knowledge_categories(db)
        db.add(ModelProfile(
            id="MODEL-curation",
            name="Curation API",
            task_type="chat",
            mode="api",
            provider="openai_compatible",
            model_name="test-curation-model",
            base_url="https://model.example/v1",
            enabled=True,
            is_active=True,
        ))
        db.add(KnowledgeCurationSession(
            id=session_id,
            status="QUEUED",
            title_hint="AP authentication timeout",
            category_id=get_default_category_id(db, "fault_case"),
            confidentiality="RESTRICTED",
            model_profile_id="MODEL-curation",
            model_snapshot_json="{}",
            source_manifest_json=json_dumps({
                "file_count": 1,
                "model_egress_consent": True,
            }),
            created_by="USER-admin",
        ))
        db.add(KnowledgeCurationSourceFile(
            id="KSRC-test",
            session_id=session_id,
            source_ref="SRC-0001",
            relative_path="case/analysis.txt",
            stored_path=isolated_storage.storage_key(source_path),
            sha256=sha256_file(source_path),
            size_bytes=source_path.stat().st_size,
            media_type="text/plain",
            source_role="analysis",
        ))
        db.commit()
    return session_id


def test_folder_curation_refinement_and_confirmation_are_gated(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine, factory = _factory(tmp_path)
    isolated_storage = StorageService(tmp_path / "storage")
    session_id = _seed_session(factory, isolated_storage)
    monkeypatch.setattr(knowledge_curation, "SessionLocal", factory)
    monkeypatch.setattr(knowledge_curation, "storage", isolated_storage)
    monkeypatch.setattr(knowledge_curation, "get_llm_provider", lambda profile=None: _Provider())

    result = curate_knowledge_folder_job(_Context(), session_id)
    assert result["draft_version"] == 1
    assert result["confirmable"] is True

    with factory() as db:
        session = db.get(KnowledgeCurationSession, session_id)
        assert session.status == "REVIEWING"
        assert session.device_type == "AP"
        assert session.module == "WLAN"
        assert json_dumps(session.validation_json)
        assert len(list(db.scalars(select(KnowledgeCurationRevision)).all())) == 1
        assert len(list(db.scalars(select(KnowledgeCurationMessage)).all())) == 1

        refined = asyncio.run(refine_curation_session(
            db,
            session,
            instruction="解决方案中增加修改前备份配置的步骤。",
            expected_draft_version=1,
            actor="USER-admin",
        ))
        assert refined.draft_version == 2
        assert "先备份配置" in refined.draft_markdown

        with pytest.raises(CurationConflict, match="Draft changed"):
            asyncio.run(refine_curation_session(
                db,
                refined,
                instruction="使用过期版本再次修改",
                expected_draft_version=1,
                actor="USER-admin",
            ))

        document = confirm_curation_session(
            db,
            refined,
            expected_draft_version=2,
            actor="USER-admin",
        )
        assert document.review_status == "DRAFT"
        assert document.active is False
        assert document.source_type == "fault_case"
        assert document.metadata_json.find(session_id) >= 0

    with factory() as db:
        session = db.get(KnowledgeCurationSession, session_id)
        document = db.get(KnowledgeDocument, session.knowledge_document_id)
        assert session.status == "CONFIRMED"
        assert session.confirmed_at is not None
        assert document.content == REFINED_MARKDOWN.strip() + "\n"
        assert len(list(db.scalars(
            select(KnowledgeCurationRevision).where(
                KnowledgeCurationRevision.session_id == session_id
            )
        ).all())) == 2
    engine.dispose()


def test_folder_upload_paths_are_portable_and_traversal_is_rejected(
    tmp_path: Path,
    monkeypatch,
) -> None:
    isolated_storage = StorageService(tmp_path / "storage")
    monkeypatch.setattr(knowledge_curation, "storage", isolated_storage)
    upload = UploadFile(filename="analysis.txt", file=BytesIO(SOURCE_TEXT.encode("utf-8")))
    manifest = asyncio.run(persist_curation_uploads(
        "KCUR-upload",
        [upload],
        ["fault-case/analysis.txt"],
    ))
    assert manifest[0]["relative_path"] == "fault-case/analysis.txt"
    assert isolated_storage.resolve_path(manifest[0]["stored_path"]).is_file()
    assert normalize_relative_path("folder\\log.txt") == "folder/log.txt"
    with pytest.raises(CurationError, match="Unsafe folder path"):
        normalize_relative_path("../secret.txt")


def test_out_of_range_source_citation_blocks_confirmation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine, factory = _factory(tmp_path)
    isolated_storage = StorageService(tmp_path / "storage")
    session_id = _seed_session(factory, isolated_storage)
    monkeypatch.setattr(knowledge_curation, "SessionLocal", factory)
    monkeypatch.setattr(knowledge_curation, "storage", isolated_storage)
    monkeypatch.setattr(knowledge_curation, "get_llm_provider", lambda profile=None: _Provider())
    curate_knowledge_folder_job(_Context(), session_id)

    invalid_markdown = INITIAL_MARKDOWN.replace(
        "[SRC-0001:L7-L8]",
        "[SRC-0001:L7-L99]",
    )
    with factory() as db:
        session = db.get(KnowledgeCurationSession, session_id)
        edited = save_manual_curation_draft(
            db,
            session,
            markdown=invalid_markdown,
            title="AP 认证超时案例",
            expected_draft_version=1,
            change_summary="测试越界引用",
            actor="USER-admin",
        )
        assert edited.draft_version == 2
        assert "[SRC-0001:L7-L99]" in edited.validation_json
        with pytest.raises(CurationError, match="cannot be confirmed"):
            confirm_curation_session(
                db,
                edited,
                expected_draft_version=2,
                actor="USER-admin",
            )
        assert db.get(KnowledgeCurationSession, session_id).status == "REVIEWING"
        assert db.scalar(select(KnowledgeDocument)) is None
    engine.dispose()


def test_folder_with_no_readable_text_fails_without_creating_a_draft(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine, factory = _factory(tmp_path)
    isolated_storage = StorageService(tmp_path / "storage")
    session_id = _seed_session(factory, isolated_storage)
    source_path = (
        isolated_storage.curation_dir(session_id)
        / "sources"
        / "case"
        / "analysis.txt"
    )
    source_path.write_bytes(b"\x00" * 2048)
    with factory() as db:
        source = db.get(KnowledgeCurationSourceFile, "KSRC-test")
        source.sha256 = sha256_file(source_path)
        source.size_bytes = source_path.stat().st_size
        db.commit()
    monkeypatch.setattr(knowledge_curation, "SessionLocal", factory)
    monkeypatch.setattr(knowledge_curation, "storage", isolated_storage)
    monkeypatch.setattr(knowledge_curation, "get_llm_provider", lambda profile=None: _Provider())

    with pytest.raises(CurationError, match="No readable text files"):
        curate_knowledge_folder_job(_Context(), session_id)

    with factory() as db:
        session = db.get(KnowledgeCurationSession, session_id)
        source = db.get(KnowledgeCurationSourceFile, "KSRC-test")
        assert session.status == "FAILED"
        assert session.draft_version == 0
        assert source.included is False
        assert source.skip_reason == "binary_or_unsupported_text_encoding"
    engine.dispose()


def test_document_extraction_is_used_for_evidence_and_source_preview(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine, factory = _factory(tmp_path)
    isolated_storage = StorageService(tmp_path / "storage")
    session_id = _seed_session(factory, isolated_storage)
    html_path = (
        isolated_storage.curation_dir(session_id)
        / "sources"
        / "case"
        / "analysis.html"
    )
    html_path.write_text(
        "<h1>AP failure</h1><script>ignore()</script>"
        "<p>Root cause: shared key mismatch</p>",
        encoding="utf-8",
    )
    monkeypatch.setattr(knowledge_curation, "storage", isolated_storage)

    with factory() as db:
        session = db.get(KnowledgeCurationSession, session_id)
        source = db.get(KnowledgeCurationSourceFile, "KSRC-test")
        source.relative_path = "case/analysis.html"
        source.stored_path = isolated_storage.storage_key(html_path)
        source.sha256 = sha256_file(html_path)
        source.size_bytes = html_path.stat().st_size
        db.commit()

        evidence, manifest = build_evidence_bundle(db, session)
        db.refresh(source)
        assert "Root cause: shared key mismatch" in evidence
        assert "ignore()" not in evidence
        assert source.extraction_method == "html_visible_text"
        assert source.extracted_text_path
        assert source.extracted_text_sha256
        assert source.line_count == 2
        assert manifest["document_extracted_source_refs"] == ["SRC-0001"]

        preview = preview_curation_source(source, start_line=1, line_count=10)
        assert "AP failure" in preview["text"]
        assert "ignore()" not in preview["text"]
        assert preview["extraction_method"] == "html_visible_text"
    engine.dispose()


def test_source_integrity_mismatch_blocks_evidence_and_preview(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine, factory = _factory(tmp_path)
    isolated_storage = StorageService(tmp_path / "storage")
    session_id = _seed_session(factory, isolated_storage)
    monkeypatch.setattr(knowledge_curation, "storage", isolated_storage)
    source_path = (
        isolated_storage.curation_dir(session_id)
        / "sources"
        / "case"
        / "analysis.txt"
    )
    source_path.write_text("tampered after upload", encoding="utf-8")

    with factory() as db:
        session = db.get(KnowledgeCurationSession, session_id)
        source = db.get(KnowledgeCurationSourceFile, "KSRC-test")
        with pytest.raises(CurationError, match="integrity check"):
            build_evidence_bundle(db, session)
        with pytest.raises(CurationError, match="integrity check"):
            preview_curation_source(source, start_line=1, line_count=10)
    engine.dispose()


def test_curation_api_upload_chat_and_confirm_flow(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine, factory = _factory(tmp_path)
    isolated_storage = StorageService(tmp_path / "api-storage")
    with factory() as db:
        seed_knowledge_categories(db)
        db.add(ModelProfile(
            id="MODEL-api-curation",
            name="API Curation",
            task_type="chat",
            mode="api",
            provider="openai_compatible",
            model_name="test-curation-model",
            base_url="https://model.example/v1",
            enabled=True,
            is_active=True,
        ))
        db.commit()

    monkeypatch.setattr(knowledge_curation, "SessionLocal", factory)
    monkeypatch.setattr(knowledge_curation, "storage", isolated_storage)
    monkeypatch.setattr(curation_api, "storage", isolated_storage)
    monkeypatch.setattr(knowledge_curation, "get_llm_provider", lambda profile=None: _Provider())
    monkeypatch.setattr(curation_api, "record_audit_event", lambda *args, **kwargs: None)

    def fake_submit(db, kind, function, *args, input_data=None, deduplicate=True):
        job = Job(
            id="JOB-api-curation",
            kind=kind,
            input_json=json_dumps(input_data or {}),
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        return job

    monkeypatch.setattr(curation_api.job_runner, "submit", fake_submit)
    with TestClient(_admin_app(factory)) as client:
        rejected = client.post(
            "/api/v1/knowledge-curations",
            files={"files": ("analysis.txt", SOURCE_TEXT, "text/plain")},
            data={
                "relative_paths_json": '["case/analysis.txt"]',
                "model_profile_id": "MODEL-api-curation",
                "consent_model_egress": "false",
            },
        )
        assert rejected.status_code == 409

        created = client.post(
            "/api/v1/knowledge-curations",
            files={"files": ("analysis.txt", SOURCE_TEXT, "text/plain")},
            data={
                "relative_paths_json": '["case/analysis.txt"]',
                "title_hint": "AP authentication timeout",
                "model_profile_id": "MODEL-api-curation",
                "consent_model_egress": "true",
            },
        )
        assert created.status_code == 202, created.text
        session_id = created.json()["session"]["id"]
        assert created.json()["session"]["status"] == "QUEUED"
        assert created.json()["session"]["sources"][0]["relative_path"] == "case/analysis.txt"

        result = curate_knowledge_folder_job(_Context(), session_id)
        assert result["confirmable"] is True
        detail = client.get(f"/api/v1/knowledge-curations/{session_id}")
        assert detail.status_code == 200
        assert detail.json()["status"] == "REVIEWING"

        refined = client.post(
            f"/api/v1/knowledge-curations/{session_id}/chat",
            json={
                "instruction": "补充修改前备份配置的步骤",
                "expected_draft_version": 1,
            },
        )
        assert refined.status_code == 200, refined.text
        assert refined.json()["draft_version"] == 2

        confirmed = client.post(
            f"/api/v1/knowledge-curations/{session_id}/confirm",
            json={"expected_draft_version": 2},
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["session"]["status"] == "CONFIRMED"
        assert confirmed.json()["knowledge_document"]["review_status"] == "DRAFT"
    engine.dispose()
