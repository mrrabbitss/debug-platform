from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_ROOT = ROOT / "sample_data" / "demo_ap_frequent_offline"
EXPECTED_FAULT_TREE_STATUSES = {
    "场景1": "EXCLUDED",
    "场景2": "SUPPORTED",
    "场景3": "INSUFFICIENT_EVIDENCE",
    "4.3": "SUPPORTED",
    "端口": "INSUFFICIENT_EVIDENCE",
    "心跳": "INSUFFICIENT_EVIDENCE",
}
EXPECTED_HYPOTHESIS_CODES = {
    "AP_UDM_HEARTBEAT_SEND_FAILED",
    "AP_UDM_LISTEN_PORT_FAILED",
    "AP_UDM_PROCESS_ABNORMAL",
    "GW_AP_HEARTBEAT_TIMEOUT",
}
REQUIRED_METHOD_ROLES = {"FAULT_TREE", "LOG_ANALYSIS_METHOD"}
MAX_JOB_TIMEOUT_SECONDS = 4 * 60 * 60
FORBIDDEN_BROAD_KEYWORDS = {
    "ap", "critical", "debug", "error", "failed", "failure", "gw", "info",
    "notice", "offline", "online", "start", "status", "stop", "success", "warn",
    "warning",
}
INTERNAL_EVIDENCE_ID = re.compile(
    r"\b(?:EVT|LEM|LEH|LDE|DOC|KCHUNK|LOCALDOC|SYM|COMMIT|MEM|ANL|AREV)-"
    r"[A-Za-z0-9_.:-]+\b"
)
LOCAL_METHOD_FILES = {
    "FAULT_TREE": ROOT / "故障树.md",
    "LOG_ANALYSIS_METHOD": ROOT / "日志分析.md",
}
KNOWLEDGE_SOURCE_TYPES = {
    "FAULT_TREE": ("fault_tree",),
    "LOG_ANALYSIS_METHOD": (
        "analysis_method", "analysis_skill", "log_rule", "diagnostic_rule",
    ),
}


def _has_active_knowledge_source(
    client: httpx.Client,
    api_root: str,
    source_types: tuple[str, ...],
) -> bool:
    for source_type in source_types:
        response = client.get(
            f"{api_root}/knowledge",
            params={"source_type": source_type, "limit": 1000},
        )
        response.raise_for_status()
        if any(
            item.get("active") is True and item.get("review_status") == "ACTIVE"
            for item in response.json()
            if isinstance(item, dict)
        ):
            return True
    return False


def _verify_method_preconditions(
    client: httpx.Client,
    api_root: str,
) -> None:
    missing = [
        role
        for role, local_path in LOCAL_METHOD_FILES.items()
        if not local_path.is_file()
        and not _has_active_knowledge_source(
            client, api_root, KNOWLEDGE_SOURCE_TYPES[role],
        )
    ]
    if missing:
        raise RuntimeError(
            "Required diagnostic methods are unavailable before seeding the demo: "
            f"{', '.join(missing)}. Place the Git-ignored local method files in the "
            "repository root or publish equivalent reviewed documents in Knowledge."
        )
    print("Method preflight: fault tree and log-analysis method available")


def _wait_job(
    client: httpx.Client,
    api_root: str,
    job_id: str,
    *,
    timeout_seconds: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_snapshot: tuple[str, int, str] | None = None
    while True:
        job = client.get(f"{api_root}/jobs/{job_id}").raise_for_status().json()
        snapshot = (
            str(job["status"]),
            int(job["progress"]),
            str(job.get("message") or ""),
        )
        if snapshot != last_snapshot:
            print(f"{job['kind']}: {snapshot[0]} {snapshot[1]}% {snapshot[2]}")
            last_snapshot = snapshot
        if job["status"] == "COMPLETED":
            return job
        if job["status"] in {"FAILED", "DEAD_LETTER", "CANCELLED"}:
            raise RuntimeError(job.get("error_message") or f"Job ended as {job['status']}")
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Job {job_id} did not finish within {timeout_seconds} seconds "
                f"(last status={job['status']}, progress={job['progress']}%)"
            )
        time.sleep(1)


def _upload_log(
    client: httpx.Client,
    api_root: str,
    case_id: str,
    path: Path,
    device_type: str,
    device_role: str,
    *,
    timeout_seconds: int,
) -> dict[str, Any]:
    with path.open("rb") as handle:
        artifact = client.post(
            f"{api_root}/cases/{case_id}/artifacts",
            files={"file": (path.name, handle, "text/plain")},
            data={
                "kind": "debug_log",
                "source_device_type": device_type,
                "source_device_role": device_role,
            },
        ).raise_for_status().json()
    parse_job = client.post(
        f"{api_root}/cases/{case_id}/artifacts/{artifact['id']}/parse"
    ).raise_for_status().json()
    _wait_job(
        client, api_root, parse_job["id"], timeout_seconds=timeout_seconds,
    )
    return artifact


def _verify_triage(
    client: httpx.Client,
    api_root: str,
    case_id: str,
    artifact: dict[str, Any],
) -> None:
    triage = client.get(
        f"{api_root}/cases/{case_id}/log-triage",
        params={"artifact_id": artifact["id"]},
    ).raise_for_status().json()
    plan = triage.get("plan") or {}
    summary = triage.get("summary") or {}
    coverage = triage.get("method_coverage") or {}
    method_usage = summary.get("method_usage") or []
    if summary.get("planner_fallback") or summary.get("planner_status") != "ACCEPTED":
        raise RuntimeError(
            f"LLM log plan was not accepted for {artifact.get('original_name')}"
        )
    if coverage.get("all_required_read") is not True:
        raise RuntimeError(
            f"Not all methods were read for {artifact.get('original_name')}"
        )
    roles = {
        str(item.get("role") or "")
        for item in method_usage if isinstance(item, dict)
    }
    if not REQUIRED_METHOD_ROLES.issubset(roles):
        raise RuntimeError(
            f"Required diagnostic method roles were not loaded for "
            f"{artifact.get('original_name')}: {sorted(roles)}"
        )
    accepted_keywords = {
        str(item.get("keyword") or "").strip().casefold()
        for item in plan.get("additional_keywords", [])
        if isinstance(item, dict)
    }
    broad_keywords = accepted_keywords.intersection(FORBIDDEN_BROAD_KEYWORDS)
    if broad_keywords:
        raise RuntimeError(
            f"Broad supplemental keywords escaped the precision policy for "
            f"{artifact.get('original_name')}: {sorted(broad_keywords)}"
        )
    if not plan.get("selected_pattern_ids") and not accepted_keywords:
        raise RuntimeError(
            f"LLM returned an empty log-screening selection for "
            f"{artifact.get('original_name')}"
        )
    if int(summary.get("matched_events") or 0) <= 0:
        raise RuntimeError(
            f"Log triage produced no matched events for "
            f"{artifact.get('original_name')}"
        )
    bucket_pages = {
        bucket: client.get(
            f"{api_root}/cases/{case_id}/log-triage/{triage['id']}/evidence",
            params={"bucket": bucket, "limit": 500},
        ).raise_for_status().json()
        for bucket in ("LLM_RELEVANT", "METHOD_REQUIRED", "OTHER")
    }
    empty_buckets = [
        bucket for bucket, page in bucket_pages.items()
        if int(page.get("total") or 0) <= 0
    ]
    if empty_buckets:
        raise RuntimeError(
            f"Three-tier log display is incomplete for "
            f"{artifact.get('original_name')}: {empty_buckets}"
        )
    broad_selected_patterns = {
        str(item.get("pattern_text") or "").strip().casefold()
        for item in bucket_pages["LLM_RELEVANT"].get("items", [])
        if str(item.get("pattern_text") or "").strip().casefold()
        in FORBIDDEN_BROAD_KEYWORDS
    }
    if broad_selected_patterns:
        raise RuntimeError(
            f"Broad method patterns escaped into the LLM-relevant tier for "
            f"{artifact.get('original_name')}: {sorted(broad_selected_patterns)}"
        )
    if not any(
        int(item.get("occurrence_count") or 0) > 1
        for item in bucket_pages["LLM_RELEVANT"].get("items", [])
    ):
        raise RuntimeError(
            f"The LLM-relevant tier has no repeated occurrence to demonstrate for "
            f"{artifact.get('original_name')}"
        )
    print(
        f"Log triage {artifact.get('original_name')}: accepted, "
        f"methods={coverage.get('document_count', 0)}, "
        f"matched={summary.get('matched_events', 0)}, "
        f"thinking={summary.get('planner_thinking_mode')}"
    )


def _findings_by_label(result: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    findings: dict[str, list[dict[str, Any]]] = {}
    for item in result.get("fault_tree_findings", []):
        if not isinstance(item, dict):
            continue
        findings.setdefault(str(item.get("label") or ""), []).append(item)
    return findings


def _verify_fault_tree(result: dict[str, Any]) -> None:
    findings = _findings_by_label(result)
    missing_labels = sorted(set(EXPECTED_FAULT_TREE_STATUSES).difference(findings))
    if missing_labels:
        raise RuntimeError(
            "Required AP-offline method nodes are unavailable: "
            + ", ".join(missing_labels)
        )
    mismatches = [
        f"{label}={sorted({item.get('status') for item in findings[label]})} "
        f"(expected {expected})"
        for label, expected in EXPECTED_FAULT_TREE_STATUSES.items()
        if not any(item.get("status") == expected for item in findings[label])
    ]
    if mismatches:
        raise RuntimeError("Fault-tree semantic verification failed: " + "; ".join(mismatches))
    all_findings = [
        item
        for item in result.get("fault_tree_findings", [])
        if isinstance(item, dict)
    ]
    tree_evidence_ids = {
        str(evidence_id)
        for item in all_findings
        for evidence_id in item.get("evidence_ids", [])
    }
    if any(evidence_id.startswith("MEM-") for evidence_id in tree_evidence_ids):
        raise RuntimeError("Knowledge or memory was incorrectly used as current-case evidence")
    if not any(
        item.get("status") == "SUPPORTED"
        and any(
            str(evidence_id).startswith("LDE-")
            for evidence_id in item.get("evidence_ids", [])
        )
        for item in findings["4.3"]
    ):
        raise RuntimeError("UDN/AP MAC comparison did not produce local derived evidence")
    print(
        "Key fault-tree states: "
        + ", ".join(
            f"{label}={expected}"
            for label, expected in EXPECTED_FAULT_TREE_STATUSES.items()
        )
    )


def _verify_diagnostic_planning(
    result: dict[str, Any],
    *,
    require_llm: bool,
) -> None:
    planning = result.get("diagnostic_planning") or {}
    coverage = planning.get("method_coverage") or {}
    usage = planning.get("method_usage") or []
    roles = {
        str(item.get("role") or "")
        for item in usage if isinstance(item, dict)
    }
    if coverage.get("all_documents_read") is not True:
        raise RuntimeError("Comprehensive diagnosis did not read every applicable method")
    if not REQUIRED_METHOD_ROLES.issubset(roles):
        raise RuntimeError(
            f"Comprehensive diagnosis did not load both method roles: {sorted(roles)}"
        )
    if not require_llm:
        return
    rounds = planning.get("rounds") or []
    if (
        planning.get("planner_accepted") is not True
        or planning.get("planner_mode") != "llm_multiround"
        or planning.get("planner_failure")
    ):
        raise RuntimeError(
            "Comprehensive LLM planning was not accepted: "
            f"mode={planning.get('planner_mode')}, "
            f"stop={planning.get('stop_reason')}, "
            f"failure={planning.get('planner_failure')}"
        )
    if not (1 <= len(rounds) <= 20):
        raise RuntimeError(f"LLM planning round count is outside 1..20: {len(rounds)}")
    relevant_roles = {
        str(item.get("role") or "")
        for item in usage
        if isinstance(item, dict) and item.get("relevance") == "RELEVANT"
    }
    if not REQUIRED_METHOD_ROLES.issubset(relevant_roles):
        raise RuntimeError(
            "GLM did not mark both the fault tree and log-analysis method relevant: "
            f"{sorted(relevant_roles)}"
        )
    repair_codes = {
        str(repair.get("code") or "")
        for planning_round in rounds
        if isinstance(planning_round, dict)
        for repair in planning_round.get("planner_repairs", [])
        if isinstance(repair, dict)
    }
    disallowed_repairs = repair_codes.intersection({
        "MISSING_METHOD_ASSESSMENTS_ADDED",
        "SYMPTOM_METHOD_RELEVANCE_UPGRADED",
    })
    if disallowed_repairs:
        raise RuntimeError(
            "GLM did not independently assess both required methods: "
            f"{sorted(disallowed_repairs)}"
        )
    tool_calls = [
        call for call in planning.get("tool_calls", [])
        if isinstance(call, dict) and int(call.get("round") or 0) > 0
    ]
    model_searches = [
        call for call in tool_calls
        if call.get("tool_name") == "search_log"
        and call.get("invoked_by") == "MODEL"
        and call.get("status") == "COMPLETED"
        and int(call.get("returned") or 0) > 0
        and call.get("evidence_ids")
    ]
    if not model_searches:
        raise RuntimeError(
            "LLM planning did not execute a non-empty model-directed search_log"
        )
    searched_ids_by_call = {
        str(call.get("call_id") or ""): {
            str(item) for item in call.get("evidence_ids", []) if item
        }
        for call in model_searches
    }
    hydrated = False
    for call in tool_calls:
        if (
            call.get("tool_name") != "get_evidence"
            or call.get("invoked_by") != "POLICY_EVIDENCE_HYDRATION"
            or call.get("status") != "COMPLETED"
            or int(call.get("returned") or 0) <= 0
        ):
            continue
        source_ids = searched_ids_by_call.get(
            str(call.get("hydrated_from_call_id") or ""), set(),
        )
        fetched_ids = {
            str(item) for item in call.get("evidence_ids", []) if item
        }
        if fetched_ids and fetched_ids.issubset(source_ids):
            hydrated = True
            break
    if not hydrated:
        raise RuntimeError(
            "Model-directed search_log evidence was not causally hydrated by "
            "the read-only evidence policy"
        )
    fault_coverage = planning.get("fault_tree_coverage") or {}
    if fault_coverage.get("fallback_applied") is True:
        raise RuntimeError(
            "LLM planning required deterministic fallback: "
            f"{fault_coverage.get('fallback_reason')}"
        )
    print(
        "Diagnostic planning: accepted, "
        f"rounds={len(rounds)}/20, methods={len(usage)}, "
        f"stop={planning.get('stop_reason')}"
    )


def _verify_hypotheses(result: dict[str, Any]) -> None:
    deterministic = result.get("deterministic_baseline")
    if not isinstance(deterministic, dict):
        deterministic = result
    codes = {
        str(item.get("event_code") or "")
        for item in deterministic.get("hypotheses", [])
        if isinstance(item, dict)
    }
    missing = sorted(EXPECTED_HYPOTHESIS_CODES.difference(codes))
    if missing:
        raise RuntimeError(
            "Diagnosis omitted expected causal-chain hypotheses: " + ", ".join(missing)
        )
    visible_hypotheses = [
        item for item in result.get("hypotheses", []) if isinstance(item, dict)
    ]
    if not any(
        "udm" in f"{item.get('title')} {item.get('description')}".casefold()
        and item.get("supporting_evidence")
        for item in visible_hypotheses
    ):
        raise RuntimeError(
            "Final diagnosis did not retain an evidence-backed UDM hypothesis"
        )


def _verify_report_preview(
    client: httpx.Client,
    api_root: str,
    case_id: str,
    analysis_id: str,
) -> None:
    preview = client.get(
        f"{api_root}/cases/{case_id}/analyses/{analysis_id}/report/preview"
    ).raise_for_status().text
    leaked = sorted(set(INTERNAL_EVIDENCE_ID.findall(preview)))
    if leaked:
        raise RuntimeError(
            "Operator-facing report leaked internal evidence IDs: "
            + ", ".join(leaked[:10])
        )
    for expected in (
        "GW_collectDebuginfo_demo.txt - 第",
        "AP_collectDebuginfo_demo.txt - 第",
    ):
        if expected not in preview:
            raise RuntimeError(f"Report preview omitted file/line evidence: {expected}")
    print("Report preview: file/line evidence present, internal IDs hidden")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Seed the synthetic AP frequent-offline GW/AP diagnosis demo.",
    )
    parser.add_argument(
        "--api-root", default="http://127.0.0.1:8000/api/v1",
        help="Running platform API root.",
    )
    parser.add_argument(
        "--job-timeout-seconds",
        type=int,
        default=1200,
        help="Maximum wait for each parse, triage, or diagnosis job (default: 1200).",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--approve-model-egress", action="store_true",
        help="Explicitly allow the synthetic logs to be sent to the active Chat model.",
    )
    mode.add_argument(
        "--local-only", action="store_true",
        help="Skip LLM log triage and use deterministic local diagnosis only.",
    )
    args = parser.parse_args()
    if not 30 <= args.job_timeout_seconds <= MAX_JOB_TIMEOUT_SECONDS:
        parser.error(
            "--job-timeout-seconds must be between 30 and "
            f"{MAX_JOB_TIMEOUT_SECONDS}"
        )
    api_root = args.api_root.rstrip("/")
    with httpx.Client(timeout=300) as client:
        client.get(f"{api_root}/health/live").raise_for_status()
        _verify_method_preconditions(client, api_root)
        case = client.post(f"{api_root}/cases", json={
            "title": "演示：AP频繁离线 - UDM进程反复异常",
            "device_type": "AP",
            "device_model": "DEMO-GW-9000 + DEMO-AP-6000",
            "firmware_version": "DEMO-V1.0.0",
            "topology": "主 GW（Parent 33）直连从 AP1，GW/AP 联合诊断。",
            "description": (
                "AP1 每约十分钟离线后恢复；需要判断物理链路、UDM 协议栈"
                "或网络传输问题，并给出文件行号证据。"
            ),
            "reproduction_steps": "观察三次 UDM 异常、心跳中断、GW 超时判离线和自动恢复。",
            "issue_time": "2026-08-28 10:00:00",
            "model_egress_approved": bool(args.approve_model_egress),
        }).raise_for_status().json()
        artifacts = [
            _upload_log(
                client, api_root, case["id"],
                SAMPLE_ROOT / "GW_collectDebuginfo_demo.txt", "GW", "PRIMARY",
                timeout_seconds=args.job_timeout_seconds,
            ),
            _upload_log(
                client, api_root, case["id"],
                SAMPLE_ROOT / "AP_collectDebuginfo_demo.txt", "AP", "SECONDARY",
                timeout_seconds=args.job_timeout_seconds,
            ),
        ]
        if args.approve_model_egress:
            for artifact in artifacts:
                submission = client.post(
                    f"{api_root}/cases/{case['id']}/artifacts/{artifact['id']}/triage"
                ).raise_for_status().json()
                _wait_job(
                    client, api_root, submission["job"]["id"],
                    timeout_seconds=args.job_timeout_seconds,
                )
                _verify_triage(client, api_root, case["id"], artifact)
        analysis_job = client.post(
            f"{api_root}/cases/{case['id']}/analyses"
        ).raise_for_status().json()
        _wait_job(
            client, api_root, analysis_job["id"],
            timeout_seconds=args.job_timeout_seconds,
        )
        analysis = client.get(f"{api_root}/cases/{case['id']}/analyses").raise_for_status().json()[0]
        result = json.loads(analysis["result_json"])
        coverage = result.get("diagnostic_planning", {}).get("fault_tree_coverage", {})
        _verify_diagnostic_planning(
            result, require_llm=bool(args.approve_model_egress),
        )
        _verify_fault_tree(result)
        _verify_hypotheses(result)
        _verify_report_preview(client, api_root, case["id"], analysis["id"])
        print("\nDemo verification")
        print(f"Case: {case['id']}")
        print(
            "Fault tree: "
            f"{coverage.get('attempted', 0)}/{coverage.get('total', 0)} attempted, "
            f"{coverage.get('concluded', 0)}/{coverage.get('total', 0)} concluded, "
            f"complete={coverage.get('complete')}"
        )
        print(f"Status counts: {coverage.get('status_counts', {})}")
        print(f"Hypotheses: {len(result.get('hypotheses', []))}")
        synthesis = result.get("synthesis_status") or {}
        synthesis_failure_code = (synthesis.get("failure") or {}).get("code")
        print(
            "Final synthesis: "
            f"{synthesis.get('mode') or 'UNKNOWN'}"
            + (f" ({synthesis_failure_code})" if synthesis_failure_code else "")
        )
        print(f"Open: http://127.0.0.1:5173/cases/{case['id']}")
        if (
            coverage.get("complete") is not True
            or int(coverage.get("total") or 0) == 0
            or not result.get("hypotheses")
        ):
            raise RuntimeError("Demo did not reach complete coverage with a hypothesis")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
