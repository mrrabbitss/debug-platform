"""Self-declared engineer identities for explicitly enabled trusted LAN deployments."""
from datetime import timedelta
import re

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.utils import new_id, utcnow
from app.models import AccessToken, UserAccount
from app.services.access_control import issue_access_token, token_digest

HANDOFF_NAME = "browser-handoff"


def require_enabled() -> None:
    settings = get_settings()
    if not (settings.simple_engineer_login and settings.deployment_mode == "lan_server"
            and settings.auth_mode == "rbac"):
        raise HTTPException(404, "简易登录未启用")


def engineer_credentials(db: Session, user: UserAccount) -> dict:
    if not user.active or user.role not in {"ENGINEER", "EXPERT"}:
        raise HTTPException(403, "此识别码不能用于工程师自助登录，请联系管理员")
    _, raw = issue_access_token(db, user, name="trusted-lan-device", expires_days=None)
    return {"token": raw, "user_id": user.id, "personal_code": user.username, "role": user.role}


def enroll_engineer(db: Session, code: str) -> dict:
    require_enabled()
    if not re.fullmatch(r"[a-z][0-9]{8}", code, flags=re.ASCII):
        raise HTTPException(422, "识别码须为一位小写字母加八位数字")
    user = db.scalar(select(UserAccount).where(UserAccount.username == code))
    if user is None:
        user = UserAccount(id=new_id("USR"), username=code, display_name=code, role="ENGINEER")
        db.add(user)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            user = db.scalar(select(UserAccount).where(UserAccount.username == code))
            if user is None:
                raise
    return engineer_credentials(db, user)


def create_browser_ticket(db: Session, principal: dict) -> dict:
    require_enabled()
    user = db.get(UserAccount, principal.get("id"))
    if user is None or not user.active or user.role not in {"ENGINEER", "EXPERT"}:
        raise HTTPException(403, "仅工程师或专家账号可使用分机网页登录")
    ticket, raw = issue_access_token(db, user, name=HANDOFF_NAME, expires_days=None)
    ticket.expires_at = utcnow() + timedelta(seconds=60)
    db.commit()
    return {"ticket": raw, "expires_in": 60}


def redeem_browser_ticket(db: Session, raw: str) -> dict:
    require_enabled()
    # Consume exactly once, including when two browsers redeem concurrently.
    user_id = db.scalar(update(AccessToken).where(
        AccessToken.token_hash == token_digest(raw), AccessToken.name == HANDOFF_NAME,
        AccessToken.revoked_at.is_(None), AccessToken.expires_at > utcnow(),
    ).values(revoked_at=utcnow()).returning(AccessToken.user_id))
    db.commit()
    user = db.get(UserAccount, user_id) if user_id else None
    if user is None:
        raise HTTPException(401, "网页登录已过期，请重新打开平台")
    return engineer_credentials(db, user)
