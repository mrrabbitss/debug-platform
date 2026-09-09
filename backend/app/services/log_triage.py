from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from time import perf_counter
from typing import Any

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.utils import json_dumps, json_loads, new_id, utcnow
from app.diagnostic_models import LogTriageRun
from app.models import Artifact, Case, LogEvent
from app.services.agent_trace_runtime import (
    append_live_trace,
    create_live_agent_run,
    finish_live_agent_run,
)
from app.services.agentic.tools import ToolContext
from app.services.diagnostic_methods import (
    DiagnosticMethodDocument,
    DiagnosticPattern,
    compile_diagnostic_patterns,
    load_applicable_diagnostic_methods,
)
from app.services.diagnostic_scope import normalize_artifact_source
from app.services.diagnostic_tools import (
    DiagnosticToolEnvironment,
    build_diagnostic_tool_registry,
    invoke_diagnostic_tool,
    summarize_log_method_usage,
)
from app.services.jobs import JobCancelledError, JobContext, job_runner
from app.services.llm import get_active_chat_model_info, get_llm_provider
from app.services import log_triage_planning
from app.services.log_triage_trace import append_log_planning_trace
from app.services.log_triage_evidence import (
    build_exact_hit,
    normalize_log_message,
    publish_triage_evidence,
)
from app.services.storage import storage
from app.services.text_files import open_text_lines


TRIAGE_PROMPT_VERSION = log_triage_planning.TRIAGE_PROMPT_VERSION
MAX_LLM_SELECTED_PATTERNS = log_triage_planning.MAX_LLM_SELECTED_PATTERNS
MAX_LLM_ADDITIONAL_KEYWORDS = log_triage_planning.MAX_LLM_ADDITIONAL_KEYWORDS
_case_issue = log_triage_planning.case_issue
_deterministic_plan = log_triage_planning.deterministic_plan
_planner_plan_with_model = log_triage_planning.plan_with_model
_planner_safe_plan = log_triage_planning.safe_plan

LLM_BUCKET = "LLM_RELEVANT"
METHOD_BUCKET = "METHOD_REQUIRED"
OTHER_BUCKET = "OTHER"


from app.services.workbench import case_model_job, run_configuration


@case_model_job
def submit_log_triage(
    db: Any,
    *,
    case: Case,
    artifact: Artifact,
    created_by: str,
    deduplicate: bool = False,
) -> tuple[LogTriageRun, Any, Any]:
    if artifact.case_id != case.id or not artifact.active_parse_run_id:
        raise ValueError("Artifact has no active parsed log generation")
    model_info = get_active_chat_model_info()
    if not model_info.get("is_mock") and not case.model_egress_approved:
        raise ValueError("This case has not approved method egress to the active Chat model")
    triage = LogTriageRun(
        id=new_id("LTRIAGE"),
        case_id=case.id,
        artifact_id=artifact.id,
        parse_run_id=artifact.active_parse_run_id,
        status="QUEUED",
        issue_snapshot=_case_issue(case),
        model_profile_id=str(model_info.get("profile_id") or "") or None,
        model_name=str(model_info.get("model") or "") or None,
    )
    db.add(triage)
    run = create_live_agent_run(
        db,
        operation="log_triage_planning",
        case_id=case.id,
        resource_type="log_triage",
        resource_id=triage.id,
        input_summary={
            "case_id": case.id,
            "artifact_id": artifact.id,
            "parse_run_id": artifact.active_parse_run_id,
        },
        model_profile_id=triage.model_profile_id,
        model_name=triage.model_name,
        model_config={
            **run_configuration(),
            "profile_name": model_info.get("profile_name"),
            "mode": model_info.get("mode"),
            "base_url": model_info.get("base_url"),
            "config": model_info.get("config", {}),
            "proxy_url_configured": model_info.get("proxy_url_configured", False),
        },
        prompt_version=TRIAGE_PROMPT_VERSION,
        created_by=created_by,
    )
    triage.agent_run_id = run.id
    db.flush()
    job = job_runner.submit(
        db,
        "log_triage",
        log_triage_job,
        triage.id,
        input_data={"triage_run_id": triage.id},
        deduplicate=deduplicate,
        idempotency_key=(
            f"log-triage:{artifact.id}:{artifact.active_parse_run_id}"
            if deduplicate
            else None
        ),
        max_attempts=1,
        timeout_seconds=20 * 60,
        resource_limits={"max_input_bytes": 16 * 1024},
    )
    return triage, run, job


async def _plan_with_model(
    case: Case,
    documents: list[DiagnosticMethodDocument],
    patterns: list[DiagnosticPattern],
    artifact_sources: list[dict[str, Any]] | None = None,
    provider: Any | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    provider = provider or get_llm_provider()
    return await _planner_plan_with_model(
        case, documents, patterns, artifact_sources, provider,
    )


def _safe_plan(
    case: Case,
    documents: list[DiagnosticMethodDocument],
    patterns: list[DiagnosticPattern],
    artifact_sources: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    return _planner_safe_plan(
        case, documents, patterns, artifact_sources, get_llm_provider,
    )


def _compiled_searchers(
    patterns: list[DiagnosticPattern],
    plan: dict[str, Any],
) -> list[dict[str, Any]]:
    selected_pattern_ids = list(dict.fromkeys(plan.get("selected_pattern_ids", [])))
    selected_ranks = {
        pattern_id: rank for rank, pattern_id in enumerate(selected_pattern_ids)
    }
    searchers: list[dict[str, Any]] = []
    for proposal_index, proposal in enumerate(plan.get("additional_keywords", []), start=1):
        keyword = str(proposal.get("keyword") or "").strip()
        if len(keyword) < 2:
            continue
        searchers.append({
            "id": f"LLMKW-{proposal_index:04d}",
            "text": keyword,
            "regex": re.compile(re.escape(keyword), re.IGNORECASE),
            "match_kind": "llm_literal",
            "bucket": LLM_BUCKET,
            "score": 0.88 + min(0.1, float(proposal.get("relevance") or 0.0) / 10),
            "reason": str(proposal.get("reason") or "LLM 规划关键词"),
            "meaning": str(
                proposal.get("reason")
                or "LLM 根据问题描述补充的相关日志特征"
            ),
            "document_id": None,
            "document_title": "LLM 规划补充关键词",
            "document_version": None,
            "source_type": "llm_plan",
            "heading": "LLM additional keyword",
            "line_start": None,
        })
    for pattern in patterns:
        try:
            compiled = re.compile(pattern.regex, re.IGNORECASE)
        except re.error:
            continue
        selected = pattern.id in selected_ranks
        selected_rank = selected_ranks.get(pattern.id, 0)
        selected_denominator = max(1, len(selected_pattern_ids) - 1)
        selected_score = 0.97 - (0.13 * selected_rank / selected_denominator)
        searchers.append({
            "id": pattern.id,
            "text": pattern.text,
            "regex": compiled,
            "match_kind": pattern.match_kind,
            "bucket": LLM_BUCKET if selected else METHOD_BUCKET,
            "score": selected_score if selected else 0.55,
            "reason": (
                "LLM 根据问题描述选中的方法关键词"
                if selected
                else "适用日志分析方法要求检查的关键词"
            ),
            "meaning": pattern.meaning,
            "document_id": (
                pattern.document_id
                if not pattern.document_id.startswith("LOCALDOC-")
                else None
            ),
            "local_document_id": pattern.document_id,
            "document_title": pattern.document_title,
            "document_version": pattern.document_version,
            "source_type": pattern.source_type,
            "heading": pattern.heading,
            "line_start": pattern.line_start,
        })
    return searchers


def _scan_events(
    ctx: JobContext,
    *,
    triage: LogTriageRun,
    searchers: list[dict[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
]:
    groups: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    hit_rows: list[dict[str, Any]] = []
    occurrence_map: dict[str, dict[str, Any]] = {}
    bucket_occurrences: Counter[str] = Counter()
    pattern_occurrences: Counter[str] = Counter()
    total_events = 0
    scanned_lines = 0
    event_by_line: dict[tuple[str, int], dict[str, Any]] = {}
    fallback_events: list[tuple[str, int, dict[str, Any]]] = []
    with SessionLocal() as db:
        artifact = db.get(Artifact, triage.artifact_id)
        case = db.get(Case, triage.case_id)
        artifact_source = normalize_artifact_source(artifact, case) if artifact else {}
        metadata = json_loads(artifact.metadata_json, {}) if artifact else {}
        statement = (
            select(LogEvent)
            .where(
                LogEvent.case_id == triage.case_id,
                LogEvent.artifact_id == triage.artifact_id,
                LogEvent.parse_run_id == triage.parse_run_id,
            )
            .order_by(LogEvent.source_file, LogEvent.line_start)
            .execution_options(yield_per=1000)
        )
        for event in db.scalars(statement):
            total_events += 1
            event_info = {
                "id": event.id,
                "timestamp": event.timestamp_normalized or event.timestamp_raw,
                "level": event.level,
                "module": event.module,
                "event_code": event.event_code,
                "raw_text": event.raw_text or event.message,
            }
            fallback_events.append((event.source_file, event.line_start, event_info))
            for line_number in range(event.line_start, event.line_end + 1):
                event_by_line[(event.source_file.replace("\\", "/"), line_number)] = event_info

    def process_line(
        *,
        source_file: str,
        line_number: int,
        text: str,
        event_info: dict[str, Any] | None,
    ) -> None:
        nonlocal scanned_lines
        scanned_lines += 1
        matched = [searcher for searcher in searchers if searcher["regex"].search(text)]
        if event_info and matched:
            best = max(
                matched,
                key=lambda item: (
                    item["bucket"] == LLM_BUCKET,
                    float(item["score"]),
                ),
            )
            existing = occurrence_map.get(str(event_info["id"]))
            all_pattern_ids = list(dict.fromkeys([
                *(json_loads(existing["pattern_ids_json"], []) if existing else []),
                *(item["id"] for item in matched),
            ]))
            if existing is None or (
                best["bucket"] == LLM_BUCKET and existing["bucket"] != LLM_BUCKET
            ) or float(best["score"]) > float(existing["relevance_score"]):
                occurrence_map[str(event_info["id"])] = {
                    "id": existing["id"] if existing else new_id("LEO"),
                    "triage_run_id": triage.id,
                    "event_id": event_info["id"],
                    "bucket": best["bucket"],
                    "relevance_score": best["score"],
                    "pattern_ids_json": json_dumps(all_pattern_ids),
                    "created_at": existing["created_at"] if existing else utcnow(),
                }
            elif existing:
                existing["pattern_ids_json"] = json_dumps(all_pattern_ids)

        normalized_message = normalize_log_message(text)
        for searcher in matched:
            key = (
                searcher["bucket"],
                searcher["id"],
                source_file,
                normalized_message,
            )
            group = groups.get(key)
            if group is None:
                group = {
                    "id": new_id("LEM"),
                    "triage_run_id": triage.id,
                    "case_id": triage.case_id,
                    "artifact_id": triage.artifact_id,
                    "source_file": source_file,
                    "line_start": line_number,
                    "line_end": line_number,
                    "bucket": searcher["bucket"],
                    "relevance_score": searcher["score"],
                    "pattern_id": searcher["id"],
                    "pattern_text": searcher["text"],
                    "match_kind": searcher["match_kind"],
                    "reason": searcher["reason"],
                    "meaning": searcher.get("meaning") or searcher["reason"],
                    "method_document_id": searcher.get("document_id"),
                    "method_version": searcher.get("document_version"),
                    "message": text[:4000],
                    "occurrence_count": 0,
                    "first_timestamp": event_info.get("timestamp") if event_info else None,
                    "last_timestamp": event_info.get("timestamp") if event_info else None,
                    "metadata_json": "{}",
                    "created_at": utcnow(),
                    "_sample_event_ids": [],
                    "_method_source": {
                        "document_id": searcher.get("local_document_id")
                        or searcher.get("document_id"),
                        "source_type": searcher.get("source_type"),
                        "document_title": searcher.get("document_title"),
                        "heading": searcher.get("heading"),
                        "line_start": searcher.get("line_start"),
                    },
                }
                groups[key] = group
            hit_rows.append(build_exact_hit(
                triage, group["id"], source_file, line_number, text, event_info,
            ))
            group["occurrence_count"] += 1
            group["line_end"] = max(group["line_end"], line_number)
            if event_info and event_info.get("timestamp"):
                group["last_timestamp"] = event_info["timestamp"]
            if event_info and len(group["_sample_event_ids"]) < 20:
                group["_sample_event_ids"].append(event_info["id"])
            bucket_occurrences[searcher["bucket"]] += 1
            pattern_occurrences[searcher["id"]] += 1

    scanned_source_files: set[str] = set()
    structured_event_fallback_count = 0
    extract_key = metadata.get("extract_root")
    manifest = metadata.get("manifest")
    if isinstance(extract_key, str) and isinstance(manifest, list):
        extract_root = storage.resolve_path(extract_key).resolve()
        if extract_root.is_dir():
            for manifest_item in manifest:
                relative_path = str(manifest_item.get("path") or "")
                if not relative_path:
                    continue
                source_path = (extract_root / Path(relative_path)).resolve()
                if not source_path.is_relative_to(extract_root) or not source_path.is_file():
                    continue
                opened = open_text_lines(source_path)
                if opened is None:
                    continue
                _, lines = opened
                scanned_source_files.add(relative_path.replace("\\", "/"))
                for line_number, text in enumerate(lines, start=1):
                    process_line(
                        source_file=relative_path,
                        line_number=line_number,
                        text=text,
                        event_info=event_by_line.get((relative_path.replace("\\", "/"), line_number)),
                    )
                    if scanned_lines % 5000 == 0:
                        ctx.update(
                            min(85, 55 + scanned_lines // 5000),
                            f"Scanning complete extracted log text ({scanned_lines:,} lines)",
                        )
                        ctx.raise_if_cancelled()

    for source_file, line_number, event_info in fallback_events:
        if source_file.replace("\\", "/") in scanned_source_files:
            continue
        structured_event_fallback_count += 1
        process_line(
            source_file=source_file,
            line_number=line_number,
            text=str(event_info["raw_text"]),
            event_info=event_info,
        )
        if scanned_lines % 5000 == 0:
            ctx.update(
                min(85, 55 + scanned_lines // 5000),
                f"Scanning parsed log events ({scanned_lines:,})",
            )
            ctx.raise_if_cancelled()

    raw_scan_completed = bool(scanned_source_files)

    match_rows: list[dict[str, Any]] = []
    for group in groups.values():
        group["metadata_json"] = json_dumps({
            "sample_event_ids": group.pop("_sample_event_ids"),
            "method_source": group.pop("_method_source"),
            "artifact_source": artifact_source,
        })
        match_rows.append(group)
    match_rows.sort(key=lambda item: (
        item["bucket"] != LLM_BUCKET,
        -float(item["relevance_score"]),
        -int(item["occurrence_count"]),
        item["source_file"],
        item["line_start"],
    ))
    summary = {
        "total_events": total_events,
        "total_scanned_lines": scanned_lines,
        "raw_text_scan_completed": raw_scan_completed,
        "raw_text_source_count": len(scanned_source_files),
        "structured_event_fallback_count": structured_event_fallback_count,
        "matched_events": len(occurrence_map),
        "other_events": max(0, total_events - len(occurrence_map)),
        "cluster_counts": dict(Counter(row["bucket"] for row in match_rows)),
        "occurrence_counts": dict(bucket_occurrences),
        "matched_pattern_count": len(pattern_occurrences),
        "pattern_occurrences": dict(pattern_occurrences),
        "exact_hit_count": len(hit_rows),
        "artifact_source": artifact_source,
        "knowledge_scope": "GW_AP_JOINT",
    }
    return match_rows, hit_rows, list(occurrence_map.values()), summary


def _mark_triage_failure(
    triage_run_id: str,
    *,
    status: str,
    stop_reason: str,
    error_message: str | None,
    started: float,
) -> None:
    with SessionLocal() as db:
        triage = db.get(LogTriageRun, triage_run_id)
        if not triage:
            return
        triage.status = status
        triage.error_message = error_message[:2000] if error_message else None
        triage.completed_at = utcnow()
        if triage.agent_run_id:
            append_live_trace(
                db,
                triage.agent_run_id,
                stage="log_triage",
                status=status,
                stop_reason=stop_reason,
                output_summary={"error_type": stop_reason},
                metadata={"reason": stop_reason},
                commit=False,
            )
            finish_live_agent_run(
                db,
                triage.agent_run_id,
                status=status,
                stop_reason=stop_reason,
                output_summary={"error_type": stop_reason},
                duration_ms=int((perf_counter() - started) * 1000),
                budget_ms=20 * 60 * 1000,
            )
        db.commit()


@case_model_job
def log_triage_job(ctx: JobContext, triage_run_id: str) -> dict[str, Any]:
    started = perf_counter()
    try:
        with SessionLocal() as db:
            triage = db.get(LogTriageRun, triage_run_id)
            if not triage:
                raise ValueError("Log triage run not found")
            case = db.get(Case, triage.case_id)
            artifact = db.get(Artifact, triage.artifact_id)
            if not case or not artifact or artifact.case_id != case.id:
                raise ValueError("Case or log artifact not found")
            if artifact.active_parse_run_id != triage.parse_run_id:
                raise ValueError("Log artifact parse generation changed; submit triage again")
            provider = get_llm_provider()
            if not provider.is_mock and not case.model_egress_approved:
                raise ValueError("Model egress approval was revoked before log planning")
            triage.status = "RUNNING"
            triage.error_message = None
            methods = load_applicable_diagnostic_methods(db, case)
            patterns = compile_diagnostic_patterns(methods)
            tool_registry = build_diagnostic_tool_registry(DiagnosticToolEnvironment(
                case=case, methods=methods, patterns=patterns,
            ))
            tool_context = ToolContext(role="ENGINEER", case_id=case.id)
            catalog_tool = invoke_diagnostic_tool(
                tool_registry, tool_context,
                tool_name="list_diagnostic_documents", arguments={},
            )
            read_tool = invoke_diagnostic_tool(
                tool_registry, tool_context,
                tool_name="read_diagnostic_documents",
                arguments={"document_ids": [method.id for method in methods]},
            ) if methods else None
            coverage = {
                "required_document_ids": [method.id for method in methods],
                "documents": [method.public_snapshot() for method in methods],
                "document_count": len(methods),
                "compiled_pattern_count": len(patterns),
                "status": "LOADED",
            }
            triage.method_coverage_json = json_dumps(coverage)
            if triage.agent_run_id:
                append_live_trace(
                    db,
                    triage.agent_run_id,
                    stage="list_diagnostic_documents",
                    tool_name="list_diagnostic_documents",
                    status="COMPLETED",
                    duration_ms=catalog_tool.duration_ms,
                    output_summary={
                        "documents": len(methods),
                        "patterns": len(patterns),
                    },
                    evidence_ids=[method.id for method in methods],
                    metadata={"candidate_count": len(methods)},
                    commit=False,
                )
                if read_tool:
                    append_live_trace(
                        db, triage.agent_run_id,
                        stage="read_diagnostic_documents",
                        tool_name="read_diagnostic_documents", status="COMPLETED",
                        duration_ms=read_tool.duration_ms,
                        output_summary={"documents": len(methods), "patterns": len(patterns)},
                        evidence_ids=read_tool.evidence_ids,
                        metadata={"candidate_count": len(methods), "role": "POLICY"},
                        commit=False,
                    )
            db.commit()

        ctx.update(20, "Planning relevant log evidence with the configured Chat model")
        ctx.raise_if_cancelled()
        planning_started = perf_counter()
        with SessionLocal() as db:
            active_artifacts = list(db.scalars(select(Artifact).where(
                Artifact.case_id == case.id,
                Artifact.active_parse_run_id.is_not(None),
            )).all())
        plan, model_result = _safe_plan(
            case,
            methods,
            patterns,
            [normalize_artifact_source(item, case) for item in active_artifacts],
        )
        with SessionLocal() as db:
            triage = db.get(LogTriageRun, triage_run_id)
            if not triage:
                raise ValueError("Log triage run was removed")
            coverage = json_loads(triage.method_coverage_json, {})
            coverage["read_document_ids"] = plan.get("read_document_ids", [])
            coverage["status"] = "READ_AND_PLANNED"
            coverage["all_required_read"] = set(coverage.get("required_document_ids", [])) == set(
                plan.get("read_document_ids", [])
            )
            triage.method_coverage_json = json_dumps(coverage)
            triage.plan_json = json_dumps(plan)
            if triage.agent_run_id:
                append_log_planning_trace(
                    db,
                    run_id=triage.agent_run_id,
                    plan=plan,
                    model_result=model_result,
                    duration_ms=int((perf_counter() - planning_started) * 1000),
                )
            db.commit()

        ctx.update(55, "Scanning all parsed events for planned and mandatory method patterns")
        searchers = _compiled_searchers(patterns, plan)
        match_rows, hit_rows, occurrences, summary = _scan_events(
            ctx,
            triage=triage,
            searchers=searchers,
        )
        ctx.update(90, "Publishing ranked log evidence buckets")
        ctx.raise_if_cancelled()
        with SessionLocal() as db:
            triage = db.get(LogTriageRun, triage_run_id)
            if not triage:
                raise ValueError("Log triage run was removed")
            publish_triage_evidence(db, match_rows, hit_rows, occurrences)
            summary.update({
                "triage_run_id": triage.id,
                "artifact_id": triage.artifact_id,
                "parse_run_id": triage.parse_run_id,
                "search_pattern_count": len(searchers),
                "planner_mode": plan.get("planner_mode"),
                "selected_pattern_count": len(plan.get("selected_pattern_ids", [])),
                "additional_keyword_count": len(plan.get("additional_keywords", [])),
                "planner_fallback": bool(model_result.get("fallback")),
                "planner_error_type": model_result.get("error_type"),
                "planner_status": (
                    "FALLBACK" if model_result.get("fallback") else "ACCEPTED"
                ),
                "planner_failure": model_result.get("failure"),
                "planner_thinking_mode": model_result.get("thinking_mode"),
                "planner_finish_reason": model_result.get("finish_reason"),
            })
            summary["method_usage"] = summarize_log_method_usage(
                methods, patterns, plan, summary,
            )
            for tool_call in plan.get("tool_calls", []):
                if tool_call.get("tool_name") == "search_log":
                    tool_call["status"] = "COMPLETED"
                    tool_call["returned"] = len(match_rows)
            triage.plan_json = json_dumps(plan)
            triage.summary_json = json_dumps(summary)
            triage.status = "COMPLETED"
            triage.completed_at = utcnow()
            job_result = {
                "triage_run_id": triage.id,
                "agent_run_id": triage.agent_run_id,
                "summary": summary,
            }
            ctx.complete_in_transaction(db, job_result)
            if triage.agent_run_id:
                append_live_trace(
                    db,
                    triage.agent_run_id,
                    stage="rank_log_evidence",
                    tool_name="local_pattern_scanner",
                    status="COMPLETED",
                    output_summary=summary,
                    evidence_ids=[row["id"] for row in match_rows[:250]],
                    metadata={
                        "candidate_count": summary["total_events"],
                        "returned_count": len(match_rows),
                    },
                    commit=False,
                )
                finish_live_agent_run(
                    db,
                    triage.agent_run_id,
                    status="COMPLETED",
                    stop_reason=(
                        "ALL_METHODS_SCANNED_WITH_DETERMINISTIC_FALLBACK"
                        if model_result.get("fallback")
                        else "ALL_METHODS_SCANNED"
                    ),
                    output_summary=summary,
                    duration_ms=int((perf_counter() - started) * 1000),
                    evidence_ids=[row["id"] for row in match_rows[:1000]],
                    budget_ms=20 * 60 * 1000,
                )
            db.commit()
        return job_result
    except JobCancelledError:
        _mark_triage_failure(
            triage_run_id,
            status="CANCELLED",
            stop_reason="CANCELLED",
            error_message=None,
            started=started,
        )
        raise
    except Exception as exc:
        _mark_triage_failure(
            triage_run_id,
            status="FAILED",
            stop_reason="TRIAGE_FAILED",
            error_message=str(exc) or type(exc).__name__,
            started=started,
        )
        raise
