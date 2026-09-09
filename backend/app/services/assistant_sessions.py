"""Administrator transitions; the session and its queued job commit together."""
from app.core.utils import json_dumps, utcnow
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
    value.update(status="APPROVED", approved_by=actor, approved_digest=current,
                 approved_at=utcnow().isoformat(), error=None, requested_by=actor)
    return enqueue(db, row, value, "assistant_publish", reviewer=actor)


def retry_reading(db, row, value, actor):
    if value.get("status") not in {"FAILED", "CANCELLED", "PAUSED"}:
        raise ValueError("仅中断的阅读任务可以继续；发布必须重新核对确认")
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
