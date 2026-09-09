"""Pinned, review-only candidate patch generation for one case symbol."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.core.utils import json_dumps, json_loads, mask_sensitive
from app.models import AnalysisRun, Case, CodeSymbol, Repository
from app.services.access_control import authorize_case_action
from app.services.job_progress import report_progress
from app.services.llm import get_llm_provider
from app.services.model_access import (
    ModelAccessError,
    chat_model_snapshot,
    principal_for_model_user,
    resolve_chat_model_snapshot,
    resolve_user_chat_profile,
)


@dataclass(frozen=True)
class PatchSuggestionContext:
    case: Case
    repository: Repository
    symbol: CodeSymbol
    diagnosis: dict[str, Any]
    actor: dict[str, str]
    snapshot: dict[str, str]


def _model_error(error: ModelAccessError) -> HTTPException:
    return HTTPException(error.status_code, str(error))


def _find_symbol(
    db: Session,
    case_id: str,
    symbol_id: str,
    generation_id: str | None = None,
) -> tuple[Repository, CodeSymbol]:
    row = db.execute(
        select(Repository, CodeSymbol)
        .join(CodeSymbol, CodeSymbol.repository_id == Repository.id)
        .where(
            Repository.case_id == case_id,
            CodeSymbol.generation_id == Repository.active_graph_generation_id,
            or_(CodeSymbol.logical_id == symbol_id, CodeSymbol.id == symbol_id),
        )
        .limit(1)
    ).first()
    if not row:
        raise HTTPException(404, "Case or symbol not found")
    repository, symbol = row
    if generation_id is not None and (
        generation_id != repository.active_graph_generation_id
        or generation_id != symbol.generation_id
    ):
        raise HTTPException(409, "Source code generation changed; start a fresh patch request")
    return repository, symbol


def _require_model_egress(case: Case, profile) -> None:
    if profile.provider != "mock" and not case.model_egress_approved:
        raise HTTPException(409, "Model egress approval was revoked for this case")


def create_patch_suggestion_input(
    db: Session,
    *,
    case_id: str,
    symbol_id: str,
    instruction: str,
    principal: dict[str, str],
) -> dict[str, Any]:
    """Authorize a fresh request and persist only identifiers plus safe pinning."""
    case = authorize_case_action(db, case_id, principal, write=True)
    repository, symbol = _find_symbol(db, case_id, symbol_id)
    try:
        profile = resolve_user_chat_profile(db, principal)
        snapshot = chat_model_snapshot(db, principal, profile)
    except ModelAccessError as error:
        raise _model_error(error) from error
    _require_model_egress(case, profile)
    return {
        "case_id": case_id,
        "actor": principal["id"],
        "snapshot": snapshot,
        "symbol_id": symbol.logical_id or symbol.id,
        "generation_id": repository.active_graph_generation_id,
        # A job payload is durable. Do not retain a credential accidentally
        # pasted into the optional instruction.
        "instruction": mask_sensitive(instruction),
    }


def resolve_patch_suggestion_context(
    db: Session,
    *,
    case_id: str,
    actor_id: str,
    snapshot: dict[str, str],
    symbol_id: str,
    generation_id: str,
) -> PatchSuggestionContext:
    """Recheck all mutable authorization and pinning before every publish."""
    # The worker keeps one transaction open while awaiting the model. Discard
    # identity-map values so this final check observes a revoked consent,
    # membership, profile or active generation committed by another request.
    db.expire_all()
    try:
        actor = principal_for_model_user(db, actor_id)
        profile = resolve_chat_model_snapshot(db, snapshot)
    except ModelAccessError as error:
        raise _model_error(error) from error
    case = authorize_case_action(db, case_id, actor, write=True)
    repository, symbol = _find_symbol(db, case_id, symbol_id, generation_id)
    _require_model_egress(case, profile)
    latest = db.scalars(
        select(AnalysisRun)
        .where(AnalysisRun.case_id == case_id, AnalysisRun.status == "COMPLETED")
        .order_by(AnalysisRun.created_at.desc())
        .limit(1)
    ).first()
    diagnosis = json_loads(latest.result_json, {}) if latest else {}
    return PatchSuggestionContext(
        case=case,
        repository=repository,
        symbol=symbol,
        diagnosis=diagnosis if isinstance(diagnosis, dict) else {},
        actor=actor,
        snapshot=snapshot,
    )


def _masked_prompt(context: PatchSuggestionContext, instruction: str) -> str:
    # Serialize first so nested diagnosis fields receive the same redaction as
    # source and free-form user input, then deserialize to retain JSON structure.
    diagnosis = json_loads(mask_sensitive(json_dumps(context.diagnosis)), {})
    prompt = {
        "instruction": mask_sensitive(instruction),
        "case": {
            "title": mask_sensitive(context.case.title),
            "description": mask_sensitive(context.case.description),
            "device": mask_sensitive(context.case.device_type),
        },
        "diagnosis": diagnosis if isinstance(diagnosis, dict) else {},
        "symbol": {
            "file_path": mask_sensitive(context.symbol.file_path),
            "line_start": context.symbol.line_start,
            "line_end": context.symbol.line_end,
            "code": mask_sensitive(context.symbol.code),
        },
        "output": "只输出 unified diff；不得修改无关文件；不得调用不存在的 API；无法安全修复时说明 NEED_HUMAN_REVIEW。",
    }
    return json_dumps(prompt)


async def generate_patch_suggestion(
    db: Session,
    context: PatchSuggestionContext,
    instruction: str,
) -> dict[str, Any]:
    """Generate text only. Candidate patches are never applied to source files."""
    case_id, actor_id = context.case.id, context.actor["id"]
    symbol_id = context.symbol.logical_id or context.symbol.id
    generation_id = context.repository.active_graph_generation_id
    result = await _generate_with_resolved_profile(db, context, instruction)
    # The legacy synchronous endpoint must respect revocation during a slow
    # request too, before returning a candidate to its caller.
    resolve_patch_suggestion_context(db, case_id=case_id, actor_id=actor_id, snapshot=context.snapshot,
                                    symbol_id=symbol_id, generation_id=generation_id)
    return result


def patch_suggestion_job(
    ctx,
    case_id: str,
    actor: str,
    snapshot: dict[str, str],
    symbol_id: str,
    generation_id: str,
    instruction: str,
) -> dict[str, Any]:
    """Generate one candidate and atomically publish only after final checks."""
    report_progress(ctx, 15, "准备候选补丁请求", stage="准备", stage_index=1, stage_count=3)
    with SessionLocal() as db:
        ctx.raise_if_cancelled()
        context = resolve_patch_suggestion_context(
            db, case_id=case_id, actor_id=actor, snapshot=snapshot,
            symbol_id=symbol_id, generation_id=generation_id,
        )
        report_progress(ctx, 40, "正在生成候选补丁", stage="生成", stage_index=2, stage_count=3)
        result = asyncio.run(generate_patch_suggestion(db, context, instruction))
        ctx.raise_if_cancelled()
        # A model credential/role, case consent, membership or active code
        # generation may change while the provider request is in flight.
        resolve_patch_suggestion_context(
            db, case_id=case_id, actor_id=actor, snapshot=snapshot,
            symbol_id=symbol_id, generation_id=generation_id,
        )
        ctx.raise_if_cancelled()
        report_progress(ctx, 90, "保存候选补丁结果", stage="保存", stage_index=3, stage_count=3)
        ctx.complete_in_transaction(db, result, "Candidate patch suggestion saved")
        db.commit()
    return result


async def _generate_with_resolved_profile(
    db: Session,
    context: PatchSuggestionContext,
    instruction: str,
) -> dict[str, Any]:
    try:
        profile = resolve_chat_model_snapshot(db, context.snapshot)
    except ModelAccessError as error:
        raise _model_error(error) from error
    _require_model_egress(context.case, profile)
    provider = get_llm_provider(profile)
    if provider.is_mock:
        return {
            "status": "NEED_LLM_CONFIGURATION",
            "message": "配置 Qwen/GLM API 后可生成候选 unified diff。当前仅返回人工审查模板。",
            "symbol": {"file": context.symbol.file_path, "name": context.symbol.name,
                       "line_start": context.symbol.line_start},
            "review_checklist": ["确认日志证据与该函数存在数据流或调用关系", "采用最小修改",
                                 "重新编译并运行相关测试", "不得直接覆盖原文件"],
        }
    text = await provider.generate_text(
        "你是 C/C++ 网络设备代码审查工程师，生成最小、可审查、未自动应用的候选补丁。",
        _masked_prompt(context, instruction), purpose="patch_suggestion",
    )
    return {"status": "SUGGESTED", "patch": text, "auto_applied": False}
