from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path

import pytest

from app.models import Artifact, Case
from app.services.diagnostic_fault_tree_baseline import (
    complete_fault_tree_with_deterministic_evidence,
    merge_fault_tree_findings_into_diagnosis,
    reconcile_synthesis_with_fault_tree,
)
from app.services.diagnostic_methods import (
    DiagnosticMethodDocument,
    compile_diagnostic_patterns,
)
from app.services.diagnostic_local_evidence import derive_case_local_evidence
from app.services.diagnostic_planning_contract import (
    PlanningRound,
    validate_planning_round,
)
from app.services.diagnostic_planning_coverage import initial_fault_tree_coverage
from app.services.diagnostic_planning_repair import repair_planning_payload
from app.services.diagnosis_rules import (
    HYPOTHESIS_RULES,
    heartbeat_timeout_confirmed,
)
from app.services.fault_tree_coverage import (
    FaultTreeCoverageItem,
    compile_fault_tree_items,
)
from app.services.log_parsers import HuaweiCollectDebugInfoParser


SYNTHETIC_TREE = """# Synthetic AP offline tree

```
│ 步骤1: inspect topology with `Status=[0]`
│ 步骤2: inspect connection relation with `Parent: 33` or `Parent: 1`
│ 场景1: physical link problem; check `REACHABLE` or `TestLinkOK failed`
│ 场景2: UDM protocol stack problem; check `udm Is Abnormal!Reset Proc!`
│ 场景3: network transport problem; check `TestLinkOK failed`
```

| 判断点 | 日志关键字 | 判断结论 |
| --- | --- | --- |
| AP offline | `Status=[0]` | AP is offline |
| UDM process | `udm Is Abnormal!Reset Proc!` | UDM failed |
"""

IDENTITY_AND_VERSION_TREE = """# Synthetic identity and protocol checks

```
│ 4.3 UDN最后一串即AP的MAC地址
│ - UDN[uuid:00e0fc37-2525-2828-2500-020000000101]
```

| 判断方法 | 1.0协议 | 2.0协议 |
| --- | --- | --- |
| 端口 | `1900` + `37443` | 差异待确认 |
| 心跳 | `SSDP Advertise` | 待确认 |
"""


def _method(
    document_id: str,
    content: str,
    *,
    role: str = "FAULT_TREE",
) -> DiagnosticMethodDocument:
    return DiagnosticMethodDocument(
        id=document_id,
        title="Synthetic AP offline method",
        source_type="fault_tree" if role == "FAULT_TREE" else "analysis_skill",
        version=1,
        device_type="GENERAL",
        module="SMARTLINK",
        content=content,
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        role=role,
    )


def _evidence(evidence_id: str, content: str, source_file: str, line: int) -> dict:
    return {
        "evidence_id": evidence_id,
        "source_type": "log_triage_match",
        "content": content,
        "source_file": source_file,
        "line_start": line,
        "line_end": line,
    }


def test_deterministic_fault_tree_baseline_concludes_every_node_from_real_evidence() -> None:
    method = _method("DOC-synthetic-tree", SYNTHETIC_TREE)
    items = compile_fault_tree_items([method])
    evidence = [
        _evidence("EV-GW-OFFLINE", "Topo, apInst=[1] Status=[0]", "gw.txt", 20),
        _evidence("EV-GW-LINK", "LAN3 REACHABLE 02:00:00:00:01:01 192.0.2.10", "gw.txt", 21),
        _evidence("EV-GW-TIMEOUT", "[Abnormal] curTime[114598], iAdvrTimeOut[250], lastEventTime[114277]", "gw.txt", 22),
        _evidence("EV-GW-CTRL", "ApInst:1 Recv Offline Event, src=CtrlPointVerify", "gw.txt", 23),
        _evidence("EV-AP-SEND", "Error sending alive advertisements : -5", "ap.txt", 12),
        _evidence("EV-AP-PORT", "dynamic:[udm] listen port check failed", "ap.txt", 13),
        _evidence("EV-AP-PROC", "udm Is Abnormal!Reset Proc! [Proc Not Exist]", "ap.txt", 14),
    ]

    coverage = complete_fault_tree_with_deterministic_evidence(
        initial_fault_tree_coverage(items),
        items=items,
        evidence=evidence,
        patterns=compile_diagnostic_patterns([method]),
        round_number=1,
        reason="SYNTHETIC_MODEL_FAILURE",
    )

    roots = {
        str(item["label"]): item
        for item in coverage["items"]
        if item["category"] == "ROOT_CAUSE"
    }
    assert coverage["complete"] is True
    assert coverage["attempted"] == coverage["total"] == len(items)
    assert coverage["concluded"] == coverage["total"]
    assert roots["场景1"]["status"] == "EXCLUDED"
    assert roots["场景2"]["status"] == "SUPPORTED"
    assert roots["场景3"]["status"] == "INSUFFICIENT_EVIDENCE"
    known_ids = {item["evidence_id"] for item in evidence}
    assert all(
        set(item["evidence_ids"]).issubset(known_ids)
        for item in coverage["items"]
    )

    result = merge_fault_tree_findings_into_diagnosis(
        {"hypotheses": [], "recommended_actions": []}, coverage,
    )
    assert any("场景2" in item["title"] for item in result["hypotheses"])
    assert any("1900/37443" in item["action"] for item in result["recommended_actions"])


def test_healthy_udm_state_is_not_promoted_to_failure_evidence() -> None:
    method = _method("DOC-healthy-state-tree", SYNTHETIC_TREE)
    items = compile_fault_tree_items([method])
    evidence = [
        _evidence("EV-OFFLINE", "Topo, apInst=[1] Status=[0]", "gw.txt", 20),
        _evidence(
            "EV-GATEWAY", "ApInst:1 Recv Offline Event, src=CtrlPointVerify",
            "gw.txt", 21,
        ),
        _evidence(
            "EV-NORMAL-TIME",
            "[Abnormal] curTime[114300], iAdvrTimeOut[250], lastEventTime[114277]",
            "gw.txt", 22,
        ),
        _evidence(
            "EV-READY", "udm process restart completed, listen ports 1900 and 37443 ready",
            "ap.txt", 23,
        ),
        _evidence(
            "EV-ALIVE", "deviceApHandle:1 alive; UpnpSendAdvertisement success",
            "ap.txt", 24,
        ),
    ]

    coverage = complete_fault_tree_with_deterministic_evidence(
        initial_fault_tree_coverage(items),
        items=items,
        evidence=evidence,
        patterns=compile_diagnostic_patterns([method]),
        round_number=1,
        reason="HEALTHY_SIGNALS_ARE_NOT_FAILURES",
    )
    by_label = {item["label"]: item for item in coverage["items"]}

    assert by_label["场景2"]["status"] == "INSUFFICIENT_EVIDENCE"
    assert not any(
        evidence_id in {"EV-NORMAL-TIME", "EV-READY", "EV-ALIVE"}
        for evidence_id in by_label["场景2"]["evidence_ids"]
    )


def test_link_failure_classes_do_not_blur_root_causes() -> None:
    method = _method("DOC-link-classification-tree", SYNTHETIC_TREE)
    items = compile_fault_tree_items([method])
    scenarios = (
        (
            "RefreshTopoTree APInstId: 1 TestLinkOK failed",
            "INSUFFICIENT_EVIDENCE",
            "INSUFFICIENT_EVIDENCE",
        ),
        (
            "LAN3 carrier lost; no carrier",
            "SUPPORTED",
            "INSUFFICIENT_EVIDENCE",
        ),
        (
            "SSDP multicast blocked; UDP packet loss",
            "INSUFFICIENT_EVIDENCE",
            "SUPPORTED",
        ),
    )

    for index, (message, physical_status, transport_status) in enumerate(scenarios):
        coverage = complete_fault_tree_with_deterministic_evidence(
            initial_fault_tree_coverage(items),
            items=items,
            evidence=[
                _evidence(f"EV-LINK-{index}", message, "gw.txt", 30 + index),
            ],
            patterns=compile_diagnostic_patterns([method]),
            round_number=1,
            reason="LINK_FAILURE_CLASSIFICATION",
        )
        roots = {
            item["label"]: item
            for item in coverage["items"]
            if item["category"] == "ROOT_CAUSE"
        }
        assert roots["场景1"]["status"] == physical_status
        assert roots["场景3"]["status"] == transport_status


def test_topology_signal_matching_respects_negation_and_numeric_boundaries() -> None:
    method = _method("DOC-topology-boundaries", SYNTHETIC_TREE)
    items = compile_fault_tree_items([method])

    negative = complete_fault_tree_with_deterministic_evidence(
        initial_fault_tree_coverage(items),
        items=items,
        evidence=[
            _evidence(
                "EV-TOPOLOGY-NEGATIVE",
                "Topo, apInst=[1] Status=[1]; no offline event; Parent: 10",
                "gw.txt",
                40,
            ),
        ],
        patterns=compile_diagnostic_patterns([method]),
        round_number=1,
        reason="TOPOLOGY_BOUNDARIES",
    )
    negative_by_label = {item["label"]: item for item in negative["items"]}
    assert negative_by_label["步骤1"]["status"] == "INSUFFICIENT_EVIDENCE"
    assert negative_by_label["步骤2"]["status"] == "INSUFFICIENT_EVIDENCE"

    positive = complete_fault_tree_with_deterministic_evidence(
        initial_fault_tree_coverage(items),
        items=items,
        evidence=[
            _evidence(
                "EV-TOPOLOGY-POSITIVE",
                "Topo, apInst=[1] Status=[0]; Parent: 1",
                "gw.txt",
                41,
            ),
        ],
        patterns=compile_diagnostic_patterns([method]),
        round_number=1,
        reason="TOPOLOGY_BOUNDARIES",
    )
    positive_by_label = {item["label"]: item for item in positive["items"]}
    assert positive_by_label["步骤1"]["status"] == "SUPPORTED"
    assert positive_by_label["步骤2"]["status"] == "SUPPORTED"


def test_planning_repair_downgrades_unsupported_claim_and_adds_read_only_bindings() -> None:
    case = Case(
        id="CASE-repair", title="AP频繁离线", description="AP频繁离线",
        device_type="AP",
    )
    tree = _method("DOC-tree", SYNTHETIC_TREE)
    log_method = _method(
        "DOC-log",
        "# Log analysis\n\n- `listen port check failed` means the UDM port is unavailable.",
        role="LOG_ANALYSIS_METHOD",
    )
    methods = [tree, log_method]
    patterns = compile_diagnostic_patterns(methods)
    items = compile_fault_tree_items(methods)
    raw = {
        "method_assessments": [{
            "method_document_id": tree.id,
            "relevance": "RELEVANT",
            "rationale": "The symptom overlaps the tree",
        }],
        "checks": [],
        "tool_calls": [],
        "fault_tree_assessments": [{
            "item_id": items[0].id,
            "method_document_id": tree.id,
            "status": "SUPPORTED",
            "rationale": "The model claimed support without a case evidence ID",
            "evidence_ids": [],
        }],
        "continue_analysis": True,
    }

    repaired, repairs = repair_planning_payload(
        raw,
        round_number=1,
        case=case,
        methods=methods,
        diagnostic_patterns=patterns,
        fault_tree_items=items,
        symptom_relevant_method_ids={tree.id},
        unattempted_fault_tree_item_ids={item.id for item in items},
        valid_evidence_ids=set(),
    )
    parsed = PlanningRound.model_validate(repaired)
    validate_planning_round(
        parsed,
        round_number=1,
        case=case,
        methods=methods,
        fault_tree_items=items,
        diagnostic_patterns=patterns,
        unattempted_fault_tree_item_ids={item.id for item in items},
        valid_evidence_ids=set(),
    )

    assert parsed.fault_tree_assessments[0].status == "INSUFFICIENT_EVIDENCE"
    assert parsed.fault_tree_assessments[0].evidence_ids == []
    assert {item.method_document_id for item in parsed.method_assessments} == {
        tree.id, log_method.id,
    }
    assert any(call.tool_name == "search_log" for call in parsed.tool_calls)
    assert {
        "UNSUPPORTED_CONCLUSIONS_DOWNGRADED",
        "EXECUTABLE_CHECKS_ADDED",
        "READ_ONLY_EVIDENCE_BINDINGS_ADDED",
    }.issubset({item["code"] for item in repairs})


def test_deterministic_identity_check_requires_matching_udn_and_ap_mac() -> None:
    method = _method("DOC-identity-tree", IDENTITY_AND_VERSION_TREE)
    items = compile_fault_tree_items([method])
    evidence = [
        _evidence(
            "EV-UDN", "UDN[uuid:00e0fc37-2525-2828-2500-020000000101]",
            "gw.txt", 20,
        ),
        _evidence(
            "EV-MAC", "LAN3 REACHABLE 02:00:00:00:01:01 192.0.2.10",
            "gw.txt", 21,
        ),
        _evidence(
            "EV-PORT", "listen ports 1900 and 37443 ready",
            "ap.txt", 22,
        ),
        _evidence(
            "EV-HEARTBEAT", "UpnpSendAdvertisement success",
            "ap.txt", 23,
        ),
    ]

    coverage = complete_fault_tree_with_deterministic_evidence(
        initial_fault_tree_coverage(items),
        items=items,
        evidence=evidence,
        patterns=compile_diagnostic_patterns([method]),
        round_number=1,
        reason="IDENTITY_CHECK",
    )
    by_label = {item["label"]: item for item in coverage["items"]}
    assert by_label["4.3"]["status"] == "SUPPORTED"
    assert set(by_label["4.3"]["evidence_ids"]) == {"EV-UDN", "EV-MAC"}
    assert "规范化值一致" in by_label["4.3"]["rationale"]
    assert by_label["端口"]["status"] == "INSUFFICIENT_EVIDENCE"
    assert by_label["心跳"]["status"] == "INSUFFICIENT_EVIDENCE"

    mismatched = [
        evidence[0],
        _evidence(
            "EV-OTHER-MAC", "LAN3 REACHABLE 02:00:00:00:09:99 192.0.2.10",
            "gw.txt", 24,
        ),
    ]
    mismatch_coverage = complete_fault_tree_with_deterministic_evidence(
        initial_fault_tree_coverage(items),
        items=items,
        evidence=mismatched,
        patterns=compile_diagnostic_patterns([method]),
        round_number=1,
        reason="IDENTITY_CHECK",
    )
    mismatch_by_label = {
        item["label"]: item for item in mismatch_coverage["items"]
    }
    assert mismatch_by_label["4.3"]["status"] == "INSUFFICIENT_EVIDENCE"

    partially_matched = [
        *evidence[:2],
        _evidence(
            "EV-SECOND-UDN",
            "UDN[uuid:00e0fc37-2525-2828-2500-020000000202]",
            "gw.txt", 25,
        ),
    ]
    partial_coverage = complete_fault_tree_with_deterministic_evidence(
        initial_fault_tree_coverage(items),
        items=items,
        evidence=partially_matched,
        patterns=compile_diagnostic_patterns([method]),
        round_number=1,
        reason="IDENTITY_CHECK",
    )
    partial_by_label = {item["label"]: item for item in partial_coverage["items"]}
    assert partial_by_label["4.3"]["status"] == "INSUFFICIENT_EVIDENCE"


def test_synthesis_reconciliation_removes_completed_identity_gap() -> None:
    result = {
        "confirmed_facts": [],
        "missing_information": [
            "UDN uuid末段与AP实际MAC的精确映射仍需确认",
            "仍需采集 core dump 以定位进程崩溃原因",
        ],
    }
    coverage = {
        "items": [{
            "label": "4.3",
            "status": "SUPPORTED",
            "evidence_ids": ["EV-UDN", "EV-MAC"],
        }],
    }

    reconciled = reconcile_synthesis_with_fault_tree(result, coverage)

    assert reconciled["missing_information"] == [
        "仍需采集 core dump 以定位进程崩溃原因",
    ]
    assert reconciled["confirmed_facts"] == [{
        "statement": "本地确定性检查确认 UDN UUID 末 12 位与 AP MAC 规范化值一致。",
        "evidence_ids": ["EV-UDN", "EV-MAC"],
    }]


def test_local_identity_derivation_compares_raw_values_without_exposing_them(
    tmp_path: Path,
) -> None:
    path = tmp_path / "synthetic.txt"
    path.write_text(
        "UDN[uuid:00e0fc37-2525-2828-2500-020000000101]\n"
        "LAN3 REACHABLE 02:00:00:00:01:01 192.0.2.10\n",
        encoding="utf-8",
    )
    case = Case(id="CASE-local-derived", title="AP频繁离线", device_type="AP")
    artifact = Artifact(
        id="ART-local-derived",
        case_id=case.id,
        original_name=path.name,
        stored_path=str(path),
        sha256="0" * 64,
        size_bytes=path.stat().st_size,
        source_device_type="GW",
        source_device_role="PRIMARY",
    )

    evidence = derive_case_local_evidence(case, [artifact])

    assert len(evidence) == 1
    assert evidence[0]["event_code"] == "UDN_AP_MAC_MATCH"
    serialized = str(evidence[0])
    assert "020000000101" not in serialized
    assert "02:00:00:00:01:01" not in serialized
    assert evidence[0]["metadata"]["comparison_complete"] is True


def test_deterministic_baseline_never_uses_knowledge_as_case_evidence() -> None:
    method = _method("DOC-no-knowledge-evidence", SYNTHETIC_TREE)
    items = compile_fault_tree_items([method])
    knowledge_only = [{
        **_evidence(
            "MEM-method", "TestLinkOK failed; UDM process abnormal",
            "fault-tree.md", 1,
        ),
        "source_type": "memory_procedural",
    }]

    coverage = complete_fault_tree_with_deterministic_evidence(
        initial_fault_tree_coverage(items),
        items=items,
        evidence=knowledge_only,
        patterns=compile_diagnostic_patterns([method]),
        round_number=1,
        reason="KNOWLEDGE_IS_NOT_CASE_EVIDENCE",
    )

    assert all(item["status"] == "INSUFFICIENT_EVIDENCE" for item in coverage["items"])
    assert not any(item["evidence_ids"] for item in coverage["items"])


@pytest.mark.parametrize("previous_status", ["SUPPORTED", "EXCLUDED"])
def test_non_authoritative_node_rejects_unrelated_case_evidence(
    previous_status: str,
) -> None:
    method = _method(
        "DOC-auth-node",
        "# Authentication diagnosis\n\n- `EAP handshake rejected`",
    )
    item = FaultTreeCoverageItem(
        id="FTITEM-auth-node",
        method_document_id=method.id,
        document_title=method.title,
        category="DECISION",
        label="认证检查",
        description="检查 EAP handshake rejected",
        section="Authentication diagnosis",
        line_start=3,
        evidence_hints=["EAP handshake rejected"],
    )
    coverage = initial_fault_tree_coverage([item])
    coverage[item.id].update({
        "status": previous_status,
        "rationale": "Model attached an unrelated current-case event",
        "evidence_ids": ["LEM-unrelated"],
        "attempted": True,
        "last_round": 1,
    })

    resolved = complete_fault_tree_with_deterministic_evidence(
        coverage,
        items=[item],
        evidence=[_evidence(
            "LEM-unrelated",
            "Topo, apInst=[1] Status=[0]",
            "GW.txt",
            9,
        )],
        patterns=compile_diagnostic_patterns([method]),
        round_number=2,
        reason="UNRELATED_CASE_EVIDENCE",
        fallback_applied=False,
    )["items"][0]

    assert resolved["status"] == "INSUFFICIENT_EVIDENCE"
    assert resolved["evidence_ids"] == []


def test_huawei_parser_emits_ap_offline_causal_chain_event_codes(tmp_path: Path) -> None:
    text = """Start run collect command:WAP:display debuglog info
ERROR 2026-08-28 10:00:01.000[33][UDM]Error sending alive advertisements : -5
DEBUG 2026-08-28 10:00:01.030[33][UDM][Debug] dynamic:[udm] listen port check failed
CRITICAL 2026-08-28 10:00:01.090[33][UDM][Critical] udm Is Abnormal!Reset Proc! [Proc Not Exist]
WARN 2026-08-28 10:04:30.100[33][UDM][Abnormal] curTime[114598], iAdvrTimeOut[250], lastEventTime[114277]
WARN 2026-08-28 10:04:30.120[545][WAP]ApInst:1 Recv Offline Event, src=CtrlPointVerify
WARN 2026-08-28 10:04:30.140[545][WAP]Topo, apInst=[1] Status=[0]
NOTICE 2026-08-28 10:04:31.000[90][LAN]LAN3 REACHABLE 02:00:00:00:01:01 192.0.2.10
"""
    path = tmp_path / "collectDebuginfo_synthetic"
    path.write_text(text, encoding="utf-8")
    events = HuaweiCollectDebugInfoParser().parse(path, path.name, text)
    codes = {event.event_code for event in events}

    assert {
        "AP_UDM_HEARTBEAT_SEND_FAILED",
        "AP_UDM_LISTEN_PORT_FAILED",
        "AP_UDM_PROCESS_ABNORMAL",
        "GW_AP_HEARTBEAT_TIMEOUT",
        "GW_AP_OFFLINE_DETECTED",
        "GW_AP_TOPOLOGY_OFFLINE",
        "AP_LINK_REACHABLE",
    }.issubset(codes)
    assert {
        "AP_UDM_HEARTBEAT_SEND_FAILED",
        "AP_UDM_LISTEN_PORT_FAILED",
        "AP_UDM_PROCESS_ABNORMAL",
        "GW_AP_HEARTBEAT_TIMEOUT",
    }.issubset(HYPOTHESIS_RULES)


def test_heartbeat_timeout_requires_the_fault_tree_arithmetic(tmp_path: Path) -> None:
    valid = "[Abnormal] curTime[114598], iAdvrTimeOut[250], lastEventTime[114277]"
    equal_boundary = "[Abnormal] curTime[114527], iAdvrTimeOut[250], lastEventTime[114277]"
    below_boundary = "[Abnormal] curTime[114300], iAdvrTimeOut[250], lastEventTime[114277]"
    wrapped_counter = "[Abnormal] curTime[10], iAdvrTimeOut[250], lastEventTime[114277]"

    assert heartbeat_timeout_confirmed(valid) is True
    assert heartbeat_timeout_confirmed(equal_boundary) is False
    assert heartbeat_timeout_confirmed(below_boundary) is False
    assert heartbeat_timeout_confirmed(wrapped_counter) is False

    text = (
        "WARN 2026-08-28 10:00:00.000[33][UDM]" + below_boundary + "\n"
    )
    path = tmp_path / "collectDebuginfo_non_timeout"
    path.write_text(text, encoding="utf-8")
    events = HuaweiCollectDebugInfoParser().parse(path, path.name, text)

    assert "GW_AP_HEARTBEAT_TIMEOUT" not in {
        event.event_code for event in events
    }


def test_tracked_ap_offline_demo_logs_preserve_the_expected_causal_chain() -> None:
    demo_root = Path(__file__).parents[2] / "sample_data" / "demo_ap_frequent_offline"
    events = []
    raw_logs: list[str] = []
    for filename in ("GW_collectDebuginfo_demo.txt", "AP_collectDebuginfo_demo.txt"):
        path = demo_root / filename
        text = path.read_text(encoding="utf-8")
        raw_logs.append(text)
        events.extend(HuaweiCollectDebugInfoParser().parse(path, filename, text))

    combined = "\n".join(raw_logs)
    assert "DEMO-" in combined and "SYNTHETIC-" in combined
    assert "192.168." not in combined
    assert set(re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", combined)) == {
        "192.0.2.1",
        "192.0.2.10",
    }
    assert all(
        mac.casefold().startswith("02:")
        for mac in re.findall(
            r"\b[0-9a-f]{2}(?::[0-9a-f]{2}){5}\b",
            combined,
            re.IGNORECASE,
        )
    )

    codes = {event.event_code for event in events}
    assert {
        "AP_UDM_HEARTBEAT_SEND_FAILED",
        "AP_UDM_LISTEN_PORT_FAILED",
        "AP_UDM_PROCESS_ABNORMAL",
        "GW_AP_HEARTBEAT_TIMEOUT",
        "GW_AP_OFFLINE_DETECTED",
        "GW_AP_TOPOLOGY_OFFLINE",
        "AP_LINK_REACHABLE",
    }.issubset(codes)
    assert sum(event.event_code == "AP_UDM_PROCESS_ABNORMAL" for event in events) == 6
    assert sum(event.event_code == "GW_AP_OFFLINE_DETECTED" for event in events) == 3

    def event_times(code: str, text: str = "") -> list[datetime]:
        return sorted(
            datetime.fromisoformat(str(event.timestamp_normalized))
            for event in events
            if event.event_code == code
            and text.casefold() in event.raw_text.casefold()
            and event.timestamp_normalized
        )

    send_failures = event_times("AP_UDM_HEARTBEAT_SEND_FAILED")
    port_failures = event_times("AP_UDM_LISTEN_PORT_FAILED", "check failed")
    process_failures = event_times(
        "AP_UDM_PROCESS_ABNORMAL", "udm Is Abnormal",
    )
    timeouts = event_times("GW_AP_HEARTBEAT_TIMEOUT")
    offline_events = event_times("GW_AP_OFFLINE_DETECTED")
    assert all(
        len(values) == 3
        for values in (
            send_failures, port_failures, process_failures,
            timeouts, offline_events,
        )
    )
    for send, port, process, timeout, offline in zip(
        send_failures,
        port_failures,
        process_failures,
        timeouts,
        offline_events,
        strict=True,
    ):
        assert send < port < process < timeout < offline
        assert 240 < (timeout - process).total_seconds() < 300
