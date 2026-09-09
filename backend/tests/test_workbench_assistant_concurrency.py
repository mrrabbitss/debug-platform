"""Additional new assistant lifecycle, egress and concurrency checks."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import select

from app.core.utils import json_dumps, json_loads
from app.models import Job, KnowledgeChunk, KnowledgeDocument, KnowledgeGraphState, KnowledgePublication, ModelProfile
from app.services import jobs
from tests.test_workbench_assistant import (store, client, FakeChat, create, running, upload, get_value, reviewed,
    proposal, approve, add_doc, fake_indexes, api, state, sources, plans, sessions, runtime, assistant, publication)


def test_confirmation_concurrency_has_exactly_one_outbox_job(store, client):
    key = reviewed(store, [upload("folder/SKILL.md", "Synthetic")], [proposal()])
    version = get_value(store, key)[1]
    barrier = Barrier(2)

    def confirm():
        barrier.wait(timeout=5)
        return client.post(f"/workbench/assistant/{key}/confirm", json={"version": version})

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(confirm), pool.submit(confirm)
        results = [first.result(timeout=15), second.result(timeout=15)]
    assert sorted(result.status_code for result in results) in ([200, 200], [200, 409])
    accepted_job_ids = {result.json()["job_id"] for result in results if result.status_code == 200}
    assert len(accepted_job_ids) == 1
    replay = client.post(f"/workbench/assistant/{key}/confirm", json={"version": version})
    assert replay.status_code == 200 and replay.json()["job_id"] in accepted_job_ids
    with store() as db:
        publish_jobs = list(db.scalars(select(Job).where(Job.kind == "assistant_publish")))
        assert len(publish_jobs) == 1 and publish_jobs[0].id in accepted_job_ids


def test_concurrent_corrections_cannot_drop_either_accepted_message(store, client):
    key = create(store, consent=False)
    version = get_value(store, key)[1]
    barrier = Barrier(2)

    def correct(text):
        barrier.wait(timeout=5)
        return client.post(f"/workbench/assistant/{key}/messages", json={"version": version, "message": text})

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(correct, "First correction"), pool.submit(correct, "Second correction")
        results = [first.result(timeout=15), second.result(timeout=15)]
    assert sorted(result.status_code for result in results) == [200, 409]
    accepted = next(result.json() for result in results if result.status_code == 200)
    value, _ = get_value(store, key)
    assert value["messages"] == accepted["messages"]
    assert len(value["messages"]) == 2 and value["request_version"] == 2


def test_reading_recovery_resumes_and_old_attempt_cannot_save(store, monkeypatch):
    file = upload("folder/SKILL.md", "A" * 8100)
    model = FakeChat([{"action": "propose", "operations": [proposal()]},
        {"action": "finish", "answer": "Read fully", "evidence": [{"path": file["path"]}]}])
    interrupted = []

    def lose_lease(value):
        if value["start"] == 4000 and not interrupted:
            interrupted.append(True)
            raise jobs.JobLeaseLostError("Synthetic interruption")

    model.on_read = lose_lease
    monkeypatch.setattr(runtime, "get_llm_provider", lambda profile=None: model)
    key = create(store, [file])
    old_ctx, version = running(store, key)
    with pytest.raises(jobs.JobLeaseLostError):
        assistant.plan_job(old_ctx, key, version)
    with store() as db:
        job = db.get(Job, old_ctx.job_id)
        job.status, job.lease_owner = "QUEUED", None
        assert state.recover_abandoned_assistant_sessions(db) == 0
        db.commit()
    new_ctx, version = running(store, key)
    with store() as db:
        row, value = state.locked_session(db, key)
        state.claim_worker(db, row, value, new_ctx, version, {"READING"})
        db.commit()
    with pytest.raises(jobs.JobLeaseLostError):
        runtime.Runtime(old_ctx, key, version).save({"status": "FAILED"})
    # Run the reclaimed attempt through the already-claimed runtime.
    resumed = runtime.Runtime(new_ctx, key, version)
    resumed.read(file["path"])
    from app.services.assistant_controller import plan
    plan(resumed)
    assert get_value(store, key)[0]["status"] == "REVIEW"
    starts = [value["start"] for name, value in model.calls if name == "Reading"]
    assert starts.count(0) == 1 and starts.count(4000) == 2


def test_egress_revoked_in_flight_stops_receipt_and_next_call(store, monkeypatch):
    key = create(store, [upload("folder/SKILL.md", "A" * 8100)])
    model = FakeChat()

    def revoke(_):
        with store() as db:
            row, value = state.locked_session(db, key)
            sessions.consent(db, row, value, False)
            db.commit()

    model.on_read = revoke
    monkeypatch.setattr(runtime, "get_llm_provider", lambda profile=None: model)
    ctx, version = running(store, key)
    with pytest.raises(jobs.JobCancelledError):
        assistant.plan_job(ctx, key, version)
    value, _ = get_value(store, key)
    assert value["status"] == "PAUSED" and value["model_egress_approved"] is False
    assert len(model.calls) == 1 and not value["coverage"]


def test_redaction_matches_across_segment_boundary_without_changing_offsets(store, monkeypatch):
    text = "X" * 3995 + " api_key=SYNTHETIC_SECRET_VALUE\nTAIL"
    file = upload("folder/SKILL.md", text)
    model = FakeChat([{"action": "source", "path": file["path"], "cursor": 4000},
        {"action": "finish", "answer": "Read all segments", "evidence": [{"path": file["path"]}]}])
    monkeypatch.setattr(runtime, "get_llm_provider", lambda profile=None: model)
    key = create(store, [file], mode="answer")
    ctx, version = running(store, key)
    assistant.plan_job(ctx, key, version)
    texts = [value["text"] for name, value in model.calls if name == "Reading"]
    assert len("".join(texts)) == len(text)
    assert "SYNTHETIC_SECRET_VALUE" not in json_dumps(model.calls)
    assert "api_key=" not in "".join(texts)
    with store() as db:
        value = get_value(store, key)[0]
        assert sources.source_page(db, key, value, file["path"], 4000)["content"] == text[4000:]


def test_old_confirm_version_and_digest_rejected_after_correction(store, client):
    key = reviewed(store, [upload("folder/SKILL.md", "Synthetic")], [proposal()])
    value, version = get_value(store, key)
    response = client.post(f"/workbench/assistant/{key}/messages", json={"version": version,
        "message": "Change the category", "model_egress_approved": False})
    assert response.status_code == 200
    assert client.post(f"/workbench/assistant/{key}/confirm", json={"version": version,
        "review_digest": value["review_digest"]}).status_code == 409
    current = response.json()
    assert not current["approved_digest"] and not current["plan"]


def test_changed_target_pinned_version_blocks_confirmation(store, client):
    with store() as db:
        add_doc(db)
        db.commit()
    key = reviewed(store, [upload("folder/SKILL.md", "Synthetic")], [proposal(action="replace", target="DOC-old")], ["DOC-old"])
    with store() as db:
        db.get(KnowledgeDocument, "DOC-old").lock_version += 1
        db.commit()
    assert client.post(f"/workbench/assistant/{key}/confirm", json={"version": get_value(store, key)[1]}).status_code == 409


def test_generic_job_retry_cannot_reuse_publication_approval(store, monkeypatch):
    key = reviewed(store, [upload("folder/SKILL.md", "Synthetic")], [proposal()])
    fake_indexes(store, monkeypatch, fail_vectors=True)
    ctx, version = approve(store, key)
    approved = get_value(store, key)[0]
    with pytest.raises(ValueError):
        publication.publication_job(ctx, key, version, "local-development")
    with store() as db:
        db.get(Job, ctx.job_id).status = "FAILED"
        db.commit()
        retry = api.job_runner.retry(db, ctx.job_id)
        assert retry.id != ctx.job_id
        retry.status, retry.lease_owner, retry.attempt = "RUNNING", "retry-worker", 1
        db.commit()
        retry_ctx = jobs.JobContext(retry.id, lease_owner="retry-worker")
    with pytest.raises((ValueError, jobs.JobCancelledError, jobs.JobLeaseLostError)):
        publication.publication_job(retry_ctx, key, version, "local-development")
    value = get_value(store, key)[0]
    assert value["status"] == "PUBLISH_FAILED" and value["job_id"] == ctx.job_id
    for field in ("approved_digest", "approved_by", "approved_at", "request_version"):
        assert value[field] == approved[field]
    with store() as db:
        assert not list(db.scalars(select(KnowledgePublication)))
        assert db.get(KnowledgeGraphState, "domain").active_generation_id == "KGEN-old"
        assert db.get(ModelProfile, "MODEL-embedding").active_embedding_generation_id == "EGEN-old"


def test_retry_after_vector_failure_does_not_publish_abandoned_chunks(store, monkeypatch):
    with store() as db:
        add_doc(db)
        db.commit()
    key = reviewed(store, [upload("folder/SKILL.md", "Synthetic new")], [proposal(action="replace", target="DOC-old")], ["DOC-old"])
    fake_indexes(store, monkeypatch, fail_vectors=True)
    ctx, version = approve(store, key)
    approved = get_value(store, key)[0]
    with pytest.raises(ValueError):
        publication.publication_job(ctx, key, version, "local-development")
    with store() as db:
        abandoned = {chunk.id for chunk in db.scalars(select(KnowledgeChunk).where(KnowledgeChunk.document_version == 0))}
    assert abandoned
    original = publication.index_embeddings

    def successful_vectors(*args, **kwargs):
        try:
            original(*args, **kwargs)
        except RuntimeError:
            kwargs["progress"](1, 1)

    monkeypatch.setattr(publication, "index_embeddings", successful_vectors)
    with store() as db:
        row, value = state.locked_session(db, key)
        with pytest.raises(ValueError, match="正在执行或等待自动重试"):
            sessions.retry_reading(db, row, value, "local-development")
        db.get(Job, ctx.job_id).status = "FAILED"
        db.commit()
        row, value = state.locked_session(db, key)
        retry = sessions.retry_reading(db, row, value, "local-development")
        assert retry.id != ctx.job_id
        db.commit()
    ctx, retry_version = running(store, key)
    assert retry_version == version
    publication.publication_job(ctx, key, version, "local-development")
    value = get_value(store, key)[0]
    assert value["status"] == "PUBLISHED"
    for field in ("approved_digest", "approved_by", "approved_at", "request_version"):
        assert value[field] == approved[field]
    with store() as db:
        assert all(db.get(KnowledgeChunk, key).document_version == 0 for key in abandoned)
        current = list(db.scalars(select(KnowledgeChunk).where(KnowledgeChunk.document_version == 2)))
        assert len(current) == 1 and current[0].id not in abandoned


def test_graph_builder_contention_cannot_release_another_session_latch(store, monkeypatch):
    key = reviewed(store, [upload("folder/SKILL.md", "Synthetic")], [proposal()])
    fake_indexes(store, monkeypatch)
    with store() as db:
        db.get(KnowledgeGraphState, "domain").building_generation_id = "KGEN-other-session"
        db.commit()
    ctx, version = approve(store, key)
    approved = get_value(store, key)[0]
    with pytest.raises(ValueError):
        publication.publication_job(ctx, key, version, "local-development")
    with store() as db:
        assert db.get(KnowledgeGraphState, "domain").building_generation_id == "KGEN-other-session"
    value = get_value(store, key)[0]
    assert value["status"] == "PUBLISH_FAILED"
    assert value["approved_digest"] == approved["approved_digest"]
    assert value["request_version"] == version


def test_source_pages_and_malformed_uploads_are_explicit(client, store):
    import json
    text = "A" * 8100 + "END"
    response = client.post("/workbench/assistant", data={"paths": '["folder/SKILL.md"]', "model_egress_approved": "false"},
        files=[("files", ("SKILL.md", text.encode(), "text/markdown"))])
    assert response.status_code == 200
    key = response.json()["id"]
    cursor, pages = 0, []
    while cursor is not None:
        page = client.get(f"/workbench/assistant/{key}/source", params={"path": "folder/SKILL.md", "cursor": cursor}).json()
        pages.append(page["content"])
        cursor = page["next_cursor"]
    assert "".join(pages) == text
    assert client.post("/workbench/assistant", data={"paths": "invalid-json"}).status_code == 422
    assert client.post("/workbench/assistant", data={"paths": json.dumps(["SKILL.md"])},
        files=[("files", ("SKILL.md", b"A" * (1024 * 1024 + 1), "text/markdown"))]).status_code == 413
    assert client.post("/workbench/assistant", data={"paths": json.dumps(["SKILL.md", "skill.MD"])},
        files=[("files", ("SKILL.md", b"A", "text/markdown")), ("files", ("skill.MD", b"B", "text/markdown"))]).status_code == 422


def test_default_consent_true_and_model_is_never_called_by_http(client, store, monkeypatch):
    def never(profile=None):
        pytest.fail("HTTP handler called the model")
    monkeypatch.setattr(runtime, "get_llm_provider", never)
    response = client.post("/workbench/assistant", data={"message": "Query existing knowledge"})
    assert response.status_code == 200
    assert response.json()["model_egress_approved"] is True
    assert response.json()["status"] == "READING"


def test_actual_runner_marks_transactional_publication_completed(store, monkeypatch):
    key = reviewed(store, [upload("folder/SKILL.md", "Synthetic")], [proposal()])
    fake_indexes(store, monkeypatch)
    with store() as db:
        row, value = state.locked_session(db, key)
        job = sessions.approve(db, row, value, "local-development")
        db.commit()
    api.job_runner._run(job.id)
    with store() as db:
        assert db.get(Job, job.id).status == "COMPLETED"
    assert get_value(store, key)[0]["status"] == "PUBLISHED"


def test_all_bundle_documents_roll_back_when_second_revision_fails(store, monkeypatch):
    with store() as db:
        add_doc(db, "DOC-one")
        add_doc(db, "DOC-two")
        db.commit()
    files = [upload("folder/one.md", "New one"), upload("folder/two.md", "New two")]
    key = reviewed(store, files, [proposal(files[0]["path"], "replace", "DOC-one"),
        proposal(files[1]["path"], "replace", "DOC-two")], ["DOC-one", "DOC-two"])
    fake_indexes(store, monkeypatch)
    original = publication.create_document_revision
    calls = []

    def fail_second(db, document, **kwargs):
        calls.append(document.id)
        if len(calls) == 2:
            raise RuntimeError("Synthetic second-document failure")
        return original(db, document, **kwargs)

    monkeypatch.setattr(publication, "create_document_revision", fail_second)
    ctx, version = approve(store, key)
    with pytest.raises(ValueError):
        publication.publication_job(ctx, key, version, "local-development")
    assert calls == ["DOC-one", "DOC-two"]
    with store() as db:
        assert all(db.get(KnowledgeDocument, key).version == 1 for key in calls)
        assert all(db.get(KnowledgeDocument, key).content.startswith("# Old") for key in calls)
        assert db.get(ModelProfile, "MODEL-embedding").active_embedding_generation_id == "EGEN-old"
        assert db.get(KnowledgeGraphState, "domain").active_generation_id == "KGEN-old"


def test_angle_links_and_directory_dependencies_are_complete(store):
    files = [upload("folder/SKILL.md", "[all](<sub dir/>)\n[one](<sub dir/one.md>)"),
        upload("folder/sub dir/one.md", "One"), upload("folder/sub dir/two.md", "Two")]
    key = reviewed(store, files, [proposal(file["path"]) for file in files])
    value = get_value(store, key)[0]
    refs = value["bundle_manifest"][0]["resolved_references"]
    assert len(refs[0]["document_ids"]) == 2 and refs[1]["path"] == "folder/sub dir/one.md"
    from app.services.skill_dependencies import bundle_dependencies
    documents = [KnowledgeDocument(id=op["new_id"], content=op["after"], metadata_json=json_dumps({
        "bundle_id": key, "source_paths": op["source_paths"], "bundle_manifest": value["bundle_manifest"]})) for op in value["plan"]]
    dependencies, missing = bundle_dependencies(documents[0], documents)
    assert set(dependencies) == {doc.id for doc in documents[1:]} and not missing


def test_skipped_dependency_must_already_be_published(store):
    with store() as db:
        doc = add_doc(db)
        doc.active, doc.review_status = False, "DRAFT"
        db.commit()
    files = [upload("folder/SKILL.md", "[child](child.md)"), upload("folder/child.md", "Child")]
    with pytest.raises(ValueError, match="尚未向全员发布"):
        reviewed(store, files, [proposal(files[0]["path"]), proposal(files[1]["path"], "skip", "DOC-old")], ["DOC-old"])


def test_no_upload_reads_published_skill_and_normalized_dependencies(store, monkeypatch):
    with store() as db:
        root = add_doc(db, "DOC-root", "[child](child.md)")
        add_doc(db, "DOC-child", "Complete child method")
        root.metadata_json = json_dumps({"bundle_id": "synthetic-bundle", "source_paths": ["skill/SKILL.md"],
            "bundle_manifest": [{"path": "skill/SKILL.md", "document_ids": ["DOC-root"], "references": [
                {"path": "skill/child.md", "document_ids": ["DOC-child"], "external": False}]},
                {"path": "skill/child.md", "document_ids": ["DOC-child"], "references": []}]})
        db.commit()
    model = FakeChat([{"action": "read", "document_ids": ["DOC-root"]}, {"action": "finish", "answer": "Root and child read",
        "evidence": [{"path": "knowledge/DOC-root"}, {"path": "knowledge/DOC-child"}]}])
    monkeypatch.setattr(runtime, "get_llm_provider", lambda profile=None: model)
    key = create(store, mode="answer")
    ctx, version = running(store, key)
    assistant.plan_job(ctx, key, version)
    value = get_value(store, key)[0]
    assert value["coverage"]["knowledge/DOC-child"]["complete"]
    assert value["selected_paths"] == ["knowledge/DOC-root", "knowledge/DOC-child"]
