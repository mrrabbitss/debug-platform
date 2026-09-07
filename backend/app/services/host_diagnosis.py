from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.utils import json_dumps, new_id, utcnow
from app.models import AnalysisRun, Case
from app.services.agent_trace_runtime import append_live_trace
from app.services.diagnostic_fault_tree_baseline import is_case_log_evidence
from app.services.diagnostic_planning_contract import (
    PlanningRound,
    normalize_planning_round,
    validate_planning_round,
)
from app.services.diagnostic_planning_coverage import (
    TERMINAL_FAULT_TREE_STATUSES,
    coverage_snapshot,
    initial_fault_tree_coverage,
)
from app.services.diagnosis_contract import validate_llm_diagnosis
from app.services.host_agent_session_contracts import (
    HostAgentPlanningRoundAppend,
    HostAgentSessionCoverageUpdate,
    HostAgentSessionCreate,
    HostAgentSessionStatusTransition,
)
from app.services.host_agent_planning_rounds import append_host_agent_planning_round
from app.services.host_agent_sessions import (
    HostAgentSessionConflictError,
    create_host_agent_session,
    merge_host_agent_session_coverage,
    require_host_agent_session,
    transition_host_agent_session,
)
from app.services.host_diagnostic_runtime import (
    HOST_DIAGNOSTIC_PROMPT_VERSION,
    HostDiagnosticSnapshot,
    host_diagnostic_context,
    load_host_diagnostic_snapshot,
    normalize_host_diagnostic_tool_arguments,
)


HOST_ANALYSIS_ENGINE = "host_cli_mcp"
MIN_HOST_DIAGNOSTIC_ROUNDS = 2
MAX_HOST_DIAGNOSTIC_ROUNDS = 20


class HostDiagnosisError(ValueError):
    pass


class HostDiagnosisSnapshotDriftError(HostDiagnosisError):
    pass


class HostDiagnosisInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1, max_length=40)
    expected_version: int = Field(ge=1)
    diagnosis: dict[str, Any]


def _snapshot_payload(snapshot: HostDiagnosticSnapshot) -> dict[str, Any]:
    return host_diagnostic_context(snapshot)


def begin_host_diagnosis(
    case_id: str,
    *,
    executor: str,
    client_model_claim: str,
    skill_version: str,
    created_by: str | None,
    ttl_seconds: int = 14_400,
    session_factory: Any = SessionLocal,
) -> dict[str, Any]:
    from app.services.knowledge_personal import personal_view
    with session_factory() as db:
        knowledge_view = personal_view(db, created_by)
        db.commit()
    snapshot = load_host_diagnostic_snapshot(case_id, session_factory=session_factory, knowledge_view=knowledge_view)
    context = _snapshot_payload(snapshot)
    initial_evidence = {
        str(item["evidence_id"]): item
        for item in snapshot.initial_evidence
        if item.get("evidence_id")
    }
    with session_factory() as db:
        view = create_host_agent_session(
            db,
            HostAgentSessionCreate(
                case_id=case_id,
                executor=executor,
                client_model_claim=client_model_claim or "unknown",
                prompt_version=HOST_DIAGNOSTIC_PROMPT_VERSION,
                skill_version=skill_version,
                case_snapshot_hash=snapshot.case_snapshot_hash,
                parse_snapshot_hash=snapshot.parse_snapshot_hash,
                method_snapshot_hash=snapshot.method_manifest_hash,
                snapshot={
                    "contract_version": context["contract_version"],
                    "case": context["case"],
                    "artifacts": context["artifacts"],
                    "parse_generations": context["parse_generations"],
                    "method_manifest": context["method_manifest"],
                    "personal_knowledge_revisions": knowledge_view,
                    "prompt_version": context["prompt_version"],
                },
                ttl_seconds=ttl_seconds,
            ),
            created_by=created_by,
        )
        view = merge_host_agent_session_coverage(
            db,
            view.id,
            HostAgentSessionCoverageUpdate(
                expected_version=view.version,
                coverage_patch=initial_fault_tree_coverage(snapshot.fault_tree_items),
                allowed_evidence_ids=list(initial_evidence),
            ),
        )
        if initial_evidence:
            # Initial evidence is returned by this tool itself. Reuse the receipt
            # cache service through an explicit coverage write followed by the
            # normal cache merge performed by the MCP registry.
            from app.services.host_agent_session_contracts import (
                HostAgentEvidenceCacheUpdate,
            )
            from app.services.host_agent_sessions import merge_host_agent_evidence_cache

            view = merge_host_agent_evidence_cache(
                db,
                view.id,
                HostAgentEvidenceCacheUpdate(
                    expected_version=view.version,
                    evidence=initial_evidence,
                ),
            )
        append_live_trace(
            db,
            view.agent_run_id,
            stage="host_diagnosis_started",
            tool_name="debug_begin_host_diagnosis",
            status="COMPLETED",
            input_summary={"case_id": case_id, "executor": executor},
            output_summary={
                "methods": len(snapshot.methods),
                "fault_tree_items": len(snapshot.fault_tree_items),
                "initial_evidence": len(initial_evidence),
            },
            evidence_ids=list(initial_evidence),
            metadata={
                "execution_mode": "host_cli",
                "backend_chat_allowed": False,
                "client_model_claim_verified": False,
            },
        )
    return {
        "run": view.model_dump(mode="json"),
        "context": context,
    }


def require_unchanged_host_snapshot(
    view: Any,
    *,
    session_factory: Any = SessionLocal,
) -> HostDiagnosticSnapshot:
    snapshot = load_host_diagnostic_snapshot(
        view.case_id,
        session_factory=session_factory,
        knowledge_view=view.snapshot.get("personal_knowledge_revisions", []),
    )
    mismatches: list[str] = []
    if snapshot.case_snapshot_hash != view.case_snapshot_hash:
        mismatches.append("case")
    if snapshot.parse_snapshot_hash != view.parse_snapshot_hash:
        mismatches.append("parse generation")
    if snapshot.method_manifest_hash != view.method_snapshot_hash:
        mismatches.append("diagnostic method")
    if mismatches:
        raise HostDiagnosisSnapshotDriftError(
            "Host-agent snapshot changed ("
            + ", ".join(mismatches)
            + "); start a new diagnosis run"
        )
    return snapshot


def _receipt_by_id(view: Any) -> dict[str, Any]:
    return {receipt.call_id: receipt for receipt in view.tool_receipts}


def submit_host_planning_round(
    session_id: str,
    *,
    expected_version: int,
    round_number: int,
    payload: Mapping[str, Any],
    session_factory: Any = SessionLocal,
) -> dict[str, Any]:
    if not 1 <= round_number <= MAX_HOST_DIAGNOSTIC_ROUNDS:
        raise HostDiagnosisError("Host diagnostic round is outside the 1..20 limit")
    with session_factory() as db:
        view = require_host_agent_session(db, session_id)
        if view.version != expected_version:
            raise HostAgentSessionConflictError(
                "Host-agent session changed; refresh and retry"
            )
        if view.status not in {"METHODS_READ", "SEARCHING", "REJECTED"}:
            raise HostDiagnosisError(
                f"Planning round is not allowed while session is {view.status}"
            )
        snapshot = require_unchanged_host_snapshot(
            view,
            session_factory=session_factory,
        )
        coverage = (
            dict(view.coverage)
            if view.coverage
            else initial_fault_tree_coverage(snapshot.fault_tree_items)
        )
        prior_round = view.planning_rounds[-1].round_number if view.planning_rounds else 0
        if round_number != prior_round + 1:
            raise HostDiagnosisError(
                f"Expected host diagnostic round {prior_round + 1}, got {round_number}"
            )

        normalized = normalize_planning_round(dict(payload))
        planning_round = PlanningRound.model_validate(normalized)
        receipts = _receipt_by_id(view)
        prior_call_ids = {
            str(call.get("call_id"))
            for prior in view.planning_rounds
            for call in prior.payload.get("tool_calls", [])
            if isinstance(call, dict) and call.get("call_id")
        }
        for planned_call in planning_round.tool_calls:
            if planned_call.call_id in prior_call_ids:
                raise HostDiagnosisError(
                    f"Planning tool call {planned_call.call_id} reuses an earlier receipt"
                )
            receipt = receipts.get(planned_call.call_id)
            if receipt is None:
                raise HostDiagnosisError(
                    f"Planning tool call {planned_call.call_id} has no server receipt"
                )
            expected_tool = f"debug_{planned_call.tool_name}"
            if receipt.tool_name not in {planned_call.tool_name, expected_tool}:
                raise HostDiagnosisError(
                    f"Planning receipt {planned_call.call_id} belongs to another tool"
                )
            arguments = dict(planned_call.arguments)
            if planned_call.method_document_ids:
                arguments.setdefault(
                    "method_document_ids",
                    planned_call.method_document_ids,
                )
            arguments = normalize_host_diagnostic_tool_arguments(
                planned_call.tool_name,
                arguments,
            )
            from app.services.host_agent_sessions import hash_host_agent_tool_arguments

            if receipt.arguments_hash != hash_host_agent_tool_arguments(arguments):
                raise HostDiagnosisError(
                    f"Planning receipt {planned_call.call_id} arguments changed"
                )

        unattempted = {
            item_id
            for item_id, item in coverage.items()
            if not item.get("attempted")
        }
        valid_ids = set(view.evidence_cache)
        validate_planning_round(
            planning_round,
            round_number=round_number,
            case=snapshot.case,
            methods=snapshot.methods,
            fault_tree_items=snapshot.fault_tree_items,
            diagnostic_patterns=snapshot.patterns,
            unattempted_fault_tree_item_ids=unattempted,
            valid_evidence_ids=valid_ids,
            valid_evidence_locator_ids=valid_ids,
        )
        for planned_call in planning_round.tool_calls:
            for item_id in planned_call.fault_tree_item_ids:
                if item_id in coverage:
                    coverage[item_id]["attempted"] = True
        for assessment in planning_round.fault_tree_assessments:
            current = coverage.get(assessment.item_id)
            if current is None:
                continue
            if (
                assessment.status == "PENDING"
                and current.get("status") in TERMINAL_FAULT_TREE_STATUSES
            ):
                continue
            current.update({
                "status": assessment.status,
                "rationale": assessment.rationale,
                "evidence_ids": list(dict.fromkeys(assessment.evidence_ids)),
                "next_action": assessment.next_action,
                "last_round": round_number,
            })
        round_summary = {
            "checks": len(planning_round.checks),
            "tool_receipts": [call.call_id for call in planning_round.tool_calls],
            "fault_tree_assessments": len(planning_round.fault_tree_assessments),
            "coverage": coverage_snapshot(coverage),
        }
        updated = append_host_agent_planning_round(
            db,
            session_id,
            HostAgentPlanningRoundAppend(
                expected_version=view.version,
                round_number=round_number,
                payload=planning_round.model_dump(mode="json"),
                summary=round_summary,
            ),
            coverage=coverage,
        )
        append_live_trace(
            db,
            updated.agent_run_id,
            stage=f"host_planning_round_{round_number}",
            tool_name="debug_submit_planning_round",
            status="COMPLETED",
            input_summary={
                "round": round_number,
                "tool_receipts": [call.call_id for call in planning_round.tool_calls],
            },
            output_summary=round_summary,
            evidence_ids=list({
                evidence_id
                for assessment in planning_round.fault_tree_assessments
                for evidence_id in assessment.evidence_ids
            }),
            metadata={
                "round": round_number,
                "executor": updated.executor,
                "client_model_claim_verified": False,
            },
        )
    return {
        "round": round_number,
        "accepted": True,
        "coverage": coverage_snapshot(coverage),
        "run": updated.model_dump(mode="json"),
    }


def _referenced_diagnosis_ids(result: Mapping[str, Any]) -> set[str]:
    ids: set[str] = set()
    for fact in result.get("confirmed_facts", []):
        ids.update(str(item) for item in fact.get("evidence_ids", []))
    for hypothesis in result.get("hypotheses", []):
        ids.update(str(item) for item in hypothesis.get("supporting_evidence", []))
        ids.update(str(item) for item in hypothesis.get("contradicting_evidence", []))
    for conclusion in result.get("fault_tree_conclusions", []):
        ids.update(str(item) for item in conclusion.get("evidence_ids", []))
    return ids


def _validate_case_evidence_grounding(
    result: Mapping[str, Any],
    evidence_cache: Mapping[str, Any],
) -> None:
    case_ids = {
        evidence_id
        for evidence_id, item in evidence_cache.items()
        if isinstance(item, dict) and is_case_log_evidence(item)
    }
    for fact in result.get("confirmed_facts", []):
        if not set(fact.get("evidence_ids", [])).issubset(case_ids):
            raise HostDiagnosisError(
                "Confirmed facts may cite only current-case log evidence"
            )
    for hypothesis in result.get("hypotheses", []):
        if not set(hypothesis.get("supporting_evidence", [])).intersection(case_ids):
            raise HostDiagnosisError(
                "Every root-cause hypothesis requires current-case log evidence"
            )
    for conclusion in result.get("fault_tree_conclusions", []):
        if (
            conclusion.get("status") in {"SUPPORTED", "EXCLUDED"}
            and not set(conclusion.get("evidence_ids", [])).issubset(case_ids)
        ):
            raise HostDiagnosisError(
                "Supported or excluded fault-tree conclusions require current-case evidence"
            )


def finalize_host_diagnosis(
    request: HostDiagnosisInput,
    *,
    session_factory: Any = SessionLocal,
    now: datetime | None = None,
) -> dict[str, Any]:
    current_time = now or utcnow()
    with session_factory() as db:
        view = require_host_agent_session(db, request.session_id)
        existing = db.scalar(select(AnalysisRun).where(
            AnalysisRun.agent_run_id == view.agent_run_id,
            AnalysisRun.provider == "host_cli",
            AnalysisRun.status == "COMPLETED",
        ))
        if existing and view.status == "COMPLETED":
            return {
                "analysis_id": existing.id,
                "status": existing.status,
                "run": view.model_dump(mode="json"),
                "idempotent_replay": True,
            }
        if view.version != request.expected_version:
            raise HostAgentSessionConflictError(
                "Host-agent session changed; refresh and retry"
            )
        if view.status not in {"SEARCHING", "DRAFT_SUBMITTED", "REJECTED"}:
            raise HostDiagnosisError(
                f"Diagnosis cannot be finalized while session is {view.status}"
            )
        snapshot = require_unchanged_host_snapshot(
            view,
            session_factory=session_factory,
        )
        coverage = coverage_snapshot(view.coverage)
        completed_rounds = len(view.planning_rounds)
        if snapshot.fault_tree_items and not coverage["complete"]:
            raise HostDiagnosisError("Fault-tree coverage is incomplete")
        if snapshot.methods and completed_rounds < MIN_HOST_DIAGNOSTIC_ROUNDS:
            raise HostDiagnosisError("Host diagnosis requires at least two planning rounds")
        required_items = {
            item_id: {
                "method_document_id": item.get("method_document_id"),
                "status": item.get("status"),
            }
            for item_id, item in view.coverage.items()
        }
        evidence_cache = {
            str(key): value
            for key, value in view.evidence_cache.items()
            if isinstance(value, dict)
        }
        validated = validate_llm_diagnosis(
            request.diagnosis,
            set(evidence_cache),
            required_items if snapshot.fault_tree_items else None,
        )
        missing_cached = _referenced_diagnosis_ids(validated).difference(evidence_cache)
        if missing_cached:
            raise HostDiagnosisError(
                "Diagnosis cited evidence without a persisted server observation"
            )
        _validate_case_evidence_grounding(validated, evidence_cache)

        if view.status != "DRAFT_SUBMITTED":
            view = transition_host_agent_session(
                db,
                view.id,
                HostAgentSessionStatusTransition(
                    expected_version=view.version,
                    status="DRAFT_SUBMITTED",
                    reason="Host CLI submitted an evidence-grounded diagnosis draft",
                ),
                now=current_time,
            )
        view = transition_host_agent_session(
            db,
            view.id,
            HostAgentSessionStatusTransition(
                expected_version=view.version,
                status="VALIDATED",
                reason="Server schema, receipt, evidence and fault-tree gates passed",
            ),
            now=current_time,
        )

        result = {
            **validated,
            "analysis_engine": HOST_ANALYSIS_ENGINE,
            "diagnostic_planning": {
                "execution_mode": "host_cli",
                "rounds_completed": completed_rounds,
                "fault_tree_coverage": coverage,
                "backend_chat_calls": 0,
                "client_model_claim": view.client_model_claim,
                "client_model_claim_verified": False,
            },
            "synthesis_status": {
                "accepted": True,
                "mode": "HOST_CLI_EVIDENCE_VALIDATED",
                "failure": None,
                "finish_reason": "HOST_AGENT_COMPLETED",
            },
        }
        analysis = AnalysisRun(
            id=new_id("RUN"),
            case_id=view.case_id,
            status="COMPLETED",
            provider="host_cli",
            model=view.client_model_claim,
            model_profile_id=None,
            agent_run_id=view.agent_run_id,
            model_config_json=json_dumps({
                "execution_mode": "host_cli_mcp",
                "executor": view.executor,
                "skill_version": view.skill_version,
                "client_model_claim_verified": False,
                "backend_chat_calls": 0,
            }),
            prompt_version=view.prompt_version,
            result_json=json_dumps(result),
            evidence_json=json_dumps(list(evidence_cache.values())),
            error_message=None,
            created_at=view.created_at,
            completed_at=current_time,
        )
        case = db.get(Case, view.case_id)
        if not case:
            raise HostDiagnosisError("Case was removed during host diagnosis")
        db.add(analysis)
        case.status = "ANALYZED"
        db.commit()
        append_live_trace(
            db,
            view.agent_run_id,
            stage="host_diagnosis_finalized",
            tool_name="debug_finalize_diagnosis",
            status="COMPLETED",
            output_summary={
                "analysis_id": analysis.id,
                "evidence": len(evidence_cache),
                "fault_tree": coverage,
            },
            evidence_ids=sorted(_referenced_diagnosis_ids(validated)),
            stop_reason="HOST_AGENT_COMPLETED",
            metadata={
                "execution_mode": "host_cli",
                "backend_chat_calls": 0,
                "client_model_claim_verified": False,
            },
        )
        # append_live_trace keeps an in-flight run in RUNNING. Perform the
        # terminal session transition afterwards so the linked AgentRun ends
        # atomically in the same COMPLETED state.
        view = transition_host_agent_session(
            db,
            view.id,
            HostAgentSessionStatusTransition(
                expected_version=view.version,
                status="COMPLETED",
                reason=f"Persisted host analysis {analysis.id}",
            ),
            now=current_time,
        )
    return {
        "analysis_id": analysis.id,
        "status": analysis.status,
        "run": view.model_dump(mode="json"),
        "idempotent_replay": False,
    }
