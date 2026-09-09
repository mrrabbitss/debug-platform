"""Synthetic 0.4 assistant checks; isolated DB and fake Chat/vector/graph only."""
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import sessionmaker

from app.core.db import Base, get_db
from app.core.utils import json_dumps, json_loads, utcnow, new_id
from app.models import (Job, KnowledgeDocument, KnowledgeChunk, KnowledgeEmbedding, KnowledgeGraphState,
                        ModelProfile, KnowledgePublication, UserAccount)
from app.workbench_models import WorkbenchRecord
from app.api import knowledge_assistant as api
from app.services import (assistant_state as state, assistant_sources as sources, assistant_plan as plans,
    assistant_sessions as sessions, assistant_runtime as runtime, assistant_controller as controller,
    knowledge_assistant as assistant, assistant_publication as publication, jobs)
from app.services.workbench import make_record


class FakeChat:
    is_mock = False
    base_url = None

    def __init__(self, steps=()):
        self.steps, self.calls = list(steps), []
        self.on_read = None

    async def generate_json(self, system, user, schema_name, purpose):
        value = json.loads(user)
        self.calls.append((schema_name, value))
        if schema_name == "Reading":
            if self.on_read:
                self.on_read(value)
            return {"notes": "Synthetic receipt: " + value["text"][-50:]}
        if not self.steps:
            raise RuntimeError("Unexpected fake model call")
        step = self.steps.pop(0)
        return step(value) if callable(step) else step


@pytest.fixture
def store(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'assistant.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    for module in (runtime, controller, assistant, publication, jobs):
        monkeypatch.setattr(module, "SessionLocal", factory)
    monkeypatch.setattr(state, "get_settings", lambda: SimpleNamespace(auth_mode="local", auth_allow_legacy_admin=False))
    from app.services import storage_capacity
    monkeypatch.setattr(storage_capacity, "require_storage_capacity", lambda: None)
    monkeypatch.setattr(api.job_runner, "_schedule", lambda *args: None)
    yield factory
    engine.dispose()


@pytest.fixture
def client(store):
    app = FastAPI()
    app.include_router(api.router)
    principal = {"id": "local-development", "role": "ADMIN"}

    @app.middleware("http")
    async def identity(request, call_next):
        request.state.principal = principal.copy()
        return await call_next(request)

    def database():
        with store() as db:
            yield db

    app.dependency_overrides[get_db] = database
    with TestClient(app) as client:
        client.principal = principal
        yield client


def upload(path, text):
    return {"path": path, "content": text, "sha256": state.digest(text), "references": sources.references(text)}


def create(store, files=(), *, mode="edit", consent=True):
    with store() as db:
        value = {"schema_version": 2, "files": list(files), "messages": [{"role": "user", "content": "Synthetic request"}],
            "request_version": 1, "mode": mode, "model_egress_approved": consent, "coverage": {}, "selected_paths": []}
        row = make_record(db, "assistant", "local-development", {})
        sessions.start_reading(db, row, value, "local-development", reset=True)
        db.commit()
        return row.id


def get_value(store, key):
    with store() as db:
        row = db.get(WorkbenchRecord, key)
        return json_loads(row.payload_json, {}), row.version


def running(store, key):
    with store() as db:
        value = json_loads(db.get(WorkbenchRecord, key).payload_json, {})
        job = db.get(Job, value["job_id"])
        job.status, job.lease_owner = "RUNNING", "worker-" + job.id
        job.lease_expires_at = utcnow() + timedelta(seconds=600)
        job.deadline_at = utcnow() + timedelta(seconds=3500)
        job.attempt += 1
        db.commit()
        return jobs.JobContext(job.id, lease_owner=job.lease_owner, lease_seconds=600), value["request_version"]


def complete_read(db, key, value, item):
    count = 0
    for start in range(0, len(item["content"]), sources.SEGMENT_CHARS):
        count += 1
        end = min(start + sources.SEGMENT_CHARS, len(item["content"]))
        receipt = {"path": item["path"], "sha256": item["sha256"], "segment": count, "start": start, "end": end,
            "text_sha256": state.digest(item["content"][start:end]), "complete": True, "notes": "Synthetic receipt"}
        db.merge(WorkbenchRecord(id=sources.receipt_key(key, item, start), kind="assistant_reading", owner_id=key,
                                payload_json=json_dumps(receipt)))
    value.setdefault("coverage", {})[item["path"]] = {"sha256": item["sha256"], "read": count, "total": count, "complete": True}
    db.flush()


def add_doc(db, key="DOC-old", text="# Old\nOriginal method\n", **kwargs):
    doc = KnowledgeDocument(id=key, title=key, content=text, active=True, review_status="ACTIVE",
        confidentiality="INTERNAL", metadata_json=json_dumps({"problem_categories": ["network"], "knowledge_role": "diagnosis"}), **kwargs)
    db.add(doc)
    db.flush()
    db.add(KnowledgeChunk(id="CHK-" + key, document_id=key, document_version=doc.version, chunk_index=0, content=text))
    db.flush()
    return doc


def proposal(path="folder/SKILL.md", action="create", target=None, **fields):
    return {"action": action, "title": "Synthetic method", "categories": ["network"], "role": "diagnosis",
        "reason": "Synthetic grounded change", "target_id": target, "sources": [{"path": path}], **fields}


def reviewed(store, files, proposals, targets=()):
    key = create(store, files)
    with store() as db:
        row = db.get(WorkbenchRecord, key)
        value = json_loads(row.payload_json, {})
        for item in files:
            complete_read(db, key, value, item)
        for target in targets:
            item = sources.snapshot_document(db, key, target, 1)
            complete_read(db, key, value, item)
        plan = []
        for raw in proposals:
            plan.append(plans.materialize(db, key, value, raw, plan))
        value.update(plan=plan, status="REVIEW", answer="Synthetic exact draft", bundle_manifest=None)
        value["bundle_manifest"] = plans.validate_review(db, key, value)
        value["review_digest"] = plans.review_digest(value)
        db.get(Job, value["job_id"]).status = "COMPLETED"
        row.payload_json = json_dumps(value)
        db.commit()
    return key


def approve(store, key):
    with store() as db:
        row, value = state.locked_session(db, key)
        sessions.approve(db, row, value, "local-development")
        db.commit()
    return running(store, key)


def fake_indexes(store, monkeypatch, on_graph=None, fail_vectors=False):
    with store() as db:
        db.add(ModelProfile(id="MODEL-embedding", name="Synthetic", task_type="embedding", mode="builtin",
            provider="hashing", model_name="synthetic", is_active=True, enabled=True, active_embedding_generation_id="EGEN-old"))
        db.add(KnowledgeGraphState(id="domain", active_generation_id="KGEN-old", status="READY"))
        for chunk in db.scalars(select(KnowledgeChunk)):
            db.add(KnowledgeEmbedding(id=new_id("VEC"), chunk_id=chunk.id, profile_id="MODEL-embedding",
                generation_id="EGEN-old", dimension=1, vector_json="[1]"))
        db.commit()

    def vectors(db, profile, chunks, *, generation_id, activate_if_missing, progress):
        assert activate_if_missing is False
        assert generation_id != "EGEN-old"
        for chunk in chunks:
            assert chunk.document_version >= 1
            db.add(KnowledgeEmbedding(id=new_id("VEC"), chunk_id=chunk.id, profile_id=profile.id,
                generation_id=generation_id, dimension=1, vector_json="[1]"))
        db.commit()
        if fail_vectors:
            raise RuntimeError("Synthetic vector failure with UNTRUSTED_MODEL_BODY")
        progress(len(chunks), len(chunks))

    def graph(factory, documents, chunks, generation_id, ctx):
        ctx.raise_if_cancelled()
        with store() as db:
            assert db.get(KnowledgeGraphState, "domain").active_generation_id == "KGEN-old"
            assert db.get(ModelProfile, "MODEL-embedding").active_embedding_generation_id == "EGEN-old"
            staged = list(db.scalars(select(KnowledgeChunk).where(KnowledgeChunk.document_version == 0)))
            assert staged
        if on_graph:
            on_graph()
        return {"generation_id": generation_id, "documents": len(documents)}

    monkeypatch.setattr(publication, "index_embeddings", vectors)
    monkeypatch.setattr(publication, "stage_graph", graph)


def test_full_folder_all_characters_and_durable_receipts(store, monkeypatch):
    files = [upload("folder/SKILL.md", "# Skill\n[child](sub/method.md)\n" + "A" * 8200 + "ROOT_END"),
             upload("folder/sub/method.md", "CHILD_BEGIN" + "B" * 4050 + "CHILD_END")]
    raw = [proposal(item["path"]) for item in files]
    model = FakeChat([{"action": "propose", "operations": raw}, {"action": "finish", "answer": "已完整读取并保留依赖。",
        "evidence": [{"path": file["path"]} for file in files]}])
    monkeypatch.setattr(runtime, "get_llm_provider", lambda: model)
    key = create(store, files)
    ctx, version = running(store, key)
    assistant.plan_job(ctx, key, version)
    value, _ = get_value(store, key)
    assert value["status"] == "REVIEW" and len(value["plan"]) == 2
    for file in files:
        calls = [data for name, data in model.calls if name == "Reading" and data["path"] == file["path"]]
        assert "".join(item["text"] for item in calls) == file["content"]
        assert all(len(item["text"]) <= sources.SEGMENT_CHARS for item in calls)
        assert value["coverage"][file["path"]]["complete"]
        with store() as db:
            sources.verify_receipts(db, key, value, file)
    root = value["bundle_manifest"][0]
    assert root["resolved_references"][0]["document_ids"] == [value["plan"][1]["new_id"]]
    assert value["messages"][-1]["role"] == "assistant"


def test_single_source_split_multiple_roles_and_sequential_same_target(store):
    with store() as db:
        add_doc(db)
        db.commit()
    files = [upload("folder/method.md", "FIRST_SECOND")]
    raw = [proposal("folder/method.md", sources=[{"path": "folder/method.md", "start": 0, "end": 6}], role="log_analysis"),
        proposal("folder/method.md", sources=[{"path": "folder/method.md", "start": 6}], role="fault_tree"),
        proposal("folder/method.md", "merge", "DOC-old", append_source=True),
        proposal("folder/method.md", "merge", "DOC-old", sources=[], edits=[{"old": "Original", "new": "Improved"}])]
    key = reviewed(store, files, raw, ["DOC-old"])
    value, _ = get_value(store, key)
    assert value["plan"][0]["after"] == "FIRST_" and value["plan"][1]["after"] == "SECOND"
    assert value["plan"][3]["before"] == value["plan"][2]["after"]
    assert "Improved" in value["plan"][3]["after"]
    assert len(value["bundle_manifest"][0]["document_ids"]) == 3


@pytest.mark.parametrize("action", ["create", "replace", "merge", "link", "skip"])
def test_skill_child_mapping_for_every_operation(store, action):
    with store() as db:
        add_doc(db)
        db.commit()
    files = [upload("folder/SKILL.md", "[method](sub/method.md#section)"), upload("folder/sub/method.md", "New method")]
    target = None if action == "create" else "DOC-old"
    op = proposal(files[1]["path"], action, target)
    if action == "merge":
        op["append_source"] = True
    key = reviewed(store, files, [proposal(files[0]["path"]), op], ["DOC-old"])
    value, _ = get_value(store, key)
    mapped = value["bundle_manifest"][0]["resolved_references"][0]
    assert mapped["path"] == files[1]["path"]
    assert mapped["document_id"] == (target or value["plan"][1]["new_id"])


def test_dependency_skip_without_mapping_blocks_review(store):
    files = [upload("folder/SKILL.md", "[child](child.md)"), upload("folder/child.md", "child")]
    with pytest.raises(ValueError, match="依赖文件"):
        reviewed(store, files, [proposal(files[0]["path"]), proposal(files[1]["path"], "skip")])


@pytest.mark.parametrize("changes", [
    {"execute": "rm"}, {"target_id": "DOC-does-not-exist", "action": "replace"},
    {"categories": ["not-a-category"]}, {"sources": [{"path": "../secret.md"}]},
    {"sources": [{"path": "folder/SKILL.md", "start": True}]},
    {"sources": [{"path": "folder/SKILL.md", "end": 100000}]},
    {"edits": [{"old": "absent", "new": "unsafe"}]}, {"append_source": "true"},
])
def test_untrusted_proposal_rejected(store, changes):
    file = upload("folder/SKILL.md", "Original")
    with pytest.raises(ValueError):
        reviewed(store, [file], [{**proposal(), **changes}])
    with store() as db:
        assert not list(db.scalars(select(KnowledgePublication)))


def test_missing_receipt_and_uncovered_ranges_block_approval(store):
    file = upload("folder/SKILL.md", "Original")
    key = reviewed(store, [file], [proposal()])
    with store() as db:
        value = json_loads(db.get(WorkbenchRecord, key).payload_json, {})
        db.delete(db.get(WorkbenchRecord, sources.receipt_key(key, file, 0)))
        db.flush()
        with pytest.raises(ValueError, match="凭据"):
            plans.validate_review(db, key, value)
    with pytest.raises(ValueError, match="来源章节"):
        reviewed(store, [file], [proposal(sources=[{"path": file["path"], "start": 1}])])


@pytest.mark.parametrize("mode", ["answer", "edit"])
def test_no_upload_query_and_existing_document_edits(store, monkeypatch, mode):
    with store() as db:
        add_doc(db)
        db.commit()
    steps = [{"action": "read", "document_ids": ["DOC-old"]}]
    if mode == "edit":
        steps.append({"action": "propose", "operations": [proposal("knowledge/DOC-old", "merge", "DOC-old",
            edits=[{"old": "Original method", "new": "Improved method"}])]})
    steps.append({"action": "finish", "answer": "文档包含可核对的方法。", "evidence": [{"path": "knowledge/DOC-old"}]})
    model = FakeChat(steps)
    monkeypatch.setattr(runtime, "get_llm_provider", lambda: model)
    key = create(store, mode=mode)
    ctx, version = running(store, key)
    assistant.plan_job(ctx, key, version)
    value, _ = get_value(store, key)
    assert value["answer_evidence"][0]["document_version"] == 1
    assert value["status"] == "REVIEW"
    assert bool(value["plan"]) == (mode == "edit")
    with store() as db:
        assert "Original" in db.get(KnowledgeDocument, "DOC-old").content


def test_answer_mode_and_forged_answer_evidence_fail_closed(store, monkeypatch):
    file = upload("folder/SKILL.md", "Original")
    for step in ({"action": "propose", "operations": [proposal()]},
                 {"action": "finish", "answer": "Unsupported", "evidence": [{"path": "knowledge/unknown"}]}):
        monkeypatch.setattr(runtime, "get_llm_provider", lambda: FakeChat([step]))
        key = create(store, [file], mode="answer")
        ctx, version = running(store, key)
        with pytest.raises(ValueError):
            assistant.plan_job(ctx, key, version)
        assert get_value(store, key)[0]["status"] == "FAILED"


def test_reading_budget_pause_retry_reuses_receipts(store, monkeypatch):
    file = upload("folder/SKILL.md", "A" * 8100)
    model = FakeChat([{"action": "propose", "operations": [proposal()]},
        {"action": "finish", "answer": "Complete", "evidence": [{"path": file["path"]}]}])
    monkeypatch.setattr(runtime, "get_llm_provider", lambda: model)
    monkeypatch.setattr(runtime, "MAX_CALLS", 1)
    key = create(store, [file])
    ctx, version = running(store, key)
    assistant.plan_job(ctx, key, version)
    value, _ = get_value(store, key)
    assert value["status"] == "PAUSED" and value["coverage"][file["path"]]["read"] == 1
    with store() as db:
        row, value = state.locked_session(db, key)
        sessions.retry_reading(db, row, value, "local-development")
        db.commit()
    monkeypatch.setattr(runtime, "MAX_CALLS", 256)
    ctx, version = running(store, key)
    assistant.plan_job(ctx, key, version)
    assert get_value(store, key)[0]["status"] == "REVIEW"
    assert [v["start"] for name, v in model.calls if name == "Reading"] == [0, 4000, 8000]


def test_correction_during_call_fences_old_worker_and_invalidates_plan(store, monkeypatch):
    key = create(store, [upload("folder/SKILL.md", "Original")])
    model = FakeChat()

    def correction(_):
        with store() as db:
            row, value = state.locked_session(db, key)
            sessions.correct(db, row, value, api.ConversationInput(version=row.version, message="只回答，不修改", mode="answer"), "local-development")
            db.commit()

    model.on_read = correction
    monkeypatch.setattr(runtime, "get_llm_provider", lambda: model)
    ctx, version = running(store, key)
    with pytest.raises(jobs.JobCancelledError):
        assistant.plan_job(ctx, key, version)
    value, _ = get_value(store, key)
    assert value["status"] == "READING" and value["request_version"] == 2
    assert not value["plan"] and not value["approved_digest"]
    assert value["job_id"] != ctx.job_id


def test_success_publishes_bundle_once_and_keeps_old_chunks(store, monkeypatch):
    with store() as db:
        add_doc(db)
        db.commit()
    files = [upload("folder/SKILL.md", "new appendix"), upload("folder/other.md", "# Second method")]
    key = reviewed(store, files, [proposal(files[0]["path"], "merge", "DOC-old", append_source=True),
        proposal(files[0]["path"], "merge", "DOC-old", sources=[], edits=[{"old": "Original", "new": "Improved"}]),
        proposal(files[1]["path"])], ["DOC-old"])
    fake_indexes(store, monkeypatch)
    ctx, version = approve(store, key)
    result = publication.publication_job(ctx, key, version, "local-development")
    with store() as db:
        doc = db.get(KnowledgeDocument, "DOC-old")
        assert doc.version == 2 and "Improved" in doc.content and "new appendix" in doc.content
        assert db.get(KnowledgeChunk, "CHK-DOC-old").document_version == 1
        assert len(list(db.scalars(select(KnowledgePublication)))) == 2
        assert db.get(KnowledgeGraphState, "domain").active_generation_id == result["graph_generation_id"]
        assert db.get(ModelProfile, "MODEL-embedding").active_embedding_generation_id == result["embedding_generation_id"]
        assert db.get(Job, ctx.job_id).status == "COMPLETED"
    assert get_value(store, key)[0]["status"] == "PUBLISHED"


@pytest.mark.parametrize("failure", ["vector", "graph", "target", "profile", "cancel", "commit"])
def test_publication_failure_retains_old_generation_and_requires_fresh_approval(store, monkeypatch, failure):
    with store() as db:
        add_doc(db)
        db.commit()
    file = upload("folder/SKILL.md", "New content")
    key = reviewed(store, [file], [proposal(action="replace", target="DOC-old")], ["DOC-old"])

    def intervene():
        if failure == "graph":
            raise RuntimeError("Synthetic graph failure")
        with store() as db:
            if failure == "target":
                doc = db.get(KnowledgeDocument, "DOC-old")
                doc.module = "concurrent-edit"
            if failure == "profile":
                db.get(ModelProfile, "MODEL-embedding").config_json = '{"batch_size":1}'
            if failure == "cancel":
                row, value = state.locked_session(db, key)
                state.cancel_session(db, row, value)
            db.commit()

    fake_indexes(store, monkeypatch, intervene, fail_vectors=failure == "vector")
    ctx, version = approve(store, key)
    if failure == "commit":
        monkeypatch.setattr(ctx, "complete_in_transaction", lambda *args: (_ for _ in ()).throw(RuntimeError("Synthetic final marker failure")))
    with pytest.raises((ValueError, jobs.JobCancelledError, jobs.JobLeaseLostError)):
        publication.publication_job(ctx, key, version, "local-development")
    value, _ = get_value(store, key)
    assert value["status"] == "REVIEW" and value["approved_digest"] is None
    with store() as db:
        assert db.get(KnowledgeDocument, "DOC-old").content == "# Old\nOriginal method\n"
        assert db.get(KnowledgeDocument, "DOC-old").version == 1
        assert db.get(ModelProfile, "MODEL-embedding").active_embedding_generation_id == "EGEN-old"
        assert db.get(KnowledgeGraphState, "domain").active_generation_id == "KGEN-old"
        assert not list(db.scalars(select(KnowledgePublication)))
    with pytest.raises((ValueError, jobs.JobCancelledError, jobs.JobLeaseLostError)):
        publication.publication_job(ctx, key, version, "local-development")


def test_abandoned_publication_clears_latch_and_never_republishes(store, monkeypatch):
    file = upload("folder/SKILL.md", "Synthetic")
    key = reviewed(store, [file], [proposal()])
    fake_indexes(store, monkeypatch)
    ctx, version = approve(store, key)
    fence = publication.PublicationFence(ctx, key, version, "local-development", "KGEN-interrupted")
    publication.prepare(fence, "EGEN-interrupted")
    with store() as db:
        job = db.get(Job, ctx.job_id)
        job.status, job.lease_owner = "QUEUED", None
        assert state.recover_abandoned_assistant_sessions(db) == 1
        db.commit()
    value, _ = get_value(store, key)
    assert value["status"] == "REVIEW" and not value["approved_digest"]
    with store() as db:
        assert db.get(Job, ctx.job_id).status == "CANCELLED"
        assert db.get(KnowledgeGraphState, "domain").building_generation_id is None
    with pytest.raises((ValueError, jobs.JobCancelledError, jobs.JobLeaseLostError)):
        publication.publication_job(ctx, key, version, "local-development")


def test_api_consent_outbox_versions_and_admin_only(client, store, monkeypatch):
    scheduled = []

    def wake(job_id):
        with store() as db:
            job = db.get(Job, job_id)
            key = json_loads(job.input_json, {})["session_id"]
            assert json_loads(db.get(WorkbenchRecord, key).payload_json, {})["job_id"] == job_id
            scheduled.append(job_id)

    monkeypatch.setattr(api.job_runner, "_schedule", wake)
    response = client.post("/workbench/assistant", data={"message": "Query", "model_egress_approved": "false"})
    assert response.status_code == 200
    value = response.json()
    assert value["status"] == "PAUSED" and not scheduled
    key = value["id"]
    response = client.patch(f"/workbench/assistant/{key}/consent", json={"version": value["version"], "model_egress_approved": True})
    value = response.json()
    assert value["status"] == "PAUSED" and not scheduled
    value = client.post(f"/workbench/assistant/{key}/retry", json={"version": value["version"]}).json()
    assert value["status"] == "READING" and len(scheduled) == 1
    assert client.post(f"/workbench/assistant/{key}/pause", json={"version": value["version"] - 1}).status_code == 409
    value = client.post(f"/workbench/assistant/{key}/pause", json={"version": value["version"]}).json()
    assert value["status"] == "PAUSED"
    client.principal.update(id="engineer", role="ENGINEER")
    assert client.get(f"/workbench/assistant/{key}").status_code == 403


@pytest.mark.parametrize("path", ["../secret.md", "/root.md", "C:\\secret.md", "folder//x.md", "folder/./x.md"])
def test_api_rejects_unsafe_paths(client, path):
    response = client.post("/workbench/assistant", data={"paths": json.dumps([path]), "message": "Synthetic"},
                           files=[("files", ("text.md", b"synthetic", "text/plain"))])
    assert response.status_code == 422


def test_catalogue_is_paginated_beyond_old_2000_limit(store):
    with store() as db:
        db.add_all(KnowledgeDocument(id=f"DOC-{index:04}", title=f"Synthetic {index}", content="text") for index in range(2010))
        db.commit()
        page = sources.catalogue_page(db, query="Synthetic 2009")
        assert page["items"][0]["id"] == "DOC-2009"
        first = sources.catalogue_page(db)
        second = sources.catalogue_page(db, first["next_cursor"])
        assert len(first["items"]) == sources.PAGE_SIZE
        assert first["items"][-1]["id"] < second["items"][0]["id"]


def test_endpoint_guard_and_egress_revocation_before_next_segment(store, monkeypatch):
    model = FakeChat()
    model.base_url = "http://169.254.169.254/latest"
    monkeypatch.setattr(runtime, "get_llm_provider", lambda: model)
    key = create(store, [upload("folder/SKILL.md", "Synthetic")])
    ctx, version = running(store, key)
    with pytest.raises(ValueError):
        assistant.plan_job(ctx, key, version)
    assert not model.calls
    assert "169.254" not in get_value(store, key)[0]["error"]


def test_lan_actor_revalidation(store, monkeypatch):
    monkeypatch.setattr(state, "get_settings", lambda: SimpleNamespace(auth_mode="rbac", auth_allow_legacy_admin=False))
    with store() as db:
        with pytest.raises(ValueError):
            state.require_admin_actor(db, "local-development")
        db.add(UserAccount(id="ADMIN-synthetic", username="synthetic-admin", display_name="Synthetic", role="ADMIN", active=True))
        db.flush()
        state.require_admin_actor(db, "ADMIN-synthetic")
        db.get(UserAccount, "ADMIN-synthetic").active = False
        with pytest.raises(ValueError):
            state.require_admin_actor(db, "ADMIN-synthetic")
