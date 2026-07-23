from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from app.core.db import Base
from app.models import AccessToken, Case, CaseMember, UserAccount
from app.services.access_control import (
    authenticate_access_token,
    authorize_request,
    issue_access_token,
)


def _request(method: str, path: str) -> Request:
    return Request({
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8000),
    })


def _database(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'rbac.db'}")
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    return engine, session_factory


def test_access_tokens_are_hashed_and_authenticate_active_user(tmp_path: Path) -> None:
    engine, session_factory = _database(tmp_path)
    with session_factory() as db:
        user = UserAccount(
            id="USR-engineer",
            username="engineer",
            display_name="Engineer",
            role="ENGINEER",
        )
        db.add(user)
        db.commit()
        token, raw_token = issue_access_token(db, user, expires_days=30)

        assert raw_token.startswith("gwdp_")
        assert token.token_hint == f"gwdp_...{raw_token[-6:]}"
        assert token.token_hint.isascii()
        assert raw_token not in token.token_hash
        assert authenticate_access_token(db, raw_token)["id"] == user.id
        assert authenticate_access_token(db, "gwdp_invalid") is None
        persisted = db.scalar(select(AccessToken).where(AccessToken.id == token.id))
        assert persisted is not None
        assert raw_token not in persisted.token_hash
    engine.dispose()


def test_case_membership_enforces_read_only_and_isolation(tmp_path: Path) -> None:
    engine, session_factory = _database(tmp_path)
    with session_factory() as db:
        owner = UserAccount(id="USR-owner", username="owner", display_name="Owner", role="ENGINEER")
        viewer = UserAccount(id="USR-viewer", username="viewer", display_name="Viewer", role="ENGINEER")
        outsider = UserAccount(id="USR-outsider", username="outsider", display_name="Outsider", role="ENGINEER")
        db.add_all([owner, viewer, outsider])
        db.flush()
        db.add(Case(id="CASE-private", title="private", description="", owner_id=owner.id))
        db.add(Case(id="CASE-legacy", title="legacy", description="", owner_id=None))
        db.flush()
        db.add(CaseMember(
            id="MEM-viewer",
            case_id="CASE-private",
            user_id=viewer.id,
            permission="VIEWER",
        ))
        db.commit()

        viewer_principal = {"id": viewer.id, "role": "ENGINEER", "type": "user_token"}
        authorize_request(db, _request("GET", "/api/v1/cases/CASE-private"), viewer_principal)
        with pytest.raises(HTTPException) as read_only:
            authorize_request(db, _request("PATCH", "/api/v1/cases/CASE-private"), viewer_principal)
        assert read_only.value.status_code == 403

        outsider_principal = {"id": outsider.id, "role": "ENGINEER", "type": "user_token"}
        with pytest.raises(HTTPException) as forbidden:
            authorize_request(db, _request("GET", "/api/v1/cases/CASE-private"), outsider_principal)
        assert forbidden.value.status_code == 403
        authorize_request(db, _request("GET", "/api/v1/cases/CASE-legacy"), outsider_principal)
    engine.dispose()


def test_viewer_role_is_globally_read_only(tmp_path: Path) -> None:
    engine, session_factory = _database(tmp_path)
    with session_factory() as db:
        principal = {"id": "USR-viewer", "role": "VIEWER", "type": "user_token"}
        with pytest.raises(HTTPException) as forbidden:
            authorize_request(db, _request("POST", "/api/v1/cases"), principal)
        assert forbidden.value.status_code == 403
    engine.dispose()
