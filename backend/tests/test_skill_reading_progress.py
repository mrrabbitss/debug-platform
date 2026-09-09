"""Observe real Skill reader milestones with a synthetic in-process provider."""
from types import SimpleNamespace

from app.core.utils import json_loads
from app.models import Case
from app.services.diagnostic_agent_budget import DiagnosticAgentBudget
from app.services.diagnostic_skill_reading import read_skills
from app.services.agent_runtime.context import ContextWindowPolicy
from app.services.job_progress import active_job_context, track_model_request
from app.services.jobs import JobContext
from tests.test_job_progress import progress_db as progress_db, snapshot


def test_full_skill_reading_progress_counts_all_documents_and_cached_receipts(progress_db):
    with progress_db() as db:
        db.add(Case(id="case-progress", title="Synthetic progress", model_egress_approved=True))
        db.commit()
        case = db.get(Case, "case-progress")
    documents = [SimpleNamespace(id=f"doc-{index}", version=1, content="原则\n" * size,
        content_sha256=f"synthetic-{index}", public_snapshot=lambda: {}) for index, size in enumerate([2800, 1400])]
    total = sum(len(item.content) for item in documents)
    observed, characters = [], []

    class SyntheticReader:
        profile = None

        @track_model_request
        async def generate_json(self, system, user, **kwargs):
            request = json_loads(user, {})
            characters.append(request["end"] - request["start"])
            observed.append(snapshot(progress_db))
            return {"notes": "Synthetic validated notes"}

    ctx = JobContext("progress-job", lease_owner="owner")
    token = active_job_context.set(ctx)
    try:
        result = read_skills(ctx, provider=SyntheticReader(), case=case, agent_run_id="synthetic-run",
            methods=documents, session_factory=progress_db,
            context_policy=ContextWindowPolicy(context_window_tokens=8192, reserved_output_tokens=1024,
                                               safety_margin_tokens=512), budget=DiagnosticAgentBudget())
        assert result["model_reading"]["complete"] is True
        assert sum(characters) == total
        assert all(item.progress_detail["waiting_for_model"] for item in observed)
        assert all(item.progress_detail["total_units"] == total for item in observed)
        assert [item.progress_detail["completed_units"] for item in observed] == [
            sum(characters[:index]) for index in range(len(characters))]
        final = snapshot(progress_db)
        assert final.progress == 50 and final.progress_detail["completed_units"] == total
        assert final.status == "RUNNING"  # Reading is only one part of a diagnosis.
        original_calls = len(characters)
        read_skills(ctx, provider=SyntheticReader(), case=case, agent_run_id="synthetic-run",
            methods=documents, session_factory=progress_db,
            context_policy=ContextWindowPolicy(context_window_tokens=8192, reserved_output_tokens=1024,
                                               safety_margin_tokens=512), budget=DiagnosticAgentBudget())
        assert len(characters) == original_calls  # Resumed receipt replay needs no model work.
        assert snapshot(progress_db).progress == 50
    finally:
        active_job_context.reset(token)
