from __future__ import annotations

import re
from collections import Counter
from copy import deepcopy
from typing import Any

from app.services.diagnostic_methods import DiagnosticPattern
from app.services.diagnostic_planning_coverage import coverage_snapshot
from app.services.diagnosis_rules import heartbeat_timeout_confirmed
from app.services.fault_tree_coverage import FaultTreeCoverageItem


_SIGNALS: dict[str, tuple[str, ...]] = {
    "offline": (
        "status=[0]", "delaptopotree", "recv offline event",
        "apinst offline", "gw_ap_offline_detected", "gw_ap_topology_offline",
    ),
    "parent": (),
    "reachable": (" reachable ", "link is up"),
    "link_detection_failure": ("testlinkok failed", "link detection failed"),
    "physical_link_failure": (
        "carrier lost", "no carrier", "physical link down", "port negotiation failed",
    ),
    "transport_failure": (
        "udp packet loss", "ssdp multicast blocked", "multicast blocked",
    ),
    "gateway_offline": ("src=ctrlpointverify", "recv offline event"),
    "heartbeat_timeout": ("heartbeat timeout",),
    "udn": ("udn[uuid:",),
    "udm_process": (
        "udm is abnormal!reset proc!", "process udm died",
        "watchdog restarting service udm", "proc not exist",
    ),
    "listen_port_failure": (
        "ap_udm_listen_port_failed", "listen port check failed",
        "listen port not exist",
    ),
    "listen_port_ready": ("listen ports 1900 and 37443 ready",),
    "heartbeat_send_failure": (
        "ap_udm_heartbeat_send_failed", "error sending alive advertisements",
    ),
    "heartbeat_send_success": ("upnpsendadvertisement success", "deviceaphandle:"),
    "protocol_v2": ("libudm_rpc_adapt.so",),
    "protocol_v1": ("libhw_smp_udm_api.so",),
}
_COMMON_TOKENS = frozenset({
    "ap", "gw", "udm", "log", "info", "debug", "error", "warn", "notice",
    "status", "failed", "abnormal", "display", "device", "process", "check",
})
_CASE_EVIDENCE_SOURCE_TYPES = frozenset({
    "local_derived_evidence", "log_event", "log_triage_match",
})
_COLON_MAC = re.compile(
    r"(?<![0-9a-f])([0-9a-f]{2}(?::[0-9a-f]{2}){5})(?![0-9a-f])",
    re.IGNORECASE,
)
_UDN_MAC_SUFFIX = re.compile(
    r"udn\[uuid:[^\]\r\n]*?([0-9a-f]{12})\]",
    re.IGNORECASE,
)
_PARENT_RELATION = re.compile(
    r"\bparent(?:\s+apinstid)?\s*:\s*(?:1|33)\b",
    re.IGNORECASE,
)


def is_case_log_evidence(item: dict[str, Any]) -> bool:
    return str(item.get("source_type") or "") in _CASE_EVIDENCE_SOURCE_TYPES


def _evidence_text(item: dict[str, Any]) -> str:
    return "\n".join(str(item.get(key) or "") for key in (
        "content", "raw_text", "pattern_text", "meaning", "reason", "event_code",
        "title", "source_file",
    )).casefold()


def _matching_evidence(
    evidence: list[dict[str, Any]],
    phrases: tuple[str, ...] | list[str],
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    normalized = [phrase.casefold() for phrase in phrases if len(phrase.strip()) >= 3]
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in evidence:
        evidence_id = str(item.get("evidence_id") or "")
        if not evidence_id or evidence_id in seen:
            continue
        haystack = f" {_evidence_text(item)} "
        if any(phrase in haystack for phrase in normalized):
            seen.add(evidence_id)
            result.append(item)
            if len(result) >= limit:
                break
    return result


def _signal_evidence(
    evidence: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    signals = {
        name: _matching_evidence(evidence, list(phrases))
        for name, phrases in _SIGNALS.items()
    }
    signals["parent"] = [
        item for item in evidence if _PARENT_RELATION.search(_evidence_text(item))
    ]
    neighbor_offline = [
        item for item in evidence
        if "neighborlist" in _evidence_text(item)
        and "offline" in _evidence_text(item)
    ]
    signals["offline"] = _combine(signals["offline"], neighbor_offline)
    # A lone lastEventTime/curTime field is ordinary heartbeat state, not proof
    # of a timeout. Require the complete arithmetic tuple (or the parser's
    # typed event code) before allowing it to support a fault-tree conclusion.
    compound_timeouts = [
        item for item in evidence
        if heartbeat_timeout_confirmed(_evidence_text(item))
    ]
    signals["heartbeat_timeout"] = _combine(
        signals["heartbeat_timeout"], compound_timeouts,
    )
    udn_values: dict[str, list[dict[str, Any]]] = {}
    mac_values: dict[str, list[dict[str, Any]]] = {}
    for item in evidence:
        text = _evidence_text(item)
        for match in _UDN_MAC_SUFFIX.finditer(text):
            udn_values.setdefault(match.group(1).casefold(), []).append(item)
        for match in _COLON_MAC.finditer(text):
            mac_values.setdefault(match.group(1).replace(":", "").casefold(), []).append(item)
    matched_values = set(udn_values) & set(mac_values)
    matched_groups = [
        _combine(udn_values[value], mac_values[value])
        for value in sorted(matched_values)
    ] if udn_values and set(udn_values).issubset(matched_values) else []
    raw_matches = _combine(*matched_groups) if matched_groups else []
    derived_matches = _matching_evidence(evidence, ["UDN_AP_MAC_MATCH"])
    signals["udn_mac_match"] = _combine(raw_matches, derived_matches)
    return signals


def _direct_item_evidence(
    item: FaultTreeCoverageItem,
    evidence: list[dict[str, Any]],
    patterns: list[DiagnosticPattern],
) -> list[dict[str, Any]]:
    hints = [
        hint.strip().strip("`[]") for hint in item.evidence_hints
        if len(hint.strip().strip("`[]")) >= 3
    ]
    item_text = f"{item.label}\n{item.description}".casefold()
    related_pattern_ids: set[str] = set()
    for pattern in patterns:
        if pattern.document_id != item.method_document_id:
            continue
        pattern_text = pattern.text.casefold()
        tokens = [
            token for token in re.findall(
                r"[a-z_][a-z0-9_.:/!-]{2,}", pattern_text,
            )
            if token not in _COMMON_TOKENS
        ]
        if pattern_text in item_text or sum(token in item_text for token in tokens) >= 2:
            related_pattern_ids.add(pattern.id)

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for evidence_item in evidence:
        evidence_id = str(evidence_item.get("evidence_id") or "")
        if not evidence_id or evidence_id in seen:
            continue
        haystack = _evidence_text(evidence_item)
        direct_pattern = str(evidence_item.get("pattern_id") or "") in related_pattern_ids
        hint_matches = sum(hint.casefold() in haystack for hint in hints)
        if direct_pattern or hint_matches:
            seen.add(evidence_id)
            result.append(evidence_item)
            if len(result) >= 20:
                break
    return result


def _combine(*groups: list[dict[str, Any]], limit: int = 30) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            evidence_id = str(item.get("evidence_id") or "")
            if evidence_id and evidence_id not in seen:
                seen.add(evidence_id)
                result.append(item)
                if len(result) >= limit:
                    return result
    return result


def _authored_branch_evidence(
    label: str,
    body: str,
    signals: dict[str, list[dict[str, Any]]],
) -> tuple[bool, list[dict[str, Any]]]:
    if label.startswith("步骤1") or "ap是否离线" in body:
        return True, signals["offline"]
    if label.startswith("步骤2") or "parent判断" in body:
        return True, signals["parent"]
    if label in {"步骤3", "3"} or "物理链路问题还是协议层问题" in body:
        return True, _combine(
            signals["reachable"], signals["udm_process"],
            signals["heartbeat_timeout"],
        )
    branches = (
        (label == "3.1" or "物理链路是否通" in body, signals["reachable"]),
        (
            label == "3.2" or "链路检测" in body,
            _combine(
                signals["link_detection_failure"],
                signals["physical_link_failure"],
                signals["transport_failure"],
            ),
        ),
        (
            label.startswith("步骤4") or "udm协议栈问题还是ap自身问题" in body,
            _combine(signals["gateway_offline"], signals["heartbeat_timeout"]),
        ),
        (label == "4.1" or "udm检测离线" in body, signals["gateway_offline"]),
        (label == "4.2" or "心跳超时" in body, signals["heartbeat_timeout"]),
        (label == "4.3" or "udn最后" in body, signals["udn_mac_match"]),
        (
            label.startswith("步骤5") or "定位ap侧根因" in body,
            _combine(signals["udm_process"], signals["listen_port_failure"]),
        ),
        (label == "5.1" or "udm进程异常" in body, signals["udm_process"]),
        (
            label == "5.2" or "监听端口异常" in body,
            signals["listen_port_failure"],
        ),
        (
            label == "5.3" or "协议版本" in body or label == "so库",
            _combine(signals["protocol_v2"], signals["protocol_v1"]),
        ),
        (label == "端口" and "待确认" in body, []),
        (
            label == "端口",
            _combine(signals["listen_port_failure"], signals["listen_port_ready"]),
        ),
        (
            label == "心跳" and "待确认" in body,
            [],
        ),
        (
            label == "心跳",
            _combine(
                signals["heartbeat_send_failure"],
                signals["heartbeat_send_success"],
                signals["heartbeat_timeout"],
            ),
        ),
    )
    return next(((True, selected) for matches, selected in branches if matches), (False, []))


def _root_cause_resolution(
    item: FaultTreeCoverageItem,
    label: str,
    body: str,
    signals: dict[str, list[dict[str, Any]]],
) -> tuple[str, list[dict[str, Any]]] | None:
    if item.category != "ROOT_CAUSE":
        return None
    if "物理链路" in body or label.startswith("场景1"):
        if signals["physical_link_failure"]:
            return "SUPPORTED", signals["physical_link_failure"]
        if signals["reachable"] and (
            signals["udm_process"] or signals["heartbeat_send_failure"]
        ):
            return "EXCLUDED", _combine(
                signals["reachable"], signals["udm_process"],
            )
        return "INSUFFICIENT_EVIDENCE", []
    if "udm协议栈" in body or label.startswith("场景2"):
        ap_side = _combine(
            signals["udm_process"], signals["listen_port_failure"],
        )
        heartbeat = _combine(
            signals["heartbeat_send_failure"], signals["heartbeat_timeout"],
        )
        gateway = _combine(signals["gateway_offline"], signals["offline"])
        selected = _combine(ap_side, heartbeat, gateway)
        status = "SUPPORTED" if ap_side and heartbeat and gateway else "INSUFFICIENT_EVIDENCE"
        return status, selected
    if "网络传输" in body or label.startswith("场景3"):
        return (
            ("SUPPORTED", signals["transport_failure"])
            if signals["transport_failure"]
            else ("INSUFFICIENT_EVIDENCE", [])
        )
    return None


def _item_resolution(
    item: FaultTreeCoverageItem,
    *,
    direct: list[dict[str, Any]],
    signals: dict[str, list[dict[str, Any]]],
) -> tuple[str, list[dict[str, Any]], str, str, bool]:
    label = re.sub(r"\s+", "", item.label).casefold()
    body = f"{item.label}\n{item.description}\n{' '.join(item.evidence_hints)}".casefold()
    root_resolution = _root_cause_resolution(item, label, body, signals)
    if root_resolution is not None:
        status, selected = root_resolution
        authoritative = True
    else:
        matched, authored = _authored_branch_evidence(label, body, signals)
        selected = authored if matched else direct
        status = "SUPPORTED" if selected else "INSUFFICIENT_EVIDENCE"
        authoritative = matched

    evidence_ids = [str(entry["evidence_id"]) for entry in selected[:30]]
    if status == "SUPPORTED":
        if label == "4.3" or "udn最后" in body:
            rationale = (
                "本地确定性比较已确认 UDN UUID 末 12 位与 AP MAC 的规范化值一致；"
                f"关联 {len(evidence_ids)} 条当前案例日志证据。"
            )
        else:
            rationale = (
                f"确定性证据扫描按《{item.document_title}》节点“{item.label}”"
                f"命中 {len(evidence_ids)} 条当前案例日志证据。"
            )
        next_action = ""
    elif status == "EXCLUDED":
        rationale = (
            f"当前案例存在 {len(evidence_ids)} 条正向反证；按《{item.document_title}》"
            f"节点“{item.label}”可排除该分支。"
        )
        next_action = ""
    else:
        if label in {"端口", "心跳"} and "待确认" in body:
            rationale = (
                f"《{item.document_title}》将该协议版本判断项标注为“待确认”；"
                "当前日志只能说明运行现象，不能据此断言版本差异。"
            )
        else:
            rationale = (
                f"已按《{item.document_title}》节点“{item.label}”完成本地证据扫描，"
                "现有日志不足以支持或排除该节点。"
            )
        hints = "、".join(item.evidence_hints[:5])
        next_action = (
            f"补采并核验以下信号：{hints}。" if hints
            else "按该节点说明补采 GW/AP 双侧日志、状态或抓包证据。"
        )
    return status, selected, rationale, next_action, authoritative


def complete_fault_tree_with_deterministic_evidence(
    coverage: dict[str, Any] | dict[str, dict[str, Any]],
    *,
    items: list[FaultTreeCoverageItem],
    evidence: list[dict[str, Any]],
    patterns: list[DiagnosticPattern],
    round_number: int,
    reason: str,
    fallback_applied: bool = True,
) -> dict[str, Any]:
    """Finish every tree node with real evidence or an explicit evidence gap."""

    case_evidence = [
        item for item in evidence if is_case_log_evidence(item)
    ]
    existing_items = coverage.get("items") if isinstance(coverage, dict) else None
    if isinstance(existing_items, list):
        current = {
            str(entry.get("id")): deepcopy(entry)
            for entry in existing_items if isinstance(entry, dict) and entry.get("id")
        }
    else:
        current = {
            str(item_id): deepcopy(entry)
            for item_id, entry in coverage.items()
            if isinstance(entry, dict)
        }
    item_by_id = {item.id: item for item in items}
    signals = _signal_evidence(case_evidence)
    case_evidence_ids = {
        str(item.get("evidence_id")) for item in case_evidence
        if item.get("evidence_id")
    }
    source_counts: Counter[str] = Counter()
    for item_id, item in item_by_id.items():
        previous = current.get(item_id, item.public_snapshot())
        valid_previous_ids = [
            str(value) for value in previous.get("evidence_ids", [])
            if str(value) in case_evidence_ids
        ]
        direct = _direct_item_evidence(item, case_evidence, patterns)
        status, selected, rationale, next_action, authoritative = _item_resolution(
            item, direct=direct, signals=signals,
        )
        selected_ids = {
            str(entry.get("evidence_id") or "")
            for entry in selected
            if entry.get("evidence_id")
        }
        node_relevant_previous_ids = [
            evidence_id for evidence_id in valid_previous_ids
            if evidence_id in selected_ids
        ]
        previous_status = str(previous.get("status") or "PENDING")
        if (
            previous_status in {"SUPPORTED", "EXCLUDED"}
            and valid_previous_ids
            and previous.get("attempted")
            and previous_status == status
            and (authoritative or node_relevant_previous_ids)
        ):
            previous["evidence_ids"] = (
                [str(entry["evidence_id"]) for entry in selected[:30]]
                if authoritative and selected
                else node_relevant_previous_ids
            )
            if authoritative and (
                item.label == "4.3"
                or "UDN最后" in item.description
            ):
                previous["rationale"] = rationale
            previous["resolution_source"] = (
                "LLM_DETERMINISTIC_CROSS_CHECKED"
                if authoritative else "LLM_EVIDENCE_GATED"
            )
            source_counts[previous["resolution_source"]] += 1
            current[item_id] = previous
            continue
        current[item_id] = {
            **item.public_snapshot(),
            "status": status,
            "rationale": rationale,
            "evidence_ids": [
                str(entry["evidence_id"]) for entry in selected[:30]
            ],
            "next_action": next_action,
            "attempted": True,
            "last_round": max(round_number, int(previous.get("last_round") or 0)),
            "resolution_source": "DETERMINISTIC_METHOD_EVIDENCE",
        }
        source_counts["DETERMINISTIC_METHOD_EVIDENCE"] += 1
    snapshot = coverage_snapshot(current)
    snapshot["fallback_applied"] = fallback_applied
    snapshot["fallback_reason"] = reason
    snapshot["resolution_source_counts"] = dict(source_counts)
    return snapshot


def merge_fault_tree_findings_into_diagnosis(
    result: dict[str, Any],
    coverage: dict[str, Any],
) -> dict[str, Any]:
    """Expose evidence-backed authored root-cause branches in the safe baseline."""

    supported_roots = [
        item for item in coverage.get("items", [])
        if isinstance(item, dict)
        and item.get("category") == "ROOT_CAUSE"
        and item.get("status") == "SUPPORTED"
        and item.get("evidence_ids")
    ]
    result["fault_tree_findings"] = [
        {
            "item_id": item["id"],
            "method_document_id": item["method_document_id"],
            "document_title": item.get("document_title"),
            "label": item.get("label"),
            "status": item.get("status"),
            "rationale": item.get("rationale"),
            "evidence_ids": item.get("evidence_ids", []),
            "next_action": item.get("next_action", ""),
        }
        for item in coverage.get("items", [])
        if isinstance(item, dict)
    ]
    hypotheses = list(result.get("hypotheses", []))
    recommendations = list(result.get("recommended_actions", []))
    for item in supported_roots:
        item_text = f"{item.get('label')} {item.get('description')}".casefold()
        duplicate = any(
            "udm" in item_text
            and "udm" in f"{entry.get('title')} {entry.get('description')}".casefold()
            for entry in hypotheses
        )
        if not duplicate:
            hypotheses.append({
                "rank": 0,
                "title": f"故障树支持：{item.get('label')}",
                "description": item.get("rationale") or item.get("description") or "",
                "supporting_evidence": list(item.get("evidence_ids", []))[:30],
                "contradicting_evidence": [],
                "confidence_score": min(
                    0.94, 0.72 + len(item.get("evidence_ids", [])) * 0.02,
                ),
                "confidence_level": "HIGH",
                "priority": "P1",
                "needs_human_review": True,
                "event_code": "FAULT_TREE_ROOT_CAUSE",
            })
        if "udm" in item_text:
            for action in (
                "保存 AP 侧 UDM 进程的 core dump/backtrace，并核对异常前后的资源状态。",
                "检查 AP 侧 UDM 进程及 1900/37443 监听端口，确认重启后端口持续可用。",
                "按同一 AP 标识对齐 AP 心跳发送失败与 GW 心跳超时、拓扑离线的时间序列。",
            ):
                if not any(entry.get("action") == action for entry in recommendations):
                    recommendations.append({
                        "priority": "P1",
                        "action": action,
                        "reason": f"验证《{item.get('document_title')}》支持的根因分支。",
                        "expected_result": "获得可支持或推翻 UDM 根因链的复现、进程及双侧日志证据。",
                    })
    hypotheses.sort(key=lambda entry: float(entry.get("confidence_score") or 0), reverse=True)
    for rank, hypothesis in enumerate(hypotheses, start=1):
        hypothesis["rank"] = rank
    result["hypotheses"] = hypotheses
    result["recommended_actions"] = recommendations
    return result


def reconcile_synthesis_with_fault_tree(
    result: dict[str, Any],
    coverage: dict[str, Any],
) -> dict[str, Any]:
    """Remove final-synthesis claims that contradict completed local checks."""

    identity_item = next((
        item for item in coverage.get("items", [])
        if isinstance(item, dict)
        and str(item.get("label") or "").strip() == "4.3"
        and item.get("status") == "SUPPORTED"
        and item.get("evidence_ids")
    ), None)
    if identity_item is None:
        return result

    def asks_for_identity_mapping(value: object) -> bool:
        text = str(value or "").casefold()
        return (
            "udn" in text
            and "mac" in text
            and any(token in text for token in ("映射", "匹配", "对比", "末段"))
        )

    result["missing_information"] = [
        item for item in result.get("missing_information", [])
        if not asks_for_identity_mapping(item)
    ]
    facts = list(result.get("confirmed_facts", []))
    if not any(asks_for_identity_mapping(item.get("statement")) for item in facts):
        facts.append({
            "statement": "本地确定性检查确认 UDN UUID 末 12 位与 AP MAC 规范化值一致。",
            "evidence_ids": list(identity_item.get("evidence_ids", []))[:30],
        })
    result["confirmed_facts"] = facts
    return result
