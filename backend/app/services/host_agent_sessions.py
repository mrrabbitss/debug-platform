from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.utils import json_dumps, json_loads, mask_sensitive, new_id, utcnow
from app.host_agent_models import HostAgentSession
from app.models import AgentRun, Case
from app.services.host_agent_session_contracts import (
    TERMINAL_HOST_AGENT_SESSION_STATUSES,
    HostAgentEvidenceCacheUpdate,
    HostAgentPlanningRound,
    HostAgentSessionCancel,
    HostAgentSessionCoverageUpdate,
    HostAgentSessionCreate,
    HostAgentSessionLeaseRelease,
    HostAgentSessionLeaseRequest,
    HostAgentSessionSnapshotUpdate,
    HostAgentSessionStatusTransition,
    HostAgentSessionView,
    HostAgentToolReceipt,
    HostAgentToolReceiptInput,
)


MAX_SNAPSHOT_JSON_CHARS = 2_000_000
MAX_COVERAGE_JSON_CHARS = 1_000_000
MAX_EVIDENCE_CACHE_JSON_CHARS = 4_000_000
MAX_TOOL_RECEIPTS_JSON_CHARS = 2_000_000
MAX_SAFE_STRING_CHARS = 16_000
MAX_JSON_DEPTH = 12
MAX_COLLECTION_ITEMS = 2_000
_RAW_OR_SECRET_KEYS = frozenset({
    "api_key",
    "authorization",
    "cookie",
    "credentials",
    "full_log",
    "headers",
    "password",
    "raw",
    "raw_content",
    "raw_log",
    "raw_text",
    "request_body",
    "response_body",
    "secret",
    "stored_path",
    "token",
    "tool_output",
    "unredacted",
})

_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "CREATED": frozenset({
        "METHODS_READ", "SEARCHING", "FAILED", "CANCELLED", "EXPIRED",
    }),
    "METHODS_READ": frozenset({
        "SEARCHING", "DRAFT_SUBMITTED", "FAILED", "CANCELLED", "EXPIRED",
    }),
    "SEARCHING": frozenset({
        "DRAFT_SUBMITTED", "FAILED", "CANCELLED", "EXPIRED",
    }),
    "DRAFT_SUBMITTED": frozenset({
        "VALIDATED", "REJECTED", "FAILED", "CANCELLED", "EXPIRED",
    }),
    "REJECTED": frozenset({
        "SEARCHING", "DRAFT_SUBMITTED", "FAILED", "CANCELLED", "EXPIRED",
    }),
    "VALIDATED": frozenset({
        "COMPLETED", "FAILED", "CANCELLED", "EXPIRED",
    }),
    "COMPLETED": frozenset(),
    "FAILED": frozenset(),
    "CANCELLED": frozenset(),
    "EXPIRED": frozenset(),
}


class HostAgentSessionError(ValueError):
    pass


class HostAgentSessionNotFoundError(HostAgentSessionError):
    pass


class HostAgentSessionConflictError(HostAgentSessionError):
    pass


class HostAgentSessionLeaseConflictError(HostAgentSessionConflictError):
    pass


class HostAgentSessionTransitionError(HostAgentSessionError):
    pass


class HostAgentSessionExpiredError(HostAgentSessionTransitionError):
    pass


def hash_host_agent_tool_arguments(arguments: Mapping[str, Any]) -> str:
    rendered = json.dumps(
        arguments,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _safe_json(value: Any, *, depth: int = 0) -> Any:
    if depth > MAX_JSON_DEPTH:
        return "<OMITTED:DEPTH_LIMIT>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        bounded = value[:MAX_SAFE_STRING_CHARS]
        if len(value) > MAX_SAFE_STRING_CHARS:
            bounded += "…[truncated]"
        return mask_sensitive(bounded)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for position, (raw_key, item) in enumerate(value.items()):
            if position >= MAX_COLLECTION_ITEMS:
                result["_omitted_items"] = len(value) - MAX_COLLECTION_ITEMS
                break
            key = str(raw_key)[:128]
            if key.casefold() in _RAW_OR_SECRET_KEYS:
                continue
            result[key] = _safe_json(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        values = list(value)
        result = [
            _safe_json(item, depth=depth + 1)
            for item in values[:MAX_COLLECTION_ITEMS]
        ]
        if len(values) > MAX_COLLECTION_ITEMS:
            result.append({"_omitted_items": len(values) - MAX_COLLECTION_ITEMS})
        return result
    return mask_sensitive(str(value)[:MAX_SAFE_STRING_CHARS])


def _bounded_json(value: Any, *, max_chars: int, label: str) -> str:
    rendered = json_dumps(value)
    if len(rendered) > max_chars:
        raise HostAgentSessionError(f"{label} exceeds the persisted size limit")
    return rendered


def _normalize_ids(values: list[str]) -> list[str]:
    return list(dict.fromkeys(
        str(value).strip()[:128]
        for value in values
        if str(value).strip()
    ))


def _deep_merge(current: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result = dict(current)
    for key, value in patch.items():
        existing = result.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            result[key] = _deep_merge(existing, value)
        else:
            result[key] = value
    return result


def _sanitize_evidence_item(evidence_id: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise HostAgentSessionError(
            f"Evidence cache item {evidence_id!r} must be an object"
        )
    sanitized = _safe_json(value)
    if not isinstance(sanitized, dict):
        raise HostAgentSessionError(
            f"Evidence cache item {evidence_id!r} could not be sanitized"
        )
    claimed_id = str(sanitized.get("evidence_id") or evidence_id)
    if claimed_id != evidence_id:
        raise HostAgentSessionError(
            f"Evidence cache key {evidence_id!r} does not match its evidence_id"
        )
    sanitized["evidence_id"] = evidence_id
    return sanitized


def _merge_evidence_cache(
    current: dict[str, Any],
    incoming: Mapping[str, Any],
    *,
    allowed_ids: set[str],
) -> dict[str, Any]:
    unknown_ids = set(incoming).difference(allowed_ids)
    if unknown_ids:
        raise HostAgentSessionError(
            "Evidence cache contains IDs that were not returned to this session: "
            + ", ".join(sorted(unknown_ids)[:10])
        )
    merged = dict(current)
    for evidence_id, value in incoming.items():
        merged[evidence_id] = _sanitize_evidence_item(evidence_id, value)
    return merged


def _require_record(db: Session, session_id: str) -> HostAgentSession:
    record = db.get(HostAgentSession, session_id)
    if not record:
        raise HostAgentSessionNotFoundError("Host-agent session not found")
    return record


def _ensure_mutable(
    record: HostAgentSession,
    *,
    now: datetime,
    allow_expired: bool = False,
) -> None:
    if record.status in TERMINAL_HOST_AGENT_SESSION_STATUSES:
        raise HostAgentSessionTransitionError(
            f"Host-agent session is terminal: {record.status}"
        )
    if not allow_expired and _as_utc(record.expires_at) <= _as_utc(now):
        raise HostAgentSessionExpiredError("Host-agent session TTL has expired")


def _ensure_lease(
    record: HostAgentSession,
    lease_owner: str | None,
    *,
    now: datetime,
) -> None:
    if not record.lease_owner or not record.lease_expires_at:
        return
    if _as_utc(record.lease_expires_at) <= _as_utc(now):
        return
    if lease_owner != record.lease_owner:
        raise HostAgentSessionLeaseConflictError(
            "Host-agent session is leased by another client"
        )


def _agent_status_values(
    status: str,
    *,
    reason: str,
    now: datetime,
    allowed_evidence_ids: list[str] | None = None,
) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if status in TERMINAL_HOST_AGENT_SESSION_STATUSES:
        values.update({
            "status": status,
            "stop_reason": {
                "COMPLETED": "HOST_AGENT_COMPLETED",
                "FAILED": "HOST_AGENT_FAILED",
                "CANCELLED": "HOST_AGENT_CANCELLED",
                "EXPIRED": "HOST_AGENT_SESSION_EXPIRED",
            }[status],
            "completed_at": now,
            "output_summary_hash": hashlib.sha256(
                f"{status}\0{reason}".encode("utf-8")
            ).hexdigest(),
        })
    else:
        values.update({"status": "RUNNING", "stop_reason": status})
    if allowed_evidence_ids is not None:
        values["evidence_ids_json"] = json_dumps(allowed_evidence_ids)
    return values


def _cas_update(
    db: Session,
    record: HostAgentSession,
    *,
    expected_version: int,
    values: dict[str, Any],
    now: datetime,
    agent_run_values: dict[str, Any] | None = None,
) -> HostAgentSessionView:
    values = {
        **values,
        "version": expected_version + 1,
        "updated_at": now,
        "last_activity_at": now,
    }
    result = db.execute(
        update(HostAgentSession)
        .where(
            HostAgentSession.id == record.id,
            HostAgentSession.version == expected_version,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        raise HostAgentSessionConflictError(
            "Host-agent session changed; refresh and retry"
        )
    if agent_run_values:
        db.execute(
            update(AgentRun)
            .where(AgentRun.id == record.agent_run_id)
            .values(**agent_run_values)
            .execution_options(synchronize_session=False)
        )
    db.commit()
    db.expire(record)
    db.refresh(record)
    return host_agent_session_to_view(record)


def host_agent_session_to_view(record: HostAgentSession) -> HostAgentSessionView:
    raw_receipts = json_loads(record.tool_receipts_json, [])
    receipts: list[HostAgentToolReceipt] = []
    for item in raw_receipts if isinstance(raw_receipts, list) else []:
        try:
            receipts.append(HostAgentToolReceipt.model_validate(item))
        except ValidationError as exc:
            raise HostAgentSessionError(
                "Persisted host-agent tool receipt is invalid"
            ) from exc
    snapshot = json_loads(record.snapshot_json, {})
    coverage = json_loads(record.coverage_json, {})
    raw_rounds = json_loads(record.planning_rounds_json, [])
    planning_rounds = ([HostAgentPlanningRound.model_validate(item)
                        for item in raw_rounds if isinstance(item, dict)]
                       if isinstance(raw_rounds, list) else [])
    allowed = json_loads(record.allowed_evidence_ids_json, [])
    evidence_cache = json_loads(record.evidence_cache_json, {})
    return HostAgentSessionView(
        id=record.id,
        case_id=record.case_id,
        agent_run_id=record.agent_run_id,
        executor=record.executor,
        client_model_claim=record.client_model_claim,
        client_model_claim_verified=False,
        prompt_version=record.prompt_version,
        skill_version=record.skill_version,
        case_snapshot_hash=record.case_snapshot_hash,
        parse_snapshot_hash=record.parse_snapshot_hash,
        method_snapshot_hash=record.method_snapshot_hash,
        snapshot=snapshot if isinstance(snapshot, dict) else {},
        coverage=coverage if isinstance(coverage, dict) else {},
        planning_rounds=planning_rounds,
        allowed_evidence_ids=(allowed if isinstance(allowed, list) else []),
        tool_receipts=receipts,
        evidence_cache=(evidence_cache if isinstance(evidence_cache, dict) else {}),
        status=record.status,
        status_reason=record.status_reason,
        version=record.version,
        lease_owner=record.lease_owner,
        lease_expires_at=record.lease_expires_at,
        heartbeat_at=record.heartbeat_at,
        expires_at=record.expires_at,
        created_by=record.created_by,
        created_at=record.created_at,
        updated_at=record.updated_at,
        last_activity_at=record.last_activity_at,
        completed_at=record.completed_at,
    )


def create_host_agent_session(
    db: Session,
    request: HostAgentSessionCreate,
    *,
    created_by: str | None = None,
    now: datetime | None = None,
) -> HostAgentSessionView:
    if not db.get(Case, request.case_id):
        raise HostAgentSessionNotFoundError("Case not found")
    current_time = now or utcnow()
    session_id = new_id("HASESS")
    snapshot = _safe_json(request.snapshot)
    snapshot_json = _bounded_json(
        snapshot, max_chars=MAX_SNAPSHOT_JSON_CHARS, label="Host-agent snapshot"
    )
    input_summary = {
        "case_id": request.case_id,
        "executor": request.executor,
        "case_snapshot_hash": request.case_snapshot_hash,
        "parse_snapshot_hash": request.parse_snapshot_hash,
        "method_snapshot_hash": request.method_snapshot_hash,
        "skill_version": request.skill_version,
    }
    agent_run = AgentRun(
        id=new_id("ARUN"),
        case_id=request.case_id,
        resource_type="host_agent_session",
        resource_id=session_id,
        operation="diagnose",
        execution_mode="host_cli",
        status="RUNNING",
        model_profile_id=None,
        model_name=request.client_model_claim,
        model_config_json=json_dumps({
            "executor": request.executor,
            "skill_version": request.skill_version,
            "client_model_claim_verified": False,
        }),
        prompt_version=request.prompt_version,
        input_summary_hash=hash_host_agent_tool_arguments(input_summary),
        output_summary_hash=None,
        evidence_ids_json="[]",
        stop_reason="CREATED",
        approval_status="READ_ONLY_AUTO",
        created_by=(created_by or "")[:128] or None,
        created_at=current_time,
        started_at=current_time,
        completed_at=None,
    )
    record = HostAgentSession(
        id=session_id,
        case_id=request.case_id,
        agent_run_id=agent_run.id,
        executor=request.executor,
        client_model_claim=request.client_model_claim,
        prompt_version=request.prompt_version,
        skill_version=request.skill_version,
        case_snapshot_hash=request.case_snapshot_hash,
        parse_snapshot_hash=request.parse_snapshot_hash,
        method_snapshot_hash=request.method_snapshot_hash,
        snapshot_json=snapshot_json,
        coverage_json="{}",
        planning_rounds_json="[]",
        allowed_evidence_ids_json="[]",
        tool_receipts_json="[]",
        evidence_cache_json="{}",
        status="CREATED",
        status_reason="",
        version=1,
        lease_owner=None,
        lease_expires_at=None,
        heartbeat_at=None,
        expires_at=current_time + timedelta(seconds=request.ttl_seconds),
        created_by=(created_by or "")[:128] or None,
        created_at=current_time,
        updated_at=current_time,
        last_activity_at=current_time,
        completed_at=None,
    )
    db.add(agent_run)
    db.flush()
    db.add(record)
    db.commit()
    db.refresh(record)
    return host_agent_session_to_view(record)


def get_host_agent_session(
    db: Session,
    session_id: str,
) -> HostAgentSessionView | None:
    record = db.get(HostAgentSession, session_id)
    return host_agent_session_to_view(record) if record else None


def require_host_agent_session(
    db: Session,
    session_id: str,
) -> HostAgentSessionView:
    return host_agent_session_to_view(_require_record(db, session_id))


def transition_host_agent_session(
    db: Session,
    session_id: str,
    request: HostAgentSessionStatusTransition,
    *,
    now: datetime | None = None,
) -> HostAgentSessionView:
    current_time = now or utcnow()
    record = _require_record(db, session_id)
    if record.version != request.expected_version:
        raise HostAgentSessionConflictError(
            "Host-agent session changed; refresh and retry"
        )
    if request.status == record.status:
        return host_agent_session_to_view(record)
    _ensure_mutable(
        record, now=current_time, allow_expired=request.status == "EXPIRED"
    )
    _ensure_lease(record, request.lease_owner, now=current_time)
    if request.status not in _ALLOWED_TRANSITIONS.get(record.status, frozenset()):
        raise HostAgentSessionTransitionError(
            f"Cannot transition host-agent session from {record.status} "
            f"to {request.status}"
        )
    terminal = request.status in TERMINAL_HOST_AGENT_SESSION_STATUSES
    allowed = json_loads(record.allowed_evidence_ids_json, [])
    return _cas_update(
        db,
        record,
        expected_version=request.expected_version,
        values={
            "status": request.status,
            "status_reason": request.reason,
            "completed_at": current_time if terminal else None,
            **({
                "lease_owner": None,
                "lease_expires_at": None,
            } if terminal else {}),
        },
        now=current_time,
        agent_run_values=_agent_status_values(
            request.status,
            reason=request.reason,
            now=current_time,
            allowed_evidence_ids=(allowed if isinstance(allowed, list) else []),
        ),
    )


def replace_host_agent_session_snapshot(
    db: Session,
    session_id: str,
    request: HostAgentSessionSnapshotUpdate,
    *,
    now: datetime | None = None,
) -> HostAgentSessionView:
    current_time = now or utcnow()
    record = _require_record(db, session_id)
    _ensure_mutable(record, now=current_time)
    _ensure_lease(record, request.lease_owner, now=current_time)
    receipts = json_loads(record.tool_receipts_json, [])
    if record.status != "CREATED" or receipts:
        raise HostAgentSessionTransitionError(
            "The pinned snapshot can only change before the session starts"
        )
    snapshot = _safe_json(request.snapshot)
    snapshot_json = _bounded_json(
        snapshot, max_chars=MAX_SNAPSHOT_JSON_CHARS, label="Host-agent snapshot"
    )
    input_summary = {
        "case_id": record.case_id,
        "executor": record.executor,
        "case_snapshot_hash": request.case_snapshot_hash,
        "parse_snapshot_hash": request.parse_snapshot_hash,
        "method_snapshot_hash": request.method_snapshot_hash,
        "skill_version": record.skill_version,
    }
    return _cas_update(
        db,
        record,
        expected_version=request.expected_version,
        values={
            "case_snapshot_hash": request.case_snapshot_hash,
            "parse_snapshot_hash": request.parse_snapshot_hash,
            "method_snapshot_hash": request.method_snapshot_hash,
            "snapshot_json": snapshot_json,
        },
        now=current_time,
        agent_run_values={
            "input_summary_hash": hash_host_agent_tool_arguments(input_summary),
        },
    )


def merge_host_agent_session_coverage(
    db: Session,
    session_id: str,
    request: HostAgentSessionCoverageUpdate,
    *,
    now: datetime | None = None,
) -> HostAgentSessionView:
    current_time = now or utcnow()
    record = _require_record(db, session_id)
    _ensure_mutable(record, now=current_time)
    _ensure_lease(record, request.lease_owner, now=current_time)
    current_coverage = json_loads(record.coverage_json, {})
    safe_patch = _safe_json(request.coverage_patch)
    merged_coverage = _deep_merge(
        current_coverage if isinstance(current_coverage, dict) else {},
        safe_patch if isinstance(safe_patch, dict) else {},
    )
    coverage_json = _bounded_json(
        merged_coverage,
        max_chars=MAX_COVERAGE_JSON_CHARS,
        label="Host-agent coverage",
    )
    current_ids = json_loads(record.allowed_evidence_ids_json, [])
    allowed_ids = _normalize_ids([
        *(current_ids if isinstance(current_ids, list) else []),
        *request.allowed_evidence_ids,
    ])
    return _cas_update(
        db,
        record,
        expected_version=request.expected_version,
        values={
            "coverage_json": coverage_json,
            "allowed_evidence_ids_json": json_dumps(allowed_ids),
        },
        now=current_time,
        agent_run_values={"evidence_ids_json": json_dumps(allowed_ids)},
    )


def merge_host_agent_evidence_cache(
    db: Session,
    session_id: str,
    request: HostAgentEvidenceCacheUpdate,
    *,
    now: datetime | None = None,
) -> HostAgentSessionView:
    current_time = now or utcnow()
    record = _require_record(db, session_id)
    _ensure_mutable(record, now=current_time)
    _ensure_lease(record, request.lease_owner, now=current_time)
    allowed = json_loads(record.allowed_evidence_ids_json, [])
    current_cache = json_loads(record.evidence_cache_json, {})
    merged = _merge_evidence_cache(
        current_cache if isinstance(current_cache, dict) else {},
        request.evidence,
        allowed_ids=set(allowed if isinstance(allowed, list) else []),
    )
    cache_json = _bounded_json(
        merged,
        max_chars=MAX_EVIDENCE_CACHE_JSON_CHARS,
        label="Host-agent evidence cache",
    )
    return _cas_update(
        db,
        record,
        expected_version=request.expected_version,
        values={"evidence_cache_json": cache_json},
        now=current_time,
    )


def record_host_agent_tool_receipt(
    db: Session,
    session_id: str,
    request: HostAgentToolReceiptInput,
    *,
    now: datetime | None = None,
) -> HostAgentSessionView:
    current_time = now or utcnow()
    record = _require_record(db, session_id)
    _ensure_mutable(record, now=current_time)
    _ensure_lease(record, request.lease_owner, now=current_time)
    receipts = json_loads(record.tool_receipts_json, [])
    if not isinstance(receipts, list):
        receipts = []
    evidence_ids = _normalize_ids(request.evidence_ids)
    receipt = HostAgentToolReceipt(
        call_id=request.call_id,
        tool_name=request.tool_name,
        arguments_hash=request.arguments_hash,
        evidence_ids=evidence_ids,
        recorded_at=current_time,
    )
    for existing in receipts:
        if not isinstance(existing, dict) or existing.get("call_id") != request.call_id:
            continue
        existing_receipt = HostAgentToolReceipt.model_validate(existing)
        if (
            existing_receipt.tool_name == receipt.tool_name
            and existing_receipt.arguments_hash == receipt.arguments_hash
            and existing_receipt.evidence_ids == receipt.evidence_ids
        ):
            return host_agent_session_to_view(record)
        raise HostAgentSessionConflictError(
            "Tool call_id was already recorded with a different receipt"
        )
    receipts.append(receipt.model_dump(mode="json"))
    receipts_json = _bounded_json(
        receipts,
        max_chars=MAX_TOOL_RECEIPTS_JSON_CHARS,
        label="Host-agent tool receipts",
    )
    current_ids = json_loads(record.allowed_evidence_ids_json, [])
    allowed_ids = _normalize_ids([
        *(current_ids if isinstance(current_ids, list) else []),
        *evidence_ids,
    ])
    extra_cache_ids = set(request.evidence).difference(evidence_ids)
    if extra_cache_ids:
        raise HostAgentSessionError(
            "Tool evidence cache contains IDs absent from its receipt: "
            + ", ".join(sorted(extra_cache_ids)[:10])
        )
    current_cache = json_loads(record.evidence_cache_json, {})
    merged_cache = _merge_evidence_cache(
        current_cache if isinstance(current_cache, dict) else {},
        request.evidence,
        allowed_ids=set(allowed_ids),
    )
    cache_json = _bounded_json(
        merged_cache,
        max_chars=MAX_EVIDENCE_CACHE_JSON_CHARS,
        label="Host-agent evidence cache",
    )
    return _cas_update(
        db,
        record,
        expected_version=request.expected_version,
        values={
            "tool_receipts_json": receipts_json,
            "allowed_evidence_ids_json": json_dumps(allowed_ids),
            "evidence_cache_json": cache_json,
        },
        now=current_time,
        agent_run_values={"evidence_ids_json": json_dumps(allowed_ids)},
    )


def acquire_host_agent_session_lease(
    db: Session,
    session_id: str,
    request: HostAgentSessionLeaseRequest,
    *,
    now: datetime | None = None,
) -> HostAgentSessionView:
    current_time = now or utcnow()
    record = _require_record(db, session_id)
    _ensure_mutable(record, now=current_time)
    if (
        record.lease_owner
        and record.lease_expires_at
        and _as_utc(record.lease_expires_at) > _as_utc(current_time)
        and record.lease_owner != request.owner
    ):
        raise HostAgentSessionLeaseConflictError(
            "Host-agent session is leased by another client"
        )
    lease_expires_at = min(
        _as_utc(current_time) + timedelta(seconds=request.lease_seconds),
        _as_utc(record.expires_at),
    )
    return _cas_update(
        db,
        record,
        expected_version=request.expected_version,
        values={
            "lease_owner": request.owner,
            "lease_expires_at": lease_expires_at,
            "heartbeat_at": current_time,
        },
        now=current_time,
    )


def renew_host_agent_session_lease(
    db: Session,
    session_id: str,
    request: HostAgentSessionLeaseRequest,
    *,
    now: datetime | None = None,
) -> HostAgentSessionView:
    current_time = now or utcnow()
    record = _require_record(db, session_id)
    _ensure_mutable(record, now=current_time)
    if record.lease_owner != request.owner:
        raise HostAgentSessionLeaseConflictError(
            "Only the current lease owner can renew this session"
        )
    if (
        not record.lease_expires_at
        or _as_utc(record.lease_expires_at) <= _as_utc(current_time)
    ):
        raise HostAgentSessionLeaseConflictError(
            "The host-agent session lease has expired"
        )
    lease_expires_at = min(
        _as_utc(current_time) + timedelta(seconds=request.lease_seconds),
        _as_utc(record.expires_at),
    )
    return _cas_update(
        db,
        record,
        expected_version=request.expected_version,
        values={
            "lease_expires_at": lease_expires_at,
            "heartbeat_at": current_time,
        },
        now=current_time,
    )


def release_host_agent_session_lease(
    db: Session,
    session_id: str,
    request: HostAgentSessionLeaseRelease,
    *,
    now: datetime | None = None,
) -> HostAgentSessionView:
    current_time = now or utcnow()
    record = _require_record(db, session_id)
    _ensure_mutable(record, now=current_time)
    if record.lease_owner != request.owner:
        raise HostAgentSessionLeaseConflictError(
            "Only the current lease owner can release this session"
        )
    return _cas_update(
        db,
        record,
        expected_version=request.expected_version,
        values={
            "lease_owner": None,
            "lease_expires_at": None,
            "heartbeat_at": current_time,
        },
        now=current_time,
    )


def cancel_host_agent_session(
    db: Session,
    session_id: str,
    request: HostAgentSessionCancel,
    *,
    now: datetime | None = None,
) -> HostAgentSessionView:
    return transition_host_agent_session(
        db,
        session_id,
        HostAgentSessionStatusTransition(
            expected_version=request.expected_version,
            status="CANCELLED",
            reason=request.reason,
            lease_owner=request.lease_owner,
        ),
        now=now,
    )


def expire_host_agent_session(
    db: Session,
    session_id: str,
    *,
    expected_version: int,
    now: datetime | None = None,
) -> HostAgentSessionView:
    current_time = now or utcnow()
    record = _require_record(db, session_id)
    if _as_utc(record.expires_at) > _as_utc(current_time):
        raise HostAgentSessionTransitionError(
            "Host-agent session has not reached its expiration time"
        )
    return transition_host_agent_session(
        db,
        session_id,
        HostAgentSessionStatusTransition(
            expected_version=expected_version,
            status="EXPIRED",
            reason="Host-agent session TTL expired",
            lease_owner=record.lease_owner,
        ),
        now=current_time,
    )


def expire_due_host_agent_sessions(
    db: Session,
    *,
    now: datetime | None = None,
    limit: int = 100,
) -> list[HostAgentSessionView]:
    current_time = now or utcnow()
    records = list(db.scalars(
        select(HostAgentSession)
        .where(
            HostAgentSession.status.notin_(TERMINAL_HOST_AGENT_SESSION_STATUSES),
            HostAgentSession.expires_at <= current_time,
        )
        .order_by(HostAgentSession.expires_at, HostAgentSession.id)
        .limit(max(1, min(int(limit), 1000)))
    ).all())
    expired: list[HostAgentSessionView] = []
    for record in records:
        try:
            expired.append(expire_host_agent_session(
                db,
                record.id,
                expected_version=record.version,
                now=current_time,
            ))
        except HostAgentSessionConflictError:
            continue
    return expired
