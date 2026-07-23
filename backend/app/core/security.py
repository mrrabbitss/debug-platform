import secrets

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.services.access_control import authenticate_access_token, authorize_request


async def verify_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> None:
    settings = get_settings()
    path = request.url.path
    if path.endswith("/health") or "/health/" in path or path.endswith("/system/auth-info"):
        request.state.principal = {"id": "health-check", "type": "anonymous", "role": "VIEWER"}
        return

    configured = settings.api_key or ""
    supplied = x_api_key or ""
    legacy_matches = bool(configured) and secrets.compare_digest(supplied, configured)
    principal: dict[str, str] | None = None
    if settings.auth_mode == "local":
        if configured and not legacy_matches:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
        principal = {
            "id": "legacy-api-key" if configured else "local-development",
            "type": "api_key" if configured else "local",
            "role": "ADMIN",
        }
    elif settings.auth_mode == "api_key":
        if not legacy_matches:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
        principal = {"id": "legacy-api-key", "type": "api_key", "role": "ADMIN"}
    elif legacy_matches and settings.auth_allow_legacy_admin:
        principal = {"id": "legacy-api-key", "type": "api_key", "role": "ADMIN"}
    elif supplied:
        principal = authenticate_access_token(db, supplied)
    if not principal:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Valid access token required")
    request.state.principal = principal
    authorize_request(db, request, principal)
