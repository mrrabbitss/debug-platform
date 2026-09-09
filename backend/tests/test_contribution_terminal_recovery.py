"""Abandoned contribution builds must not block every future publication."""
import pytest

from app.knowledge_contribution_models import KnowledgeContribution
from app.models import Job, KnowledgeGraphState, ModelProfile
from app.services import jobs, knowledge_contribution_publication as publication
from tests.test_knowledge_contributions_iteration import approval, submitted
from tests.test_knowledge_contributions_iteration import scope as scope


def interrupted_build(scope):
    factory, client = scope
    row = approval(client, submitted(client)).json()
    job_id = row["publication_job_id"]
    with factory() as db:
        job = db.get(Job, job_id)
        job.status, job.lease_owner = "RUNNING", "dead-worker"
        db.commit()
    fence = publication.ContributionFence(jobs.JobContext(job_id, lease_owner="dead-worker"), row["id"],
        row["approved_version"], row["approved_hash"], "expert")
    publication.prepare(fence)
    return row, fence


@pytest.mark.parametrize("terminal", ["CANCELLED", "DEAD_LETTER"])
def test_terminal_interrupted_contribution_releases_only_own_builder_and_keeps_approval(scope, terminal):
    factory, _ = scope
    row, fence = interrupted_build(scope)
    with factory() as db:
        db.get(Job, fence.ctx.job_id).status = terminal
        db.commit()
    with factory() as db:
        assert publication.recover_abandoned_contributions(db) == 1
        db.rollback()  # Recovery belongs to the caller's transaction.
    with factory() as db:
        assert db.get(KnowledgeGraphState, "domain").building_generation_id == fence.graph_id
        assert publication.recover_abandoned_contributions(db) == 1
        assert publication.recover_abandoned_contributions(db) == 0
        db.commit()
    with factory() as db:
        state = db.get(KnowledgeGraphState, "domain")
        assert state.building_generation_id is None and state.active_generation_id == "old-graph"
        assert db.get(ModelProfile, "embed").active_embedding_generation_id == "old-vector"
        pending = db.get(KnowledgeContribution, row["id"])
        assert pending.status == "APPROVED" and pending.worker_token is None
        assert pending.approved_hash == row["approved_hash"] and pending.approved_version == row["approved_version"]
        assert pending.building_generation_id is None and db.get(Job, fence.ctx.job_id).status == terminal


def test_recovery_keeps_queued_exact_approval_for_worker_takeover(scope):
    factory, _ = scope
    row, fence = interrupted_build(scope)
    with factory() as db:
        db.get(Job, fence.ctx.job_id).status = "QUEUED"
        db.commit()
    with factory() as db:
        assert publication.recover_abandoned_contributions(db) == 0
        assert db.get(KnowledgeGraphState, "domain").building_generation_id == fence.graph_id
        job = db.get(Job, fence.ctx.job_id)
        job.status, job.lease_owner = "RUNNING", "replacement-worker"
        db.commit()
    replacement = publication.ContributionFence(jobs.JobContext(job.id, lease_owner="replacement-worker"), row["id"],
        row["approved_version"], row["approved_hash"], "expert")
    publication.prepare(replacement)
    with factory() as db:
        pending = db.get(KnowledgeContribution, row["id"])
        assert pending.worker_token == replacement.token and pending.approved_hash == row["approved_hash"]
        assert db.get(KnowledgeGraphState, "domain").building_generation_id == replacement.graph_id


def test_recovery_never_releases_another_publications_generation(scope):
    factory, _ = scope
    row, fence = interrupted_build(scope)
    with factory() as db:
        db.get(Job, fence.ctx.job_id).status = "DEAD_LETTER"
        db.get(KnowledgeGraphState, "domain").building_generation_id = "other-publication-generation"
        db.commit()
    with factory() as db:
        assert publication.recover_abandoned_contributions(db) == 1
        db.commit()
    with factory() as db:
        state = db.get(KnowledgeGraphState, "domain")
        assert state.building_generation_id == "other-publication-generation" and state.status == "BUILDING"
        assert state.active_generation_id == "old-graph"
        assert db.get(KnowledgeContribution, row["id"]).approved_hash == row["approved_hash"]


def test_dispatcher_commits_terminal_contribution_recovery(scope, monkeypatch):
    factory, _ = scope
    row, fence = interrupted_build(scope)
    with factory() as db:
        db.get(Job, fence.ctx.job_id).status = "CANCEL_REQUESTED"
        db.get(Job, fence.ctx.job_id).lease_expires_at = None
        db.commit()
    scheduled = []
    monkeypatch.setattr(jobs.job_runner, "_schedule", scheduled.append)
    jobs.job_runner._recover_and_schedule()
    with factory() as db:
        assert db.get(Job, fence.ctx.job_id).status == "CANCELLED"
        pending = db.get(KnowledgeContribution, row["id"])
        assert pending.status == "APPROVED" and pending.worker_token is None
        assert pending.approved_hash == row["approved_hash"]
        state = db.get(KnowledgeGraphState, "domain")
        assert state.building_generation_id is None and state.active_generation_id == "old-graph"
    assert scheduled == []
