import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException, Request, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.utils import json_loads, new_id, utcnow
from app.models import (
    AccessToken,
    AnalysisRun,
    Artifact,
    Case,
    CaseMember,
    Job,
    Report,
    Repository,
    UserAccount,
)


VALID_ROLES = {"ADMIN", "ENGINEER", "VIEWER"}
VALID_CASE_PERMISSIONS = {"EDITOR", "VIEWER"}
ADMIN_ONLY_PREFIXES = ("/system/audit", "/system/status", "/system/users")
ENGINEER_READ_PREFIXES = ("/system/models", "/system/retrieval", "/system/user-directory")
CASE_SCOPED_RESOURCES = {"cases", "artifacts", "analyses", "reports", "repositories", "jobs"}


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def token_digest(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def issue_access_token(
    db: Session,
    user: UserAccount,
    *,
    name: str = "default",
    expires_days: int | None = 90,
) -> tuple[AccessToken, str]:
    if not user.active:
        raise ValueError("Cannot issue a token for an inactive user")
    raw_token = f"gwdp_{secrets.token_urlsafe(32)}"
    token = AccessToken(
        id=new_id("TOK"),
        user_id=user.id,
        name=name[:255] or "default",
        token_hash=token_digest(raw_token),
        token_hint=f"gwdp_...{raw_token[-6:]}",
        expires_at=utcnow() + timedelta(days=expires_days) if expires_days else None,
    )
    db.add(token)
    db.commit()
    db.refresh(token)
    return token, raw_token


def authenticate_access_token(db: Session, raw_token: str) -> dict[str, str] | None:
    digest = token_digest(raw_token)
    row = db.execute(
        select(AccessToken, UserAccount)
        .join(UserAccount, UserAccount.id == AccessToken.user_id)
        .where(
            AccessToken.token_hash == digest,
            AccessToken.revoked_at.is_(None),
            UserAccount.active.is_(True),
        )
    ).first()
    if not row:
        return None
    token, user = row
    if not secrets.compare_digest(token.token_hash, digest):
        return None
    if token.expires_at and _as_utc(token.expires_at) <= utcnow():
        return None
    if not token.last_used_at or _as_utc(token.last_used_at) < utcnow() - timedelta(minutes=5):
        token.last_used_at = utcnow()
        db.commit()
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
        "type": "user_token",
        "token_id": token.id,
    }


def _api_parts(path: str) -> list[str]:
    parts = [part for part in path.split("/") if part]
    if "v1" in parts:
        return parts[parts.index("v1") + 1:]
    return parts


def resolve_request_case_id(db: Session, request: Request) -> str | None:
    parts = _api_parts(request.url.path)
    if len(parts) < 2:
        return None
    resource, resource_id = parts[0], parts[1]
    if resource == "cases":
        return resource_id
    if resource == "artifacts":
        artifact = db.get(Artifact, resource_id)
        return artifact.case_id if artifact else None
    if resource == "analyses":
        analysis = db.get(AnalysisRun, resource_id)
        return analysis.case_id if analysis else None
    if resource == "reports":
        report = db.get(Report, resource_id)
        return report.case_id if report else None
    if resource == "repositories":
        repository = db.get(Repository, resource_id)
        return repository.case_id if repository else None
    if resource == "jobs":
        job = db.get(Job, resource_id)
        data: dict[str, Any] = json_loads(job.input_json, {}) if job else {}
        if data.get("case_id"):
            return str(data["case_id"])
        if data.get("artifact_id"):
            artifact = db.get(Artifact, str(data["artifact_id"]))
            return artifact.case_id if artifact else None
        if data.get("repository_id"):
            repository = db.get(Repository, str(data["repository_id"]))
            return repository.case_id if repository else None
    return None


def case_permission(db: Session, case_id: str, principal: dict[str, str]) -> str | None:
    case = db.get(Case, case_id)
    if not case:
        return None
    if principal.get("role") == "ADMIN":
        return "OWNER"
    user_id = principal.get("id")
    if case.owner_id and case.owner_id == user_id:
        return "OWNER"
    if case.owner_id is None:
        return "SHARED"
    membership = db.scalar(select(CaseMember).where(
        CaseMember.case_id == case_id,
        CaseMember.user_id == user_id,
    ))
    return membership.permission if membership else None


def accessible_case_clause(user_id: str):
    membership_case_ids = select(CaseMember.case_id).where(CaseMember.user_id == user_id)
    return or_(
        Case.owner_id.is_(None),
        Case.owner_id == user_id,
        Case.id.in_(membership_case_ids),
    )


def authorize_request(db: Session, request: Request, principal: dict[str, str]) -> None:
    role = principal.get("role", "VIEWER")
    if role == "ADMIN":
        return
    path = request.url.path
    method = request.method.upper()
    if any(path.endswith(prefix) or f"{prefix}/" in path for prefix in ADMIN_ONLY_PREFIXES):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrator role required")
    if role == "VIEWER" and any(
        path.endswith(prefix) or f"{prefix}/" in path
        for prefix in ENGINEER_READ_PREFIXES
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Engineer or administrator role required")
    if "/knowledge" in path and method != "GET":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only administrators may modify knowledge")
    if "/system/models" in path and method != "GET":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only administrators may modify model profiles")
    if "/system/model/test" in path or "/knowledge/reindex" in path:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrator role required")
    if role == "VIEWER" and method != "GET":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Viewer role is read-only")

    parts = _api_parts(path)
    resource = parts[0] if parts else None
    case_id = resolve_request_case_id(db, request)
    if resource in CASE_SCOPED_RESOURCES and len(parts) >= 2 and not case_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Case-scoped resource not found")
    if not case_id:
        return
    permission = case_permission(db, case_id, principal)
    if not permission:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No access to this case")
    if method != "GET" and permission == "VIEWER":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Case permission is read-only")
    if method == "DELETE" and resource == "cases" and permission != "OWNER":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the case owner may delete it")
