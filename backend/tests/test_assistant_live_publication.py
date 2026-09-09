"""Regression for the isolated real-HTTP approval database publication path."""
import time

from sqlalchemy import select

from app.models import Job, KnowledgeGraphState, ModelProfile
from app.services import assistant_publication as publication
from app.services import assistant_sessions as sessions
from app.services import assistant_state as state
from app.services import jobs
from tests.test_workbench_assistant import (
    get_value,
    proposal,
    reviewed,
    store as store,
    upload,
)


def test_threaded_publish_creates_and_refreshes_graph_state(store, monkeypatch):
    """A first publication has no graph row, as in the live acceptance DB."""
    key = reviewed(store, [upload("network/SKILL.md", "Synthetic network method")], [proposal("network/SKILL.md")])
    with store() as db:
        db.add(ModelProfile(id="MODEL-hashing", name="Synthetic hashing", task_type="embedding", mode="builtin",
            provider="hashing", model_name="synthetic", is_active=True, enabled=True))
        db.commit()

    def vectors(db, profile, chunks, *, generation_id, activate_if_missing, progress):
        assert profile.provider == "hashing" and not activate_if_missing
        progress(len(chunks), len(chunks))

    def graph(factory, documents, chunks, generation_id, ctx):
        ctx.raise_if_cancelled()
        return {"generation_id": generation_id, "documents": len(documents)}

    monkeypatch.setattr(publication, "index_embeddings", vectors)
    monkeypatch.setattr(publication, "stage_graph", graph)
    with store() as db:
        row, value = state.locked_session(db, key)
        job = sessions.approve(db, row, value, "local-development")
        db.commit()

    runner = jobs.JobRunner(max_workers=1, lease_seconds=10, heartbeat_seconds=1, dispatch_seconds=.01,
        retry_base_seconds=.01)
    runner.register("assistant_publish", publication.publication_job, ("session_id", "request_version", "reviewer"),
        cancellable=True, max_attempts=3, timeout_seconds=3600)
    try:
        runner._schedule(job.id)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with store() as db:
                if db.get(Job, job.id).status == "COMPLETED":
                    break
            time.sleep(.02)
    finally:
        runner.shutdown(wait=True)

    with store() as db:
        published = db.get(Job, job.id)
        graph_state = db.get(KnowledgeGraphState, "domain")
        assert published.status == "COMPLETED"
        assert graph_state and graph_state.status == "READY"
        assert graph_state.active_generation_id and graph_state.building_generation_id is None
        assert len(list(db.scalars(select(Job).where(Job.id == job.id)))) == 1
    assert get_value(store, key)[0]["status"] == "PUBLISHED"
