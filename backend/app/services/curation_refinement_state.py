"""Snapshot checks and milestones for one interactive curation revision."""
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.core.utils import json_loads
from app.models import KnowledgeCurationSession, ModelProfile
from app.services.knowledge_curation_common import CurationConflict, CurationError
from app.services.model_access import ModelAccessError


def prepare_refinement_session(
    db: Session,
    session: KnowledgeCurationSession,
    actor: str | None,
    expected_draft_version: int,
    model_snapshot: dict | None,
    *,
    resolve_session: Callable,
    resolve_model: Callable,
) -> tuple[ModelProfile, dict]:
    """Check the draft, pinned selection and consent before provider creation."""
    if session.status != "REVIEWING":
        raise CurationConflict("Only a reviewing session can be refined")
    if session.draft_version != expected_draft_version:
        raise CurationConflict("Draft changed; refresh before sending another correction")
    if actor != session.created_by:
        raise CurationError("Only the session owner may refine its private extraction")
    if model_snapshot is None:
        return resolve_session(db, session)
    try:
        if model_snapshot.get("model_actor_id") != actor:
            raise CurationError("Saved model owner no longer matches the curation session")
        profile = resolve_model(db, model_snapshot)
        saved_snapshot = json_loads(session.model_snapshot_json, {})
        if (not isinstance(saved_snapshot, dict)
                or saved_snapshot.get("model_profile_fingerprint") != model_snapshot.get("model_profile_fingerprint")):
            raise CurationError("The saved curation model changed; start a fresh request")
        snapshot = {**saved_snapshot, **model_snapshot}
        if (profile.mode == "api"
                and json_loads(session.source_manifest_json, {}).get("model_egress_consent") is not True):
            raise CurationError("Model API egress consent is disabled for this session")
        return profile, snapshot
    except ModelAccessError as exc:
        raise CurationError(str(exc)) from exc


def revalidate_refinement_snapshot(
    db: Session,
    session: KnowledgeCurationSession,
    actor: str | None,
    model_snapshot: dict,
    *,
    resolve_model: Callable,
    resolve_actor: Callable,
) -> ModelProfile:
    """Recheck live owner authority, consent and configuration after the wait."""
    try:
        principal = resolve_actor(db, actor)
        if principal["role"] not in {"ADMIN", "EXPERT", "ENGINEER"}:
            raise CurationError("The extraction owner can no longer contribute knowledge")
        if session.created_by != actor:
            raise CurationError("Only the session owner may refine its private extraction")
        saved_snapshot = json_loads(session.model_snapshot_json, {})
        if (not isinstance(saved_snapshot, dict)
                or saved_snapshot.get("model_profile_fingerprint") != model_snapshot.get("model_profile_fingerprint")):
            raise CurationError("The saved curation model changed; start a fresh request")
        if json_loads(session.source_manifest_json, {}).get("model_egress_consent") is not True:
            raise CurationError("Model API egress consent is disabled for this session")
        return resolve_model(db, model_snapshot)
    except ModelAccessError as exc:
        raise CurationError(str(exc)) from exc


def report_refinement_stage(ctx, stage_index: int, *, reporter: Callable) -> None:
    """Keep milestones outside the draft write transaction and preserve stops."""
    if not ctx:
        return
    progress, stage = {
        2: (35, "生成修订草稿"),
        3: (80, "核对输出"),
        4: (95, "保存待审版本"),
    }[stage_index]
    if stage_index != 2:
        ctx.raise_if_cancelled()
    reporter(
        ctx, progress, f"正在{stage}", stage=stage,
        stage_index=stage_index, stage_count=4, waiting_for_model=stage_index == 2,
    )
    if stage_index == 2:
        ctx.raise_if_cancelled()
