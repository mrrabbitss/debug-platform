"""Administrator transitions; the session and its queued job commit together."""
from app.core.utils import json_dumps, new_id, utcnow
from app.models import AuditEvent, Job
from app.services.assistant_plan import review_digest, validate_review
from app.services.assistant_state import cancel_session, enqueue, release_generation


def start_reading(db, row, value, actor, *, reset=False):
    release_generation(db, value)
    if reset:
        value.update(plan=[], pending_plan=[], planning_checkpoint=None, answer=None, answer_evidence=[],
                     bundle_manifest=None, review_digest=None)
    value.update(requested_by=actor, error=None)
    if value.get("model_egress_approved") is not True:
        value.update(status="PAUSED", job_id=None)
        row.payload_json = json_dumps(value)
        return None
    from app.services.model_access import chat_model_snapshot, principal_for_model_user, resolve_user_chat_profile
    identity = principal_for_model_user(db, actor)
    snapshot = chat_model_snapshot(db, identity, resolve_user_chat_profile(db, identity))
    if value.get("model_snapshot") != snapshot:
        value["coverage"] = {}
    value["model_snapshot"] = snapshot
    value["status"] = "READING"
    return enqueue(db, row, value, "assistant_plan")


def correct(db, row, value, payload, actor):
    if value.get("status") == "PUBLISHED":
        raise ValueError("该方案已发布，请新建会话")
    messages = value.get("messages", [])
    if sum(item["role"] == "user" for item in messages) >= 30:
        raise ValueError("当前会话已达30轮，请新建会话继续")
    if sum(len(item["content"]) for item in messages) + len(payload.message) > 32000:
        raise ValueError("对话超过32000字符，请新建会话；没有截断历史")
    cancel_session(db, row, value)
    messages.append({"role": "user", "content": payload.message})
    value["messages"] = messages
    if payload.mode is not None:
        value["mode"] = payload.mode
    if payload.model_egress_approved is not None:
        value["model_egress_approved"] = payload.model_egress_approved
    value["selected_paths"] = []
    return start_reading(db, row, value, actor, reset=True)


def approve(db, row, value, actor, expected_digest=None):
    if value.get("status") != "REVIEW":
        raise ValueError("方案尚未完成，请刷新后核对")
    current = review_digest(value)
    if value.get("review_digest") != current or (expected_digest is not None and expected_digest != current):
        raise ValueError("方案已变化，请重新核对")
    validate_review(db, row.id, value)
    value.update(status="APPROVED", approved_by=actor, approved_digest=current, approval_request_version=row.version,
                 approved_at=utcnow().isoformat(), error=None, requested_by=actor)
    db.add(AuditEvent(id=new_id("AUD"), action="knowledge.assistant.approved", actor_id=actor,
        actor_type="user", resource_type="knowledge_assistant", resource_id=row.id, outcome="SUCCESS",
        details_json=json_dumps({"review_digest": current, "request_version": value["request_version"],
                                "operation_count": len(value.get("plan", [])), "content_recorded": False})))
    return enqueue(db, row, value, "assistant_publish", reviewer=actor)


def retry_reading(db, row, value, actor):
    if value.get("status") == "PUBLISH_FAILED":
        from app.services.assistant_state import require_admin_actor
        require_admin_actor(db, actor)
        require_admin_actor(db, value.get("approved_by"))
        job = db.get(Job, value.get("job_id")) if value.get("job_id") else None
        if job and job.status in {"RUNNING", "QUEUED", "CANCEL_REQUESTED"}:
            raise ValueError("发布任务正在执行或等待自动重试")
        if not value.get("approved_at") or value.get("approved_digest") != review_digest(value):
            raise ValueError("审批已变化，请重新核对")
        validate_review(db, row.id, value)
        release_generation(db, value, revoke_approval=False)
        value.update(status="APPROVED", error=None)
        return enqueue(db, row, value, "assistant_publish", reviewer=value["approved_by"])
    if value.get("status") not in {"FAILED", "CANCELLED", "PAUSED"}:
        raise ValueError("仅中断的阅读或发布任务可以继续")
    if value.get("model_egress_approved") is not True:
        raise ValueError("请先开启模型授权，再继续阅读")
    value["request_version"] += 1
    return start_reading(db, row, value, actor)


def consent(db, row, value, enabled):
    if value.get("status") == "PUBLISHED":
        raise ValueError("该方案已发布")
    if value.get("model_egress_approved") is enabled:
        return
    was_review = value.get("status") == "REVIEW"
    cancel_session(db, row, value, pause=True)
    value["model_egress_approved"] = enabled
    if was_review:
        value["status"] = "REVIEW"
    if value["status"] == "REVIEW":
        value["review_digest"] = review_digest(value)
    row.payload_json = json_dumps(value)
