from __future__ import annotations

import hashlib
import shutil
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.utils import json_dumps, json_loads, new_id, utcnow
from app.diagnostic_models import (
    LogEvidenceHit,
    LogEvidenceMatch,
    LogEvidenceOccurrence,
    LogTriageRun,
)
from app.models import AgentRun, AgentTraceEvent, AnalysisRun, Artifact, Case, LogEvent
from app.services.agent_trace import summary_hash
from app.services.demo_case_contract import (
    DEMO_CASE_ID_PREFIX,
    DEMO_DATASET_VERSION,
    DEMO_METHOD_LOG_ID,
    DEMO_METHOD_TREE_ID,
    load_demo_fixtures,
    load_demo_snapshot,
)
from app.services.log_parsers import HuaweiCollectDebugInfoParser
from app.services.storage import storage


def _case_id_for(principal: dict[str, Any]) -> str:
    principal_id = str(principal.get("id") or "local-development")
    owner_key = hashlib.sha256(principal_id.encode("utf-8")).hexdigest()[:10]
    return f"{DEMO_CASE_ID_PREFIX}-{owner_key}"


def _owner_id_for(principal: dict[str, Any]) -> str | None:
    return (
        str(principal.get("id"))
        if principal.get("type") == "user_token" and principal.get("id")
        else None
    )


def _recorded_at(value: Any) -> datetime:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return utcnow()


def _rebase(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, str):
        rendered = value
        for old, new in sorted(replacements.items(), key=lambda item: -len(item[0])):
            rendered = rendered.replace(old, new)
        return rendered
    if isinstance(value, list):
        return [_rebase(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: _rebase(item, replacements) for key, item in value.items()}
    return value


def _create_recorded_agent_run(
    db: Session,
    *,
    run_id: str,
    case_id: str,
    resource_type: str,
    resource_id: str,
    snapshot: dict[str, Any],
    evidence_ids: list[str],
    replacements: dict[str, str],
    created_by: str,
) -> AgentRun:
    usage = snapshot.get("usage") or {}
    completed_at = _recorded_at(snapshot.get("recorded_at"))
    duration_ms = max(0, int(usage.get("duration_ms") or 0))
    started_at = completed_at - timedelta(milliseconds=duration_ms)
    trace_events = _rebase(snapshot.get("trace_events") or [], replacements)
    normalized_evidence = list(dict.fromkeys(
        str(item) for item in evidence_ids if str(item).strip()
    ))[:1000]
    source_config = snapshot.get("model_config") or {}
    run = AgentRun(
        id=run_id,
        case_id=case_id,
        resource_type=resource_type,
        resource_id=resource_id,
        operation=str(snapshot.get("operation") or resource_type)[:64],
        execution_mode="recorded_llm_snapshot",
        status="COMPLETED",
        model_profile_id=None,
        model_name=str(snapshot.get("model_name") or "glm-5.2")[:512],
        model_config_json=json_dumps({
            "recorded_model_run": True,
            "snapshot_import_model_called": False,
            "source_execution_mode": snapshot.get("execution_mode"),
            "source_model_config": source_config,
        }),
        prompt_version=str(snapshot.get("prompt_version") or "")[:128],
        input_summary_hash=str(
            snapshot.get("input_summary_hash")
            or summary_hash({"dataset": DEMO_DATASET_VERSION, "operation": resource_type})
        )[:64],
        output_summary_hash=str(
            snapshot.get("output_summary_hash")
            or summary_hash({"dataset": DEMO_DATASET_VERSION, "resource": resource_id})
        )[:64],
        input_tokens=max(0, int(usage.get("input_tokens") or 0)),
        output_tokens=max(0, int(usage.get("output_tokens") or 0)),
        total_tokens=max(0, int(usage.get("total_tokens") or 0)),
        estimated_cost=max(0.0, float(usage.get("estimated_cost") or 0)),
        duration_ms=duration_ms,
        retry_count=max(0, int(usage.get("retry_count") or 0)),
        evidence_ids_json=json_dumps(normalized_evidence),
        stop_reason=str(snapshot.get("stop_reason") or "RECORDED_RUN_COMPLETE")[:128],
        approval_status="RECORDED_READ_ONLY",
        replay_payload_json=json_dumps({
            "recorded_model_run": True,
            "snapshot_import_model_called": False,
        }),
        score_json=json_dumps(snapshot.get("score") or {}),
        created_by=created_by,
        created_at=started_at,
        started_at=started_at,
        completed_at=completed_at,
    )
    db.add(run)
    db.flush()
    elapsed = 0
    for sequence, event in enumerate(trace_events, start=1):
        event_evidence = list(dict.fromkeys(
            str(item) for item in event.get("evidence_ids", []) if str(item).strip()
        ))[:250]
        event_duration = max(0, int(event.get("duration_ms") or 0))
        elapsed += event_duration
        db.add(AgentTraceEvent(
            id=new_id("ATRACE"),
            run_id=run.id,
            sequence=sequence,
            stage=str(event.get("stage") or f"stage-{sequence}")[:128],
            tool_name=str(
                event.get("tool_name") or event.get("stage") or "recorded_stage"
            )[:128],
            status=str(event.get("status") or "COMPLETED")[:32],
            input_summary_hash=summary_hash({
                "stage": event.get("stage"),
                "source_run_hash": snapshot.get("input_summary_hash"),
            }),
            output_summary_hash=summary_hash({
                "status": event.get("status"),
                "metadata": event.get("metadata") or {},
            }),
            input_tokens=max(0, int(event.get("input_tokens") or 0)),
            output_tokens=max(0, int(event.get("output_tokens") or 0)),
            duration_ms=event_duration,
            retry_count=max(0, int(event.get("retry_count") or 0)),
            evidence_ids_json=json_dumps(event_evidence),
            stop_reason=(
                str(event.get("stop_reason"))[:128]
                if event.get("stop_reason") else None
            ),
            metadata_json=json_dumps({
                **(event.get("metadata") or {}),
                "recorded_model_run": True,
                "snapshot_import_model_called": False,
            }),
            created_at=started_at + timedelta(milliseconds=elapsed),
        ))
    return run


def _copy_and_parse_fixture(
    db: Session,
    *,
    case: Case,
    fixture: Any,
    source_path: Path,
    raw: bytes,
    text: str,
) -> tuple[Artifact, list[LogEvent]]:
    artifact_id = new_id("ART")
    parse_run_id = new_id("PRUN")
    artifact_dir = storage.artifact_dir(artifact_id)
    stored_path = artifact_dir / fixture.filename
    extracted_dir = artifact_dir / "extracted"
    extracted_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_path, stored_path)
    shutil.copyfile(source_path, extracted_dir / fixture.filename)

    parser = HuaweiCollectDebugInfoParser()
    parsed = parser.parse(stored_path, fixture.filename, text)
    level_counts = Counter(event.level for event in parsed)
    metadata = {
        "demo_fixture": {
            "dataset_version": DEMO_DATASET_VERSION,
            "synthetic": True,
            "source_sha256": fixture.sha256,
            "recorded_model_run": True,
            "model_egress": False,
            "snapshot_import_model_egress": False,
        },
        "manifest": [{
            "path": fixture.filename,
            "size": len(raw),
            "sha256": fixture.sha256,
            "line_count": len(text.splitlines()),
            "encoding": "utf-8",
            "line_index": [[1, 0]],
        }],
        "manifest_file_count": 1,
        "extracted_bytes": len(raw),
        "parsed_files": 1,
        "event_count": len(parsed),
        "parser_counts": {parser.parser_id: 1},
        "level_counts": dict(level_counts),
        "device_info": {},
        "extract_root": storage.storage_key(extracted_dir),
    }
    artifact = Artifact(
        id=artifact_id,
        case_id=case.id,
        kind="debug_log",
        original_name=fixture.filename,
        stored_path=storage.storage_key(stored_path),
        sha256=fixture.sha256,
        size_bytes=len(raw),
        status="PARSED",
        source_device_type=fixture.device_type,
        source_device_role=fixture.device_role,
        metadata_json=json_dumps(metadata),
        active_parse_run_id=parse_run_id,
    )
    db.add(artifact)
    events: list[LogEvent] = []
    for parsed_event in parsed:
        event = LogEvent(
            id=new_id("EVT"),
            case_id=case.id,
            artifact_id=artifact.id,
            parse_run_id=parse_run_id,
            source_file=parsed_event.source_file,
            line_start=parsed_event.line_start,
            line_end=parsed_event.line_end,
            timestamp_raw=parsed_event.timestamp_raw,
            timestamp_normalized=parsed_event.timestamp_normalized,
            level=parsed_event.level,
            module=parsed_event.module,
            component=parsed_event.component,
            event_code=parsed_event.event_code,
            message=parsed_event.message,
            raw_text=parsed_event.raw_text,
            entities_json=json_dumps(parsed_event.entities),
            parser_id=parsed_event.parser_id,
            parser_version=parsed_event.parser_version,
            confidence=parsed_event.confidence,
        )
        db.add(event)
        events.append(event)
    return artifact, events


def _event_for_location(
    events_by_location: dict[tuple[str, int], LogEvent],
    source_file: str,
    line_start: int,
) -> LogEvent:
    event = events_by_location.get((source_file, int(line_start)))
    if event is None:
        raise ValueError(
            f"Recorded GLM demo hit cannot be mapped to parsed event: "
            f"{source_file}:{line_start}"
        )
    return event


def _create_recorded_triage(
    db: Session,
    *,
    case: Case,
    artifact: Artifact,
    events: list[LogEvent],
    snapshot: dict[str, Any],
    replacements: dict[str, str],
    created_by: str,
) -> tuple[LogTriageRun, dict[str, str]]:
    device_type = artifact.source_device_type
    triage_id = new_id("LTRIAGE")
    agent_run_id = new_id("ARUN")
    replacements[f"{{{{TRIAGE_{device_type}_ID}}}}"] = triage_id
    replacements[f"{{{{TRIAGE_{device_type}_AGENT_RUN_ID}}}}"] = agent_run_id

    match_ids = {
        str(item["snapshot_ref"]): new_id("LEM")
        for item in snapshot.get("matches") or []
    }
    replacements.update(match_ids)
    _create_recorded_agent_run(
        db,
        run_id=agent_run_id,
        case_id=case.id,
        resource_type="log_triage",
        resource_id=triage_id,
        snapshot=snapshot.get("agent_run") or {},
        evidence_ids=list(match_ids.values()),
        replacements=replacements,
        created_by=created_by,
    )
    recorded_at = _recorded_at((snapshot.get("agent_run") or {}).get("recorded_at"))
    method_coverage = _rebase(snapshot.get("method_coverage") or {}, replacements)
    plan = _rebase(snapshot.get("plan") or {}, replacements)
    summary = _rebase(snapshot.get("summary") or {}, replacements)
    summary["recorded_match_group_count"] = len(match_ids)
    triage = LogTriageRun(
        id=triage_id,
        case_id=case.id,
        artifact_id=artifact.id,
        parse_run_id=artifact.active_parse_run_id,
        agent_run_id=agent_run_id,
        status="COMPLETED",
        issue_snapshot=str(snapshot.get("issue_snapshot") or case.description),
        model_profile_id=None,
        model_name=str(snapshot.get("model_name") or "glm-5.2"),
        method_coverage_json=json_dumps(method_coverage),
        plan_json=json_dumps(plan),
        summary_json=json_dumps(summary),
        error_message=None,
        created_at=recorded_at,
        completed_at=recorded_at,
    )
    db.add(triage)
    db.flush()

    events_by_location = {
        (event.source_file, int(event.line_start)): event for event in events
    }
    assignments: dict[str, dict[str, Any]] = {}
    bucket_priority = {"LLM_RELEVANT": 2, "METHOD_REQUIRED": 1, "OTHER": 0}
    for item in snapshot.get("matches") or []:
        match_id = match_ids[str(item["snapshot_ref"])]
        method_source = ((item.get("metadata") or {}).get("method_source") or {})
        metadata = {
            "demo_snapshot": True,
            "recorded_model_run": True,
            "snapshot_import_model_called": False,
            "method_source": _rebase(method_source, replacements),
        }
        match = LogEvidenceMatch(
            id=match_id,
            triage_run_id=triage.id,
            case_id=case.id,
            artifact_id=artifact.id,
            source_file=str(item["source_file"]),
            line_start=int(item["line_start"]),
            line_end=int(item["line_end"]),
            bucket=str(item["bucket"]),
            relevance_score=float(item["relevance_score"]),
            pattern_id=str(item["pattern_id"])[:96],
            pattern_text=str(item["pattern_text"]),
            match_kind=str(item["match_kind"])[:32],
            reason=str(item.get("reason") or ""),
            meaning=str(item.get("meaning") or ""),
            method_document_id=None,
            method_version=(
                int(item["method_version"]) if item.get("method_version") else None
            ),
            message=str(item["message"]),
            occurrence_count=int(item["occurrence_count"]),
            first_timestamp=item.get("first_timestamp"),
            last_timestamp=item.get("last_timestamp"),
            metadata_json=json_dumps(metadata),
            created_at=recorded_at,
        )
        db.add(match)
        db.flush()
        hits = item.get("hits") or []
        if len(hits) != match.occurrence_count:
            raise ValueError(f"Recorded match occurrence count mismatch: {item['pattern_id']}")
        for hit in hits:
            event = _event_for_location(
                events_by_location,
                str(hit["source_file"]),
                int(hit["line_start"]),
            )
            db.add(LogEvidenceHit(
                id=new_id("LEH"),
                triage_run_id=triage.id,
                match_id=match.id,
                artifact_id=artifact.id,
                source_file=str(hit["source_file"]),
                line_start=int(hit["line_start"]),
                line_end=int(hit["line_end"]),
                timestamp=hit.get("timestamp"),
                message=str(hit["message"]),
                created_at=recorded_at,
            ))
            current = assignments.get(event.id)
            pattern_ids = [str(item["pattern_id"])]
            if current:
                pattern_ids = list(dict.fromkeys(
                    [*current["pattern_ids"], str(item["pattern_id"])]
                ))
            candidate = {
                "event": event,
                "bucket": str(item["bucket"]),
                "score": float(item["relevance_score"]),
                "pattern_ids": pattern_ids,
            }
            if current and bucket_priority[current["bucket"]] > bucket_priority[candidate["bucket"]]:
                candidate["bucket"] = current["bucket"]
                candidate["score"] = max(current["score"], candidate["score"])
            elif current and bucket_priority[current["bucket"]] == bucket_priority[candidate["bucket"]]:
                candidate["score"] = max(current["score"], candidate["score"])
            assignments[event.id] = candidate

    for assignment in assignments.values():
        db.add(LogEvidenceOccurrence(
            id=new_id("LEO"),
            triage_run_id=triage.id,
            event_id=assignment["event"].id,
            bucket=assignment["bucket"],
            relevance_score=assignment["score"],
            pattern_ids_json=json_dumps(assignment["pattern_ids"]),
            created_at=recorded_at,
        ))
    expected_hits = int(summary.get("exact_hit_count") or 0)
    expected_events = int(summary.get("matched_events") or 0)
    if expected_hits != sum(len(item.get("hits") or []) for item in snapshot.get("matches") or []):
        raise ValueError(f"Recorded {device_type} triage hit count changed")
    if expected_events != len(assignments):
        raise ValueError(f"Recorded {device_type} triage event coverage changed")

    metadata = json_loads(artifact.metadata_json, {})
    metadata["latest_log_triage_run_id"] = triage.id
    metadata["demo_snapshot"] = True
    metadata["recorded_model_run"] = True
    artifact.metadata_json = json_dumps(metadata)
    db.flush()
    return triage, match_ids


def _build_evidence(
    snapshot: dict[str, Any],
    *,
    events: list[LogEvent],
    artifacts: list[Artifact],
    match_ids: dict[str, str],
    replacements: dict[str, str],
) -> list[dict[str, Any]]:
    artifact_by_id = {artifact.id: artifact for artifact in artifacts}
    events_by_location = {
        (event.source_file, int(event.line_start), event.event_code): event
        for event in events
    }
    event_by_ref: dict[str, LogEvent] = {}
    for item in snapshot.get("source_evidence") or []:
        if item.get("source_type") != "log_event":
            continue
        key = (
            str(item["source_file"]),
            int(item["line_start"]),
            str(item["event_code"]),
        )
        event = events_by_location.get(key)
        if event is None:
            raise ValueError(f"Recorded event cannot be rebased: {key}")
        ref = str(item["snapshot_ref"])
        event_by_ref[ref] = event
        replacements[ref] = event.id

    identity_id = new_id("LDE")
    replacements["DREF-IDENTITY-CHECK"] = identity_id
    replacements.update(match_ids)
    replacements[DEMO_METHOD_LOG_ID] = DEMO_METHOD_LOG_ID
    replacements[DEMO_METHOD_TREE_ID] = DEMO_METHOD_TREE_ID

    evidence: list[dict[str, Any]] = []
    memory_index = 0
    for item in snapshot.get("source_evidence") or []:
        source_type = str(item.get("source_type") or "")
        if source_type in {"analysis_skill", "fault_tree"}:
            evidence.append({
                "evidence_id": str(item["id"]),
                **{key: value for key, value in item.items() if key != "id"},
            })
        elif source_type == "log_event":
            event = event_by_ref[str(item["snapshot_ref"])]
            artifact = artifact_by_id[event.artifact_id]
            evidence.append({
                "evidence_id": event.id,
                "source_type": "log_event",
                "source_file": event.source_file,
                "line_start": event.line_start,
                "line_end": event.line_end,
                "timestamp": event.timestamp_normalized or event.timestamp_raw,
                "level": event.level,
                "module": event.module,
                "component": event.component,
                "event_code": event.event_code,
                "content": event.raw_text,
                "confidence": event.confidence,
                "artifact_source": {
                    "artifact_id": artifact.id,
                    "original_name": artifact.original_name,
                    "device_type": artifact.source_device_type,
                    "device_role": artifact.source_device_role,
                    "topology_relation": (
                        "GW_PRIMARY" if artifact.source_device_type == "GW"
                        else "AP_SECONDARY"
                    ),
                },
            })
        elif source_type == "log_triage_match":
            ref = str(item["snapshot_ref"])
            match_id = match_ids[ref]
            evidence.append({
                "evidence_id": match_id,
                "source_type": "log_triage_match",
                "source_file": item["source_file"],
                "line_start": item["line_start"],
                "line_end": item["line_end"],
                "pattern_id": item["pattern_id"],
                "content_omitted": True,
            })
        elif source_type == "local_derived_evidence":
            evidence.append({
                "evidence_id": identity_id,
                "source_type": source_type,
                "source_file": item["source_file"],
                "line_start": item["line_start"],
                "line_end": item["line_end"],
                "event_code": item["event_code"],
                "content": (
                    "Local deterministic UDN/AP MAC comparison succeeded; "
                    "synthetic identifier values are not repeated in the snapshot."
                ),
                "meaning": item.get("meaning"),
                "metadata": item.get("metadata") or {},
            })
        elif source_type.startswith("memory_"):
            ref = str(item["snapshot_ref"])
            memory_index += 1
            memory_id = f"DEMO-RECORDED-MEMORY-{memory_index:02d}"
            replacements[ref] = memory_id
            evidence.append({
                "evidence_id": memory_id,
                "source_type": source_type,
                "title": item.get("title"),
                "score": item.get("score"),
                "content_omitted": True,
                "snapshot_notice": "历史检索元数据；记忆正文未随演示快照分发。",
            })
        else:
            raise ValueError(f"Unsupported recorded evidence source: {source_type}")
    if len(evidence) != 152:
        raise ValueError("Recorded GLM demo evidence rebasing is incomplete")
    return evidence


def _create_recorded_analysis(
    db: Session,
    *,
    case: Case,
    artifacts: list[Artifact],
    events: list[LogEvent],
    snapshot: dict[str, Any],
    match_ids: dict[str, str],
    replacements: dict[str, str],
    created_by: str,
) -> AnalysisRun:
    analysis_snapshot = snapshot.get("analysis") or {}
    analysis_id = new_id("RUN")
    agent_run_id = new_id("ARUN")
    replacements["{{CASE_ID}}"] = case.id
    replacements["{{ANALYSIS_RUN_ID}}"] = analysis_id
    replacements["{{ANALYSIS_AGENT_RUN_ID}}"] = agent_run_id
    evidence = _build_evidence(
        snapshot,
        events=events,
        artifacts=artifacts,
        match_ids=match_ids,
        replacements=replacements,
    )
    evidence_ids = [str(item["evidence_id"]) for item in evidence]
    _create_recorded_agent_run(
        db,
        run_id=agent_run_id,
        case_id=case.id,
        resource_type="analysis",
        resource_id=analysis_id,
        snapshot=analysis_snapshot.get("agent_run") or {},
        evidence_ids=evidence_ids,
        replacements=replacements,
        created_by=created_by,
    )
    result = _rebase(analysis_snapshot.get("result") or {}, replacements)
    planning = result.setdefault("diagnostic_planning", {})
    planning["demo_snapshot"] = True
    planning["recorded_model_run"] = True
    planning["snapshot_import_model_called"] = False
    result["recorded_run_provenance"] = snapshot.get("provenance") or {}
    result["snapshot_imported_at"] = utcnow().isoformat()
    result["analysis_run_id"] = analysis_id
    recorded_at = _recorded_at(
        (analysis_snapshot.get("agent_run") or {}).get("recorded_at")
    )
    analysis = AnalysisRun(
        id=analysis_id,
        case_id=case.id,
        status="COMPLETED",
        provider=str(analysis_snapshot.get("provider") or "openai_compatible"),
        model=str(analysis_snapshot.get("model") or "glm-5.2"),
        model_profile_id=None,
        agent_run_id=agent_run_id,
        model_config_json=json_dumps({
            "recorded_model_run": True,
            "snapshot_import_model_called": False,
            "endpoint_origin": (snapshot.get("provenance") or {}).get("endpoint_origin"),
            "credentials_included": False,
            "private_method_bodies_included": False,
        }),
        prompt_version=str(analysis_snapshot.get("prompt_version") or "")[:32],
        result_json=json_dumps(result),
        evidence_json=json_dumps(evidence),
        error_message=None,
        created_at=recorded_at,
        completed_at=recorded_at,
    )
    db.add(analysis)
    case.status = "COMPLETED"
    case.severity = "P1"
    return analysis


def _existing_demo_result(db: Session, case: Case) -> dict[str, Any] | None:
    artifacts = list(db.scalars(
        select(Artifact).where(Artifact.case_id == case.id)
    ).all())
    triage_count = int(db.scalar(
        select(func.count(LogTriageRun.id)).where(
            LogTriageRun.case_id == case.id,
            LogTriageRun.status == "COMPLETED",
        )
    ) or 0)
    completed_analyses = db.scalars(
        select(AnalysisRun).where(
            AnalysisRun.case_id == case.id,
            AnalysisRun.status == "COMPLETED",
        ).order_by(AnalysisRun.created_at.desc())
    ).all()
    analysis = None
    for candidate in completed_analyses:
        candidate_result = json_loads(candidate.result_json, {})
        if (
            candidate.model == "glm-5.2"
            and candidate_result.get("recorded_model_run") is True
        ):
            analysis = candidate
            break
    if (
        len(artifacts) != 2
        or any(artifact.status != "PARSED" for artifact in artifacts)
        or triage_count < 2
        or analysis is None
    ):
        return None
    event_count = int(db.scalar(
        select(func.count(LogEvent.id)).where(LogEvent.case_id == case.id)
    ) or 0)
    return {
        "created": False,
        "restored": False,
        "case": case,
        "artifact_count": len(artifacts),
        "event_count": event_count,
        "triage_run_count": triage_count,
        "analysis_run_id": analysis.id,
        "demo_snapshot": True,
        "model_called": False,
        "recorded_model_run": True,
        "recorded_model": "glm-5.2",
        "recorded_total_tokens": 321_453,
    }


def import_ap_frequent_offline_demo(
    db: Session,
    *,
    principal: dict[str, Any],
) -> dict[str, Any]:
    case_id = _case_id_for(principal)
    existing = db.get(Case, case_id)
    if existing:
        result = _existing_demo_result(db, existing)
        if result:
            return result

    fixtures = load_demo_fixtures()
    snapshot = load_demo_snapshot()
    restored = existing is not None
    if existing:
        artifact_ids = list(db.scalars(
            select(Artifact.id).where(Artifact.case_id == existing.id)
        ).all())
        db.delete(existing)
        db.commit()
        for artifact_id in artifact_ids:
            try:
                storage.remove_artifact(artifact_id)
            except (OSError, ValueError):
                pass

    created_artifact_ids: list[str] = []
    created_by = str(principal.get("id") or "local-development")
    case = Case(
        id=case_id,
        owner_id=_owner_id_for(principal),
        title="演示：AP频繁离线 - UDM进程反复异常",
        device_type="AP",
        device_model="DEMO-GW-9000 + DEMO-AP-6000",
        firmware_version="DEMO-V1.0.0",
        topology="主 GW（Parent 33）直连从 AP1，GW/AP 联合诊断。",
        description=(
            "[SYNTHETIC DEMO] AP1 每约十分钟离线后恢复；需要判断物理链路、"
            "UDM 协议栈或网络传输问题，并给出文件行号证据。"
        ),
        reproduction_steps="观察三次 UDM 异常、心跳中断、GW 超时判离线和自动恢复。",
        issue_time="2026-08-28 10:00:00",
        status="PARSED",
        severity="P1",
        model_egress_approved=False,
    )
    try:
        db.add(case)
        db.flush()
        artifacts: list[Artifact] = []
        events: list[LogEvent] = []
        events_by_artifact: dict[str, list[LogEvent]] = {}
        for fixture, source_path, raw, text in fixtures:
            artifact, parsed_events = _copy_and_parse_fixture(
                db,
                case=case,
                fixture=fixture,
                source_path=source_path,
                raw=raw,
                text=text,
            )
            created_artifact_ids.append(artifact.id)
            artifacts.append(artifact)
            events.extend(parsed_events)
            events_by_artifact[artifact.id] = parsed_events
        if len(events) != 74:
            raise ValueError("Synthetic demo parser output changed; expected 74 events")
        db.flush()

        replacements = {
            DEMO_METHOD_LOG_ID: DEMO_METHOD_LOG_ID,
            DEMO_METHOD_TREE_ID: DEMO_METHOD_TREE_ID,
        }
        triages: list[LogTriageRun] = []
        all_match_ids: dict[str, str] = {}
        for artifact in artifacts:
            triage_snapshot = (snapshot.get("triage") or {}).get(
                artifact.source_device_type
            )
            if not isinstance(triage_snapshot, dict):
                raise ValueError(
                    f"Recorded triage snapshot missing for {artifact.source_device_type}"
                )
            triage, match_ids = _create_recorded_triage(
                db,
                case=case,
                artifact=artifact,
                events=events_by_artifact[artifact.id],
                snapshot=triage_snapshot,
                replacements=replacements,
                created_by=created_by,
            )
            triages.append(triage)
            all_match_ids.update(match_ids)
        analysis = _create_recorded_analysis(
            db,
            case=case,
            artifacts=artifacts,
            events=events,
            snapshot=snapshot,
            match_ids=all_match_ids,
            replacements=replacements,
            created_by=created_by,
        )
        db.commit()
        db.refresh(case)
        return {
            "created": True,
            "restored": restored,
            "case": case,
            "artifact_count": len(artifacts),
            "event_count": len(events),
            "triage_run_count": len(triages),
            "analysis_run_id": analysis.id,
            "demo_snapshot": True,
            "model_called": False,
            "recorded_model_run": True,
            "recorded_model": "glm-5.2",
            "recorded_total_tokens": 321_453,
        }
    except IntegrityError:
        db.rollback()
        for artifact_id in created_artifact_ids:
            try:
                storage.remove_artifact(artifact_id)
            except (OSError, ValueError):
                pass
        concurrent = db.get(Case, case_id)
        if concurrent:
            result = _existing_demo_result(db, concurrent)
            if result:
                return result
        raise
    except Exception:
        db.rollback()
        for artifact_id in created_artifact_ids:
            try:
                storage.remove_artifact(artifact_id)
            except (OSError, ValueError):
                pass
        raise
