import logging
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.db import SessionLocal
from app.core.utils import json_dumps, new_id
from app.models import AuditEvent, ModelProfile


logger = logging.getLogger(__name__)
_SENSITIVE_KEY_PARTS = (
    "api_key",
    "authorization",
    "cipher",
    "credential",
    "password",
    "secret",
    "token",
)
_SENSITIVE_READ_PATH_PARTS = (
    "/content",
    "/download",
    "/members",
    "/system/audit",
    "/system/users",
)


def _sanitize(value: Any, depth: int = 0) -> Any:
    if depth > 5:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in list(value.items())[:100]:
            lowered = str(key).lower()
            sanitized[str(key)[:128]] = (
                "[REDACTED]"
                if any(part in lowered for part in _SENSITIVE_KEY_PARTS)
                else _sanitize(item, depth + 1)
            )
        return sanitized
    if isinstance(value, (list, tuple)):
        return [_sanitize(item, depth + 1) for item in list(value)[:100]]
    if isinstance(value, str):
        return value[:2000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:2000]


def record_audit_event(
    action: str,
    *,
    actor_id: str | None = None,
    actor_type: str = "system",
    resource_type: str | None = None,
    resource_id: str | None = None,
    case_id: str | None = None,
    outcome: str = "SUCCESS",
    ip_address: str | None = None,
    user_agent: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    try:
        with SessionLocal() as db:
            db.add(AuditEvent(
                id=new_id("AUD"),
                actor_id=actor_id[:128] if actor_id else None,
                actor_type=actor_type[:32],
                action=action[:128],
                resource_type=resource_type[:64] if resource_type else None,
                resource_id=resource_id[:128] if resource_id else None,
                case_id=case_id[:40] if case_id else None,
                outcome=outcome[:32],
                ip_address=ip_address[:128] if ip_address else None,
                user_agent=user_agent[:512] if user_agent else None,
                details_json=json_dumps(_sanitize(details or {})),
            ))
            db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("Unable to persist audit event %s", action)


def model_endpoint_origin(base_url: str | None) -> str | None:
    if not base_url:
        return None
    parsed = urlsplit(base_url)
    if not parsed.scheme or not parsed.hostname:
        return None
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme.lower()}://{parsed.hostname.lower()}{port}"


def record_model_egress(
    profile: ModelProfile | None,
    *,
    base_url: str | None = None,
    model_name: str | None = None,
    task_type: str,
    purpose: str,
    request_items: int,
    request_chars: int,
    duration_ms: int,
    outcome: str,
    error_type: str | None = None,
    usage: dict[str, int | None] | None = None,
) -> None:
    record_audit_event(
        "model.egress",
        actor_id="background-worker",
        actor_type="system",
        resource_type="model_profile",
        resource_id=profile.id if profile else "environment",
        outcome=outcome,
        details={
            "task_type": task_type,
            "purpose": purpose,
            "provider": profile.provider if profile else "openai_compatible",
            "model": profile.model_name if profile else model_name,
            "destination_origin": model_endpoint_origin(profile.base_url if profile else base_url),
            "request_items": request_items,
            "request_chars": request_chars,
            "duration_ms": duration_ms,
            "error_type": error_type,
            "usage": usage or {},
            "content_recorded": False,
        },
    )


def _should_audit_request(request: Request) -> bool:
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        return True
    return request.method == "GET" and any(part in request.url.path for part in _SENSITIVE_READ_PATH_PARTS)


def _resource_from_path(path: str) -> tuple[str | None, str | None, str | None]:
    parts = [part for part in path.split("/") if part]
    if "v1" in parts:
        parts = parts[parts.index("v1") + 1:]
    resource_type = parts[0] if parts else None
    resource_id = parts[1] if len(parts) > 1 and not parts[1].isdigit() else None
    case_id = resource_id if resource_type == "cases" else None
    return resource_type, resource_id, case_id


class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        started = perf_counter()
        response = None
        try:
            response = await call_next(request)
            return response
        finally:
            if _should_audit_request(request):
                principal = getattr(request.state, "principal", None) or {}
                route = request.scope.get("route")
                route_name = getattr(route, "name", None)
                status_code = response.status_code if response is not None else 500
                resource_type, resource_id, case_id = _resource_from_path(request.url.path)
                record_audit_event(
                    f"http.{route_name or request.method.lower()}",
                    actor_id=principal.get("id") or "anonymous",
                    actor_type=principal.get("type") or "anonymous",
                    resource_type=resource_type,
                    resource_id=resource_id,
                    case_id=case_id,
                    outcome="SUCCESS" if status_code < 400 else "DENIED" if status_code in {401, 403} else "FAILED",
                    ip_address=request.client.host if request.client else None,
                    user_agent=request.headers.get("user-agent"),
                    details={
                        "method": request.method,
                        "path": request.url.path,
                        "query_parameter_names": sorted(request.query_params.keys()),
                        "status_code": status_code,
                        "duration_ms": int((perf_counter() - started) * 1000),
                        "body_recorded": False,
                    },
                )
