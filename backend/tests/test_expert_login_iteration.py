"""Admin-appointed experts retain existing LAN code and browser-ticket login."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.db import Base
from app.models import UserAccount
from app.services import simple_login


def test_promotion_preserves_personal_code_and_returns_current_role(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'expert-login.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(simple_login, "get_settings", lambda: SimpleNamespace(
        simple_engineer_login=True, deployment_mode="lan_server", auth_mode="rbac"))
    with sessionmaker(engine, expire_on_commit=False)() as db:
        first = simple_login.enroll_engineer(db, "e12345678")
        assert first["role"] == "ENGINEER"
        user = db.get(UserAccount, first["user_id"])
        user.role = "EXPERT"
        db.commit()
        promoted = simple_login.enroll_engineer(db, "e12345678")
        assert promoted["role"] == "EXPERT" and promoted["user_id"] == first["user_id"]
        ticket = simple_login.create_browser_ticket(db, {"id": user.id, "role": "ENGINEER"})
        assert simple_login.redeem_browser_ticket(db, ticket["ticket"])["role"] == "EXPERT"
        with pytest.raises(HTTPException):
            simple_login.redeem_browser_ticket(db, ticket["ticket"])
        assert len(list(db.scalars(select(UserAccount)))) == 1
        user.active = False
        db.commit()
        with pytest.raises(HTTPException):
            simple_login.enroll_engineer(db, "e12345678")
        user.active, user.role = True, "ADMIN"
        db.commit()
        with pytest.raises(HTTPException):
            simple_login.enroll_engineer(db, "e12345678")
    engine.dispose()
