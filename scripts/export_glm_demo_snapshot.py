#!/usr/bin/env python3
"""Export the successful synthetic AP-offline GLM run as a safe demo snapshot.

The source database is runtime-only and may contain credentials and private method
documents.  This exporter uses an explicit allowlist, replaces database primary
keys with stable demo references, and never serializes model-profile secrets,
raw prompts, private method bodies, or memory bodies.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import ipaddress
import json
import re
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


DATASET_VERSION = "ap-frequent-offline-glm52-v1"
METHOD_ID_BY_ROLE = {
    "LOG_ANALYSIS_METHOD": "DEMO-METHOD-LOG-ANALYSIS",
    "FAULT_TREE": "DEMO-METHOD-FAULT-TREE",
}
PRIVATE_RESULT_KEYS = {
    "description",
    "evidence_hints",
}
INTERNAL_ID_RE = re.compile(
    r"\b(?:CASE|ART|PRUN|LTRIAGE|RUN|ARUN|ATRACE|EVT|LEM|LEH|LEO|LDE|"
    r"LOCALDOC|MEM|MODEL|JOB|FTITEM)-[A-Za-z0-9_.:-]+\b"
)
SECRET_RE = re.compile(
    r"(?i)(?:api[_ -]?key|authorization|bearer|ciphertext|password|secret|"
    r"sk-[a-z0-9]{16,})"
)
MAC_IDENTIFIER_RE = re.compile(
    r"(?i)(?<![a-z0-9])(?:"
    r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}|"
    r"(?:[0-9a-f]{2}-){5}[0-9a-f]{2}|"
    r"(?:[0-9a-f]{4}\.){2}[0-9a-f]{4}|(?<![a-z0-9_-])[0-9a-f]{12}"
    r")(?![a-z0-9])"
)
UUID_IDENTIFIER_RE = re.compile(
    r"(?i)\b[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}(?P<mac>[0-9a-f]{12})\b"
)
IPV4_LITERAL_RE = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
DEMO_MAC_IDENTIFIERS = {"020000000033", "020000000101"}
PRIVATE_IPV4_NETWORKS = tuple(
    ipaddress.IPv4Network(value)
    for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
DEMO_IPV4_NETWORK = ipaddress.IPv4Network("192.0.2.0/24")


def _identifier(value: str) -> tuple[str, str] | None:
    if MAC_IDENTIFIER_RE.fullmatch(value):
        return "mac", re.sub(r"[:.\-]", "", value).casefold()
    try:
        return "ipv4", str(ipaddress.IPv4Address(value))
    except ipaddress.AddressValueError:
        return None


def _validate_redaction_map(value: Any) -> dict[tuple[str, str], str]:
    """Accept only explicit device-example mappings, never arbitrary text rewrites."""
    if not isinstance(value, dict) or len(value) > 128:
        raise ValueError("Redaction map must be a JSON object with at most 128 entries")
    mappings: dict[tuple[str, str], str] = {}
    for source, target in value.items():
        if not isinstance(source, str) or not isinstance(target, str):
            raise ValueError("Redaction map entries must be identifier strings")
        source_id, target_id = _identifier(source), _identifier(target)
        if source_id is None or target_id is None or source_id[0] != target_id[0]:
            raise ValueError("Redaction map must preserve the identifier kind")
        if source_id[0] == "mac":
            allowed = (
                source_id[1] not in DEMO_MAC_IDENTIFIERS
                and target_id[1] in DEMO_MAC_IDENTIFIERS
            )
        else:
            allowed = (
                any(ipaddress.IPv4Address(source_id[1]) in net for net in PRIVATE_IPV4_NETWORKS)
                and ipaddress.IPv4Address(target_id[1]) in DEMO_IPV4_NETWORK
            )
        if not allowed:
            raise ValueError("Redaction map must map private examples to fixed demo identifiers")
        if source_id in mappings and mappings[source_id] != target_id[1]:
            raise ValueError("Redaction map contains conflicting forms of one identifier")
        mappings[source_id] = target_id[1]
    return mappings


def _redact_example_identifiers(
    value: Any, mappings: dict[tuple[str, str], str],
) -> Any:
    """Fail closed for unknown devices; preserve MAC spelling and structured keys."""
    def redact_mac(match: re.Match[str]) -> str:
        original = match.group(0)
        identifier = _identifier(original)
        assert identifier is not None
        if identifier[1] in DEMO_MAC_IDENTIFIERS:
            return original
        target = mappings.get(identifier)
        if target is None:
            raise ValueError("Snapshot contains a non-demo MAC; supply --redaction-map")
        separator = next((item for item in (":", "-", ".") if item in original), "")
        if not separator:
            return target
        width = 4 if separator == "." else 2
        return separator.join(target[index:index + width] for index in range(0, 12, width))

    def redact_ip(match: re.Match[str]) -> str:
        original = match.group(0)
        identifier = _identifier(original)
        if identifier is None or not any(
            ipaddress.IPv4Address(original) in network for network in PRIVATE_IPV4_NETWORKS
        ):
            return original
        target = mappings.get(identifier)
        if target is None:
            raise ValueError("Snapshot contains a private IPv4 address; supply --redaction-map")
        return target

    def redact_uuid(match: re.Match[str]) -> str:
        # A UUID's device tail is meaningful here; policy-check-<hash> is not a MAC.
        tail = match.group("mac").casefold()
        if tail in DEMO_MAC_IDENTIFIERS:
            return match.group(0)
        target = mappings.get(("mac", tail))
        if target is None:
            raise ValueError("Snapshot contains a non-demo UUID device tail; supply --redaction-map")
        return match.group(0)[:-12] + target

    if isinstance(value, str):
        rendered = UUID_IDENTIFIER_RE.sub(redact_uuid, value)
        rendered = MAC_IDENTIFIER_RE.sub(redact_mac, rendered)
        return IPV4_LITERAL_RE.sub(redact_ip, rendered)
    if isinstance(value, list):
        return [_redact_example_identifiers(item, mappings) for item in value]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            redacted_key = _redact_example_identifiers(key, mappings)
            if redacted_key in result:
                raise ValueError("Identifier redaction would merge distinct object keys")
            result[redacted_key] = _redact_example_identifiers(item, mappings)
        return result
    return value


def _load_redaction_map(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    if path.stat().st_size > 65_536:
        raise ValueError("Redaction map exceeds the 64 KiB limit")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise ValueError("Redaction map must contain valid UTF-8 JSON") from None
    _validate_redaction_map(value)
    return value


def _json(raw: str | None, default: Any) -> Any:
    if not raw:
        return copy.deepcopy(default)
    return json.loads(raw)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stable_ref(prefix: str, *parts: Any) -> str:
    digest = _sha256_text("|".join(str(part) for part in parts))[:16]
    return f"DREF-{prefix}-{digest}"


def _replace_text(value: str, replacements: dict[str, str]) -> str:
    rendered = value
    for old, new in sorted(replacements.items(), key=lambda item: -len(item[0])):
        rendered = rendered.replace(old, new)
    return rendered


def _replace_ids(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, str):
        return _replace_text(value, replacements)
    if isinstance(value, list):
        return [_replace_ids(item, replacements) for item in value]
    if isinstance(value, dict):
        return {
            key: _replace_ids(item, replacements)
            for key, item in value.items()
        }
    return value


def _method_record(item: dict[str, Any], replacements: dict[str, str]) -> dict[str, Any]:
    method_id = replacements[str(item.get("id") or item.get("evidence_id"))]
    return {
        "id": method_id,
        "title": str(item.get("title") or "诊断方法"),
        "source_type": str(item.get("source_type") or "method"),
        "version": int(item.get("version") or 1),
        "device_type": item.get("device_type"),
        "module": item.get("module"),
        "content_sha256": str(item.get("content_sha256") or ""),
        "role": str(item.get("role") or ""),
        "content_omitted": True,
        "snapshot_notice": "历史运行时已读取；私有 Markdown 正文未随演示快照分发。",
    }


def _safe_fault_item(
    item: dict[str, Any], replacements: dict[str, str], *, document_title: str = "故障树",
) -> dict[str, Any]:
    allowed = {
        "id",
        "item_id",
        "method_document_id",
        "category",
        "label",
        "status",
        "rationale",
        "conclusion",
        "evidence_ids",
        "next_action",
        "attempted",
        "last_round",
        "resolution_source",
    }
    result = {key: item.get(key) for key in allowed if key in item}
    if "id" in result:
        result["id"] = replacements.get(str(result["id"]), str(result["id"]))
    if "item_id" in result:
        result["item_id"] = replacements.get(
            str(result["item_id"]), str(result["item_id"]),
        )
    result["document_title"] = document_title
    result["section"] = "历史运行时私有故障树（正文未分发）"
    result["line_start"] = None
    return _replace_ids(result, replacements)


def _coverage_summary(value: dict[str, Any], replacements: dict[str, str]) -> dict[str, Any]:
    allowed = {
        "total",
        "attempted",
        "concluded",
        "complete",
        "status_counts",
        "fallback_applied",
        "fallback_reason",
        "resolution_source_counts",
    }
    result = {key: value.get(key) for key in allowed if key in value}
    if isinstance(value.get("items"), list):
        result["items"] = [
            _safe_fault_item(item, replacements)
            for item in value["items"]
            if isinstance(item, dict)
        ]
    return result


def _safe_round(value: dict[str, Any], replacements: dict[str, str]) -> dict[str, Any]:
    allowed = {
        "read_document_ids",
        "method_assessments",
        "hypotheses",
        "checks",
        "search_queries",
        "tool_calls",
        "evidence_gaps",
        "continue_analysis",
        "stop_reason",
        "round",
        "planner_repairs",
        "planning_attempts",
        "context_governance",
        "executed_tool_calls",
        "agent_budget_after_round",
    }
    result = {key: copy.deepcopy(value.get(key)) for key in allowed if key in value}
    assessments = value.get("fault_tree_assessments")
    if isinstance(assessments, list):
        result["fault_tree_assessments"] = [
            _safe_fault_item(item, replacements)
            for item in assessments
            if isinstance(item, dict)
        ]
    after_round = value.get("fault_tree_coverage_after_round")
    if isinstance(after_round, dict):
        result["fault_tree_coverage_after_round"] = _coverage_summary(
            {key: item for key, item in after_round.items() if key != "items"},
            replacements,
        )
    return _replace_ids(result, replacements)


def _safe_planning(value: dict[str, Any], replacements: dict[str, str]) -> dict[str, Any]:
    allowed = {
        "planner_mode",
        "planner_accepted",
        "agent_mode",
        "prompt_version",
        "search_query_count",
        "stop_reason",
        "planner_failure",
        "budget",
        "context_governance",
    }
    result = {key: copy.deepcopy(value.get(key)) for key in allowed if key in value}
    result["rounds"] = [
        _safe_round(item, replacements)
        for item in value.get("rounds", [])
        if isinstance(item, dict)
    ]
    result["method_coverage"] = _replace_ids(
        copy.deepcopy(value.get("method_coverage") or {}), replacements,
    )
    result["method_catalog"] = [
        _method_record(item, replacements)
        for item in value.get("method_catalog", [])
        if isinstance(item, dict)
    ]
    usage_by_id = {
        str(item.get("id")): item
        for item in value.get("method_usage", [])
        if isinstance(item, dict)
    }
    result["method_usage"] = []
    for method in result["method_catalog"]:
        source_id = next(
            (old for old, new in replacements.items() if new == method["id"]), "",
        )
        usage = usage_by_id.get(source_id, {})
        merged = {**method}
        for key in (
            "read_status",
            "relevance",
            "relevance_rationale",
            "check_count",
            "tool_call_count",
            "evidence_hit_count",
        ):
            if key in usage:
                merged[key] = copy.deepcopy(usage[key])
        result["method_usage"].append(_replace_ids(merged, replacements))
    coverage = value.get("fault_tree_coverage")
    if isinstance(coverage, dict):
        result["fault_tree_coverage"] = _coverage_summary(coverage, replacements)
    result["tool_calls"] = _replace_ids(
        copy.deepcopy(value.get("tool_calls") or []), replacements,
    )
    return _replace_ids(result, replacements)


def _safe_result(result: dict[str, Any], replacements: dict[str, str]) -> dict[str, Any]:
    allowed = {
        "summary",
        "case",
        "confirmed_facts",
        "hypotheses",
        "recommended_actions",
        "missing_information",
        "suspected_modules",
        "retrieved_knowledge",
        "related_code",
        "limitations",
        "analysis_engine",
        "agentic_search",
        "synthesis_status",
        "generated_at",
    }
    safe = {key: copy.deepcopy(result.get(key)) for key in allowed if key in result}
    safe["diagnostic_planning"] = _safe_planning(
        result.get("diagnostic_planning") or {}, replacements,
    )
    safe["fault_tree_findings"] = [
        _safe_fault_item(item, replacements)
        for item in result.get("fault_tree_findings", [])
        if isinstance(item, dict)
    ]
    safe["fault_tree_conclusions"] = [
        _safe_fault_item(item, replacements)
        for item in result.get("fault_tree_conclusions", [])
        if isinstance(item, dict)
    ]
    safe["analysis_run_id"] = "{{ANALYSIS_RUN_ID}}"
    safe["demo_snapshot"] = True
    safe["recorded_model_run"] = True
    safe["demo_snapshot_notice"] = (
        "这是历史真实 GLM-5.2 成功运行的脱敏快照；导入时不会再次调用模型。"
    )
    return _replace_ids(safe, replacements)


def _safe_trace_events(
    db: sqlite3.Connection,
    run_id: str,
    replacements: dict[str, str],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    rows = db.execute(
        "select * from agent_trace_events where run_id=? order by sequence", (run_id,),
    ).fetchall()
    for row in rows:
        metadata = _replace_ids(_json(row["metadata_json"], {}), replacements)
        evidence_ids = [
            replacements[item]
            for item in _json(row["evidence_ids_json"], [])
            if item in replacements
        ]
        events.append({
            "stage": row["stage"],
            "tool_name": row["tool_name"],
            "status": row["status"],
            "input_tokens": int(row["input_tokens"] or 0),
            "output_tokens": int(row["output_tokens"] or 0),
            "duration_ms": int(row["duration_ms"] or 0),
            "retry_count": int(row["retry_count"] or 0),
            "evidence_ids": evidence_ids,
            "stop_reason": row["stop_reason"],
            "metadata": metadata,
        })
    return events


def _safe_agent_run(row: sqlite3.Row, trace_events: list[dict[str, Any]]) -> dict[str, Any]:
    model_config = _json(row["model_config_json"], {})
    safe_config = {
        "profile_name": model_config.get("profile_name"),
        "mode": model_config.get("mode"),
        "config": {
            key: value
            for key, value in (model_config.get("config") or {}).items()
            if key in {"temperature", "timeout_seconds", "thinking_mode", "max_retries"}
        },
        "proxy_url_configured": bool(model_config.get("proxy_url_configured")),
        "certificate_revocation_check_skipped": bool(
            model_config.get("certificate_revocation_check_skipped")
        ),
    }
    return {
        "operation": row["operation"],
        "execution_mode": row["execution_mode"],
        "status": row["status"],
        "model_name": row["model_name"],
        "model_config": safe_config,
        "prompt_version": row["prompt_version"],
        "input_summary_hash": row["input_summary_hash"],
        "output_summary_hash": row["output_summary_hash"],
        "usage": {
            "input_tokens": int(row["input_tokens"] or 0),
            "output_tokens": int(row["output_tokens"] or 0),
            "total_tokens": int(row["total_tokens"] or 0),
            "estimated_cost": float(row["estimated_cost"] or 0),
            "duration_ms": int(row["duration_ms"] or 0),
            "retry_count": int(row["retry_count"] or 0),
        },
        "stop_reason": row["stop_reason"],
        "approval_status": row["approval_status"],
        "score": _json(row["score_json"], {}),
        "recorded_at": str(row["completed_at"] or row["created_at"]),
        "trace_events": trace_events,
    }


def _safe_triage(
    db: sqlite3.Connection,
    row: sqlite3.Row,
    artifact: sqlite3.Row,
    replacements: dict[str, str],
) -> dict[str, Any]:
    agent = db.execute(
        "select * from agent_runs where id=?", (row["agent_run_id"],),
    ).fetchone()
    if agent is None:
        raise ValueError(f"Missing triage agent run for {row['id']}")
    method_coverage = _replace_ids(
        _json(row["method_coverage_json"], {}), replacements,
    )
    for document in method_coverage.get("documents", []):
        document["content_omitted"] = True
        document["snapshot_notice"] = "历史运行时已读取；私有正文未分发。"
    plan = _replace_ids(_json(row["plan_json"], {}), replacements)
    summary = _replace_ids(_json(row["summary_json"], {}), replacements)
    for container in (summary.get("method_usage", []),):
        for document in container:
            document["content_omitted"] = True
            document["snapshot_notice"] = "历史运行时已读取；私有正文未分发。"
    for key in ("triage_run_id", "artifact_id", "parse_run_id"):
        summary.pop(key, None)
    if isinstance(summary.get("artifact_source"), dict):
        summary["artifact_source"].pop("artifact_id", None)
    plan["demo_snapshot"] = True
    plan["recorded_model_run"] = True
    plan["snapshot_import_model_called"] = False
    summary["demo_snapshot"] = True
    summary["recorded_model_run"] = True
    summary["snapshot_import_model_called"] = False
    method_coverage["demo_snapshot"] = True
    method_coverage["recorded_model_run"] = True

    matches: list[dict[str, Any]] = []
    match_rows = db.execute(
        "select * from log_evidence_matches where triage_run_id=? "
        "order by case bucket when 'LLM_RELEVANT' then 0 when 'METHOD_REQUIRED' then 1 else 2 end, "
        "relevance_score desc, source_file, line_start, id",
        (row["id"],),
    ).fetchall()
    for match in match_rows:
        ref = replacements[match["id"]]
        metadata = _json(match["metadata_json"], {})
        method_source = metadata.get("method_source")
        safe_metadata: dict[str, Any] = {}
        if isinstance(method_source, dict):
            safe_metadata["method_source"] = {
                "document_id": replacements.get(
                    str(method_source.get("document_id") or ""),
                    str(method_source.get("document_id") or ""),
                ),
                "source_type": method_source.get("source_type"),
                "document_title": method_source.get("document_title"),
                "content_omitted": True,
            }
        hits = [
            {
                "source_file": hit["source_file"],
                "line_start": int(hit["line_start"]),
                "line_end": int(hit["line_end"]),
                "timestamp": hit["timestamp"],
                "message": hit["message"],
            }
            for hit in db.execute(
                "select * from log_evidence_hits where match_id=? order by line_start, id",
                (match["id"],),
            ).fetchall()
        ]
        matches.append({
            "snapshot_ref": ref,
            "source_file": match["source_file"],
            "line_start": int(match["line_start"]),
            "line_end": int(match["line_end"]),
            "bucket": match["bucket"],
            "relevance_score": float(match["relevance_score"]),
            "pattern_id": match["pattern_id"],
            "pattern_text": match["pattern_text"],
            "match_kind": match["match_kind"],
            "reason": match["reason"],
            "meaning": match["meaning"],
            "method_document_id": replacements.get(
                str(match["method_document_id"] or ""), None,
            ),
            "method_version": match["method_version"],
            "message": match["message"],
            "occurrence_count": int(match["occurrence_count"]),
            "first_timestamp": match["first_timestamp"],
            "last_timestamp": match["last_timestamp"],
            "metadata": safe_metadata,
            "hits": hits,
        })
    trace = _safe_trace_events(db, agent["id"], replacements)
    return {
        "device_type": artifact["source_device_type"],
        "device_role": artifact["source_device_role"],
        "source_file": artifact["original_name"],
        "issue_snapshot": row["issue_snapshot"],
        "model_name": row["model_name"],
        "method_coverage": method_coverage,
        "plan": plan,
        "summary": summary,
        "agent_run": _safe_agent_run(agent, trace),
        "matches": matches,
    }


def export_snapshot(
    database: Path,
    analysis_run_id: str,
    *,
    redaction_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    if not analysis_run_id.strip():
        raise ValueError("An explicit analysis run ID is required")
    identifier_mappings = _validate_redaction_map(redaction_map or {})
    db = sqlite3.connect(database)
    db.row_factory = sqlite3.Row
    try:
        analysis = db.execute(
            "select * from analysis_runs where id=?", (analysis_run_id,),
        ).fetchone()
        if analysis is None or analysis["status"] != "COMPLETED":
            raise ValueError("The selected analysis run is not COMPLETED")
        result_raw = analysis["result_json"]
        evidence_raw = analysis["evidence_json"]
        result = _json(result_raw, {})
        evidence = _json(evidence_raw, [])
        agent = db.execute(
            "select * from agent_runs where id=?", (analysis["agent_run_id"],),
        ).fetchone()
        profile = db.execute(
            "select id,name,task_type,mode,provider,model_name,base_url,config_json "
            "from model_profiles where id=?",
            (analysis["model_profile_id"],),
        ).fetchone()
        case = db.execute(
            "select * from cases where id=?", (analysis["case_id"],),
        ).fetchone()
        if agent is None or profile is None or case is None:
            raise ValueError("Analysis provenance is incomplete")
        endpoint = urlsplit(str(profile["base_url"] or ""))
        endpoint_origin = f"{endpoint.scheme}://{endpoint.netloc}"
        if (
            analysis["provider"] != "openai_compatible"
            or analysis["model"] != "glm-5.2"
            or endpoint_origin != "https://wawapii.com"
        ):
            raise ValueError("Selected run is not the approved WawAPI GLM-5.2 demo run")

        artifacts = db.execute(
            "select * from artifacts where case_id=? order by source_device_type desc",
            (analysis["case_id"],),
        ).fetchall()
        artifact_by_id = {row["id"]: row for row in artifacts}
        expected_hashes = {
            "GW_collectDebuginfo_demo.txt": "a8bdb3121c3093b038fa67e0ce103f0d5483fe6062ddd41518eb7befdc85f318",
            "AP_collectDebuginfo_demo.txt": "80a72a62e371b328c649569118cd7a688c472660ab29ec55cabae1b75ff539ed",
        }
        if {
            row["original_name"]: row["sha256"] for row in artifacts
        } != expected_hashes:
            raise ValueError("Source artifacts do not match the synthetic demo fixtures")

        replacements: dict[str, str] = {
            analysis["case_id"]: "{{CASE_ID}}",
            analysis["id"]: "{{ANALYSIS_RUN_ID}}",
            analysis["agent_run_id"]: "{{ANALYSIS_AGENT_RUN_ID}}",
            profile["id"]: "recorded-profile-redacted",
            "MODEL-embedding-hashing": "bundled-hashing-baseline",
        }
        for artifact in artifacts:
            replacements[artifact["id"]] = artifact["original_name"]
            if artifact["active_parse_run_id"]:
                replacements[artifact["active_parse_run_id"]] = artifact["original_name"]

        method_evidence = [
            item for item in evidence
            if item.get("source_type") in {"analysis_skill", "fault_tree"}
        ]
        for item in method_evidence:
            role = str(item.get("role") or "")
            if role not in METHOD_ID_BY_ROLE:
                raise ValueError(f"Unexpected method role in evidence: {role}")
            replacements[str(item["evidence_id"])] = METHOD_ID_BY_ROLE[role]

        planning = result.get("diagnostic_planning") or {}
        coverage_items = (planning.get("fault_tree_coverage") or {}).get("items") or []
        for index, item in enumerate(coverage_items, start=1):
            source_id = str(item.get("id") or item.get("item_id") or "")
            if source_id:
                replacements[source_id] = f"DEMO-FT-{index:02d}"

        for item in evidence:
            source_id = str(item.get("evidence_id") or "")
            if not source_id or source_id in replacements:
                continue
            source_type = str(item.get("source_type") or "evidence")
            if source_type == "local_derived_evidence":
                replacements[source_id] = "DREF-IDENTITY-CHECK"
            elif source_type == "log_event":
                replacements[source_id] = _stable_ref(
                    "EVENT", item.get("source_file"), item.get("line_start"),
                    item.get("line_end"), item.get("event_code"),
                )
            elif source_type == "log_triage_match":
                replacements[source_id] = _stable_ref(
                    "MATCH", item.get("source_file"), item.get("line_start"),
                    item.get("line_end"), item.get("pattern_id"),
                )
            elif source_type.startswith("memory_"):
                replacements[source_id] = _stable_ref(
                    "MEMORY", source_type, item.get("title"), source_id,
                )
            else:
                raise ValueError(f"Unexpected evidence source type: {source_type}")

        triage_ids = list((planning.get("method_coverage") or {}).get("triage_run_ids") or [])
        if len(triage_ids) != 2:
            raise ValueError("Expected exactly two triage runs in the selected analysis")
        for triage_id in triage_ids:
            row = db.execute(
                "select artifact_id,agent_run_id from log_triage_runs where id=?",
                (triage_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Missing triage run {triage_id}")
            device = artifact_by_id[row["artifact_id"]]["source_device_type"]
            replacements[triage_id] = f"{{{{TRIAGE_{device}_ID}}}}"
            replacements[row["agent_run_id"]] = f"{{{{TRIAGE_{device}_AGENT_RUN_ID}}}}"

        source_evidence: list[dict[str, Any]] = []
        for item in evidence:
            source_id = str(item.get("evidence_id") or "")
            source_type = str(item.get("source_type") or "")
            ref = replacements[source_id]
            if source_type in {"analysis_skill", "fault_tree"}:
                source_evidence.append(_method_record(item, replacements))
            elif source_type == "log_event":
                source_evidence.append({
                    "snapshot_ref": ref,
                    "source_type": source_type,
                    "source_file": item.get("source_file"),
                    "line_start": item.get("line_start"),
                    "line_end": item.get("line_end"),
                    "timestamp": item.get("timestamp"),
                    "event_code": item.get("event_code"),
                })
            elif source_type == "log_triage_match":
                source_evidence.append({
                    "snapshot_ref": ref,
                    "source_type": source_type,
                    "source_file": item.get("source_file"),
                    "line_start": item.get("line_start"),
                    "line_end": item.get("line_end"),
                    "pattern_id": item.get("pattern_id"),
                })
            elif source_type == "local_derived_evidence":
                source_evidence.append({
                    "snapshot_ref": ref,
                    "source_type": source_type,
                    "source_file": item.get("source_file"),
                    "line_start": item.get("line_start"),
                    "line_end": item.get("line_end"),
                    "event_code": item.get("event_code"),
                    "meaning": item.get("meaning"),
                    "metadata": {
                        "comparison_complete": True,
                        "udn_location": {
                            "source_file": "GW_collectDebuginfo_demo.txt",
                            "line": 21,
                        },
                        "mac_location": {
                            "source_file": "GW_collectDebuginfo_demo.txt",
                            "line": 14,
                        },
                    },
                })
            elif source_type.startswith("memory_"):
                source_evidence.append({
                    "snapshot_ref": ref,
                    "source_type": source_type,
                    "title": item.get("title"),
                    "score": item.get("score"),
                    "content_omitted": True,
                })

        triages: dict[str, Any] = {}
        for triage_id in triage_ids:
            row = db.execute(
                "select * from log_triage_runs where id=?", (triage_id,),
            ).fetchone()
            artifact = artifact_by_id[row["artifact_id"]]
            triages[artifact["source_device_type"]] = _safe_triage(
                db, row, artifact, replacements,
            )

        analysis_trace = _safe_trace_events(db, agent["id"], replacements)
        safe_result = _safe_result(result, replacements)
        safe_result["case"] = {
            "id": "{{CASE_ID}}",
            "title": case["title"],
            "device_type": case["device_type"],
            "device_model": case["device_model"],
            "firmware_version": case["firmware_version"],
        }
        snapshot = {
            "schema_version": 1,
            "dataset_version": DATASET_VERSION,
            "provenance": {
                "kind": "recorded_real_model_run",
                "provider": analysis["provider"],
                "profile_name": profile["name"],
                "model": analysis["model"],
                "endpoint_origin": endpoint_origin,
                "endpoint_path_omitted": True,
                "recorded_at": str(analysis["completed_at"]),
                "prompt_version": analysis["prompt_version"],
                "source_result_sha256": _sha256_text(result_raw),
                "source_evidence_sha256": _sha256_text(evidence_raw),
                "source_analysis_id_sha256": _sha256_text(analysis["id"]),
                "source_agent_run_id_sha256": _sha256_text(agent["id"]),
                "source_log_sha256": expected_hashes,
                "sanitization": {
                    "credentials_omitted": True,
                    "raw_prompts_omitted": True,
                    "private_method_bodies_omitted": True,
                    "memory_bodies_omitted": True,
                    "database_primary_keys_rebased_on_import": True,
                    "private_method_example_identifiers_replaced": True,
                },
            },
            "source_evidence": source_evidence,
            "triage": triages,
            "analysis": {
                "provider": analysis["provider"],
                "model": analysis["model"],
                "prompt_version": analysis["prompt_version"],
                "agent_run": _safe_agent_run(agent, analysis_trace),
                "result": safe_result,
            },
        }
        snapshot = _redact_example_identifiers(snapshot, identifier_mappings)
        rendered = json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if SECRET_RE.search(rendered):
            raise ValueError("Snapshot secret scan failed")
        unresolved = sorted(set(INTERNAL_ID_RE.findall(rendered)))
        if unresolved:
            raise ValueError("Snapshot still contains unreplaced internal identifiers")
        return snapshot
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--database", type=Path, default=Path("backend/data/gw_ap_debug.db"),
    )
    parser.add_argument(
        "--run-id", "--analysis-run-id", dest="analysis_run_id", required=True,
        help="Explicit completed synthetic analysis run; no local run is selected implicitly",
    )
    parser.add_argument(
        "--redaction-map", type=Path,
        help="Local JSON original-to-demo MAC/RFC1918 mappings; do not commit this file",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "sample_data/demo_ap_frequent_offline/glm52_success_snapshot.json"
        ),
    )
    args = parser.parse_args()
    snapshot = export_snapshot(
        args.database, args.analysis_run_id,
        redaction_map=_load_redaction_map(args.redaction_map),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    print(f"Wrote {args.output} ({args.output.stat().st_size} bytes)")
    print(f"SHA256 {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
