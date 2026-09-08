import hashlib
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import verify_api_key
from app.services.simple_login import (
    create_browser_ticket, enroll_engineer, redeem_browser_ticket, require_enabled,
)

router = APIRouter(tags=["engineer-login"])
Db = Annotated[Session, Depends(get_db)]


class EngineerLogin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    personal_code: str = Field(pattern=r"^[a-z][0-9]{8}$", min_length=9, max_length=9)


class BrowserLogin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticket: str = Field(min_length=20, max_length=128)


def private_response(payload: dict) -> JSONResponse:
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


@router.post("/auth/engineer-login")
def engineer_login(payload: EngineerLogin, db: Db):
    return private_response(enroll_engineer(db, payload.personal_code))


@router.post("/auth/browser-ticket", dependencies=[Depends(verify_api_key)])
def browser_ticket(request: Request, db: Db):
    return private_response(create_browser_ticket(db, request.state.principal))


@router.post("/auth/browser-login")
def browser_login(payload: BrowserLogin, db: Db):
    return private_response(redeem_browser_ticket(db, payload.ticket))


@router.get("/auth/server-certificate")
def server_certificate():
    require_enabled()
    settings = get_settings()
    path = settings.simple_login_ca_file
    if path is None or not path.is_file():
        raise HTTPException(503, "服务器证书尚未就绪，请稍后重试")
    pem = path.read_bytes()
    if b"PRIVATE KEY" in pem or pem.count(b"BEGIN CERTIFICATE") != 1:
        raise HTTPException(503, "服务器公共证书配置无效")
    return private_response({"app": settings.app_name, "server_id": settings.server_instance_id,
                             "certificate": pem.decode("ascii"), "sha256": hashlib.sha256(pem).hexdigest()})
