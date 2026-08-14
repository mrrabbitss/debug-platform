from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from time import perf_counter
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit


CHAT_PROBE_NAMES = frozenset({
    "model_gateway_text_thinking_enabled",
    "llm_log_triage_planning",
    "comprehensive_20_round_fault_tree_agent",
    "evidence_constrained_final_synthesis",
    "case_chat_background_answer",
    "diagnosis_and_report_revision",
    "knowledge_folder_curation_generation",
    "knowledge_curation_conversational_refinement",
    "repository_patch_suggestion",
})


class ProbeFailure(RuntimeError):
    def __init__(self, code: str, **safe_details: Any) -> None:
        super().__init__(code)
        self.code = code
        self.safe_details = safe_details


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run secret-safe, isolated real-GLM checks for every Chat-model feature. "
            "The API key is read only from DEBUG_PLATFORM_GLM_API_KEY."
        )
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("DEBUG_PLATFORM_GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"),
    )
    parser.add_argument(
        "--model",
        default=os.getenv("DEBUG_PLATFORM_GLM_MODEL", "glm-5.2"),
    )
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=65536,
        help="Structured diagnosis/revision output budget; small probes use lower caps.",
    )
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Validate the local fault-tree compilation and retrieval map without an API key.",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        help="Run only the named probe; repeat this option to select multiple probes.",
    )
    return parser.parse_args()


def _usage(provider: Any) -> dict[str, int]:
    raw = getattr(provider, "last_usage", {}) or {}
    prompt = int(raw.get("prompt_tokens") or raw.get("input_tokens") or 0)
    completion = int(raw.get("completion_tokens") or raw.get("output_tokens") or 0)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": int(raw.get("total_tokens") or 0) or prompt + completion,
    }


def _safe_stop_reason(value: Any) -> dict[str, Any]:
    rendered = str(value or "").strip()
    prefix = rendered.split(":", 1)[0].strip().upper()
    code = prefix if prefix and all(
        character.isalnum() or character == "_" for character in prefix
    ) and len(prefix) <= 128 else "MODEL_STOP"
    return {
        "stop_reason_code": code,
        "stop_reason_chars": len(rendered),
        "stop_reason_hash": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
    }


def _safe_probe(
    report: dict[str, Any],
    name: str,
    action: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    started = perf_counter()
    print(f"[RUN ] {name}", flush=True)
    try:
        details = action()
    except Exception as exc:  # noqa: BLE001 - external diagnostic runner
        error_fingerprint = hashlib.sha256(
            f"{type(exc).__name__}:{exc}".encode("utf-8", errors="replace")
        ).hexdigest()
        failure = {
            "name": name,
            "status": "FAILED",
            "duration_ms": int((perf_counter() - started) * 1000),
            "error_type": type(exc).__name__,
            "error_fingerprint": error_fingerprint,
        }
        if isinstance(exc, ProbeFailure):
            failure.update({
                "error_code": exc.code,
                "safe_details": exc.safe_details,
            })
        report["probes"].append(failure)
        print(f"[FAIL] {name}: {type(exc).__name__}", flush=True)
        return {}
    record = {
        "name": name,
        "status": "PASSED",
        "duration_ms": int((perf_counter() - started) * 1000),
        **details,
    }
    report["probes"].append(record)
    print(f"[PASS] {name}", flush=True)
    return details


def main() -> int:
    args = _parse_args()
    unknown_probes = set(args.only).difference(CHAT_PROBE_NAMES)
    if unknown_probes:
        print(
            "ERROR: unknown --only probe name(s): "
            + ", ".join(sorted(unknown_probes)),
            file=sys.stderr,
        )
        return 2
    api_key = os.getenv("DEBUG_PLATFORM_GLM_API_KEY", "").strip()
    if not api_key and not args.preflight_only:
        print(
            "ERROR: set DEBUG_PLATFORM_GLM_API_KEY in the current process environment; "
            "the script intentionally has no --api-key argument.",
            file=sys.stderr,
        )
        return 2
    repository_root = Path(__file__).resolve().parents[1]
    backend_root = repository_root / "backend"
    sys.path.insert(0, str(backend_root))

    with tempfile.TemporaryDirectory(prefix="debug-platform-glm-validation-") as temp_name:
        temp_root = Path(temp_name)
        os.environ.update({
            "APP_ENV": "test",
            "DATABASE_URL": f"sqlite:///{(temp_root / 'validation.db').as_posix()}",
            "STORAGE_ROOT": str(temp_root / "storage"),
            "LLM_PROVIDER": "openai_compatible",
            "LLM_API_KEY": api_key or "preflight-not-used",
            "LLM_BASE_URL": args.base_url.rstrip("/"),
            "LLM_MODEL": args.model,
            "LLM_TIMEOUT_SECONDS": str(args.timeout_seconds),
            # Each probe is already isolated and reported independently. Provider-level
            # retries would multiply a 600-second read timeout and obscure which feature
            # actually failed.
            "LLM_MAX_RETRIES": "0",
        })

        from app.core.db import Base, SessionLocal, engine
        from app.core.utils import json_dumps
        from app.diagnostic_models import LogEvidenceMatch, LogTriageRun
        from app.models import (
            AgentRun,
            AnalysisRun,
            Artifact,
            Case,
            KnowledgeCurationSession,
        )
        from app.services import analysis_revision, case_chat, diagnosis, diagnostic_planning
        from app.services.agent_trace_runtime import create_live_agent_run
        from app.services.diagnostic_methods import (
            compile_diagnostic_patterns,
            load_applicable_diagnostic_methods,
        )
        from app.services.fault_tree_coverage import compile_fault_tree_items
        from app.services.diagnostic_planning_prompt import fault_tree_items_for_prompt
        from app.services.knowledge_curation import (
            GeneratedCaseDraft,
            RefinedCaseDraft,
            _initial_system_prompt,
            _initial_user_prompt,
            _normalize_markdown,
            _refinement_system_prompt,
        )
        from app.services.knowledge_curation_evidence import validate_curation_markdown
        from app.services.llm import OpenAICompatibleProvider

        Base.metadata.create_all(bind=engine)
        endpoint = urlsplit(args.base_url)
        endpoint_port = f":{endpoint.port}" if endpoint.port else ""
        report: dict[str, Any] = {
            "schema_version": 1,
            "model": args.model,
            "base_url_origin": (
                f"{endpoint.scheme.lower()}://{(endpoint.hostname or '').lower()}{endpoint_port}"
            ),
            "credential_recorded": False,
            "isolated_database": True,
            "private_document_bodies_recorded": False,
            "probes": [],
        }
        selected_probes = set(args.only)

        def run_probe(
            name: str,
            action: Callable[[], dict[str, Any]],
        ) -> dict[str, Any]:
            if selected_probes and name not in selected_probes:
                return {}
            return _safe_probe(report, name, action)

        def provider(
            thinking: str = "enabled",
            *,
            max_tokens: int | None = None,
        ) -> Any:
            instance = OpenAICompatibleProvider()
            instance.thinking_mode = thinking
            instance.thinking_enabled = thinking == "enabled"
            instance.max_tokens = max_tokens or args.max_tokens
            return instance

        def async_result(awaitable: Awaitable[Any]) -> Any:
            return asyncio.run(awaitable)

        def text_probe() -> dict[str, Any]:
            model = provider("enabled", max_tokens=min(args.max_tokens, 1024))
            response = async_result(model.generate_text(
                "你是模型网关连通性检查器。",
                "只回复 READY 和当前模型名称，不要输出任何密钥。",
                purpose="model_profile_test",
            ))
            if not response.strip():
                raise ValueError("The model gateway returned an empty response")
            return {
                "thinking_mode": "enabled",
                "response_chars": len(response),
                "usage": _usage(model),
                "finish_reason": getattr(model, "last_finish_reason", None),
            }

        if not args.preflight_only:
            run_probe("model_gateway_text_thinking_enabled", text_probe)

        case = Case(
            id="CASE-real-glm",
            title="AP频繁离线",
            description="AP频繁离线，需要联合主网关与从 AP 日志按故障树逐项排查。",
            device_type="AP",
            topology="GW 为主设备，AP 为从设备。",
            model_egress_approved=True,
        )
        artifact = Artifact(
            id="ART-real-glm",
            case_id=case.id,
            original_name="synthetic-ap-offline.log",
            stored_path="synthetic-ap-offline.log",
            sha256="a" * 64,
            size_bytes=4096,
            status="PARSED",
            active_parse_run_id="PRUN-real-glm",
        )
        with SessionLocal() as db:
            db.add_all([case, artifact])
            db.commit()
            methods = load_applicable_diagnostic_methods(db, case)
        patterns = compile_diagnostic_patterns(methods)
        fault_tree_items = compile_fault_tree_items(methods)
        if not methods or not fault_tree_items:
            raise RuntimeError(
                "No local or managed fault-tree method was loaded; place 故障树.md in the repository root"
            )
        fault_tree_prompt_items = fault_tree_items_for_prompt(
            fault_tree_items,
            patterns,
        )
        unmapped_items = [
            item["id"]
            for item in fault_tree_prompt_items
            if not item.get("recommended_patterns")
            and not item.get("recommended_search_terms")
        ]
        if unmapped_items:
            raise RuntimeError("Fault-tree items without a deterministic retrieval entry")
        if args.preflight_only:
            _safe_probe(report, "fault_tree_retrieval_preflight", lambda: {
                "method_document_count": len(methods),
                "compiled_pattern_count": len(patterns),
                "fault_tree_item_count": len(fault_tree_items),
                "items_with_log_patterns": sum(
                    bool(item.get("recommended_patterns"))
                    for item in fault_tree_prompt_items
                ),
                "items_with_retrieval_entry": len(fault_tree_prompt_items),
            })
            report["summary"] = {
                "passed": sum(item["status"] == "PASSED" for item in report["probes"]),
                "failed": sum(item["status"] == "FAILED" for item in report["probes"]),
                "total": len(report["probes"]),
                "fault_tree_document_count": sum(
                    item.role == "FAULT_TREE" for item in methods
                ),
                "fault_tree_item_count": len(fault_tree_items),
                "method_document_hashes": [item.content_sha256 for item in methods],
                "fault_tree_item_ids_hash": hashlib.sha256(
                    "\n".join(item.id for item in fault_tree_items).encode("utf-8")
                ).hexdigest(),
            }
            rendered = json.dumps(report, ensure_ascii=False, indent=2)
            if args.report:
                args.report.parent.mkdir(parents=True, exist_ok=True)
                args.report.write_text(rendered + "\n", encoding="utf-8")
            print(rendered, flush=True)
            engine.dispose()
            return 0 if report["summary"]["failed"] == 0 else 1

        triage = LogTriageRun(
            id="LTRIAGE-real-glm",
            case_id=case.id,
            artifact_id=artifact.id,
            parse_run_id="PRUN-real-glm",
            status="COMPLETED",
        )
        with SessionLocal() as db:
            db.add(triage)
            db.flush()
            for index, pattern in enumerate(patterns, start=1):
                db.add(LogEvidenceMatch(
                    id=f"LEM-GLM-{index:05d}",
                    triage_run_id=triage.id,
                    case_id=case.id,
                    artifact_id=artifact.id,
                    source_file="synthetic-ap-offline.log",
                    line_start=index,
                    line_end=index,
                    bucket="METHOD_REQUIRED",
                    relevance_score=0.85,
                    pattern_id=pattern.id,
                    pattern_text=pattern.text,
                    match_kind=pattern.match_kind,
                    reason="Synthetic evidence generated from an applicable method pattern",
                    message=f"SYNTHETIC TEST EVIDENCE: {pattern.text}",
                    occurrence_count=1,
                    metadata_json=json_dumps({
                        "method_source": {
                            "document_id": pattern.document_id,
                            "heading": pattern.heading,
                            "line_start": pattern.line_start,
                        },
                        "artifact_source": {
                            "artifact_id": artifact.id,
                            "device_type": "AP",
                            "role": "SECONDARY_AP",
                        },
                    }),
                ))
            db.commit()

        def log_plan_probe() -> dict[str, Any]:
            from app.services.log_triage_planning import plan_with_model

            model = provider("disabled", max_tokens=min(args.max_tokens, 8192))
            plan, metadata = async_result(plan_with_model(
                case,
                methods,
                patterns,
                [{"artifact_id": artifact.id, "device_type": "AP", "role": "SECONDARY_AP"}],
                model,
            ))
            if plan.get("planner_mode") != "llm":
                raise ValueError("Log triage planning used fallback")
            if set(plan.get("read_document_ids", [])) != {item.id for item in methods}:
                raise ValueError("Log triage did not attest every applicable method")
            return {
                "thinking_mode": getattr(model, "last_thinking_mode", "disabled"),
                "method_count": len(methods),
                "compiled_pattern_count": len(patterns),
                "selected_pattern_count": len(plan.get("selected_pattern_ids", [])),
                "additional_keyword_count": len(plan.get("additional_keywords", [])),
                "validation_retry_count": metadata.get("validation_retry_count", 0),
                "usage": metadata.get("usage") or _usage(model),
            }

        run_probe("llm_log_triage_planning", log_plan_probe)

        planning_holder: dict[str, Any] = {}

        def diagnostic_planning_probe() -> dict[str, Any]:
            model = provider("enabled")
            diagnostic_planning.get_llm_provider = lambda: model
            with SessionLocal() as db:
                trace = create_live_agent_run(
                    db,
                    operation="comprehensive_diagnosis",
                    case_id=case.id,
                    resource_type="analysis",
                    resource_id="RUN-real-glm",
                    input_summary={"case_id": case.id},
                )
                db.commit()
            planning = diagnostic_planning.run_diagnostic_planning(
                type("Context", (), {
                    "update": lambda self, *_args, **_kwargs: None,
                    "raise_if_cancelled": lambda self: None,
                })(),
                case=case,
                agent_run_id=trace.id,
                baseline_search={"summary": {}, "results": []},
                session_factory=SessionLocal,
            )
            planning_holder["result"] = planning
            coverage = planning.public_plan.get("fault_tree_coverage", {})
            if diagnostic_planning.MAX_PLANNING_ROUNDS != 20:
                raise ValueError("Runtime planning limit is not 20")
            if not planning.public_plan.get("planner_accepted"):
                failure = planning.public_plan.get("planner_failure") or {}
                raise ProbeFailure(
                    "PLANNER_REJECTED",
                    planner_failure_code=failure.get("code"),
                    planner_failure_path=failure.get("field_path"),
                    planner_error_type=failure.get("error_type"),
                    finish_reason=failure.get("finish_reason"),
                    stop_reason=planning.public_plan.get("stop_reason"),
                    completed_rounds=len(planning.public_plan.get("rounds", [])),
                    fault_tree_total=coverage.get("total"),
                    fault_tree_attempted=coverage.get("attempted"),
                    fault_tree_concluded=coverage.get("concluded"),
                )
            if not coverage.get("complete"):
                raise ValueError("Fault-tree coverage did not reach a terminal result for every item")
            with SessionLocal() as db:
                persisted_trace = db.get(AgentRun, trace.id)
                trace_usage = {
                    "prompt_tokens": int(persisted_trace.input_tokens or 0),
                    "completion_tokens": int(persisted_trace.output_tokens or 0),
                    "total_tokens": int(persisted_trace.total_tokens or 0),
                } if persisted_trace else _usage(model)
            return {
                "thinking_mode": "enabled",
                "max_rounds": diagnostic_planning.MAX_PLANNING_ROUNDS,
                "completed_rounds": len(planning.public_plan.get("rounds", [])),
                **_safe_stop_reason(planning.public_plan.get("stop_reason")),
                "fault_tree_total": coverage.get("total"),
                "fault_tree_attempted": coverage.get("attempted"),
                "fault_tree_concluded": coverage.get("concluded"),
                "fault_tree_complete": coverage.get("complete"),
                "status_counts": coverage.get("status_counts", {}),
                "tool_call_count": len(planning.public_plan.get("tool_calls", [])),
                "usage": trace_usage,
            }

        run_probe("comprehensive_20_round_fault_tree_agent", diagnostic_planning_probe)

        synthesis_holder: dict[str, Any] = {}

        def synthesis_probe() -> dict[str, Any]:
            planning = planning_holder.get("result")
            if planning is None:
                raise RuntimeError("Comprehensive planning did not pass")
            model = provider("enabled")
            diagnosis.get_llm_provider = lambda: model
            method_evidence = [{
                "evidence_id": item.id,
                "source_type": item.source_type,
                "title": item.title,
                "content": item.content,
                "version": item.version,
                "role": item.role,
            } for item in planning.method_documents]
            evidence = [*method_evidence, *planning.evidence, *planning.supplemental_results]
            first_case_evidence = next(
                (item for item in evidence if item.get("source_type") == "log_triage_match"),
                None,
            )
            if first_case_evidence is None:
                raise RuntimeError("Synthetic case evidence was not available to synthesis")
            baseline = {
                "case": {
                    "id": case.id,
                    "title": case.title,
                    "device_type": case.device_type,
                    "device_model": None,
                    "firmware_version": None,
                },
                "summary": "Synthetic deterministic baseline awaiting evidence-constrained synthesis.",
                "confirmed_facts": [{
                    "statement": "Synthetic log evidence is available for validation.",
                    "evidence_ids": [first_case_evidence["evidence_id"]],
                }],
                "hypotheses": [],
                "recommended_actions": [],
                "missing_information": [],
                "suspected_modules": ["WLAN", "UDM"],
                "limitations": ["All log lines in this validation are synthetic."],
                "retrieved_knowledge": [],
                "related_code": [],
                "diagnostic_planning": planning.public_plan,
                "analysis_engine": "synthetic-validation-baseline",
            }
            synthesized, metadata = async_result(
                diagnosis._augment_with_llm_with_metadata(case, baseline, evidence)
            )
            if metadata.get("fallback"):
                failure = metadata.get("failure") or {}
                raise ValueError(f"Final synthesis fell back: {failure.get('code')}")
            expected = planning.public_plan["fault_tree_coverage"]["total"]
            conclusions = synthesized.get("fault_tree_conclusions", [])
            if len(conclusions) != expected:
                raise ValueError("Final synthesis omitted fault-tree conclusions")
            synthesis_holder.update({"result": synthesized, "evidence": evidence})
            return {
                "fault_tree_conclusion_count": len(conclusions),
                "hypothesis_count": len(synthesized.get("hypotheses", [])),
                "validation_retry_count": getattr(model, "last_validation_retry_count", 0),
                "usage": metadata.get("usage") or _usage(model),
            }

        run_probe("evidence_constrained_final_synthesis", synthesis_probe)

        def case_chat_probe() -> dict[str, Any]:
            synthesized = synthesis_holder.get("result")
            evidence = synthesis_holder.get("evidence")
            if synthesized is None or evidence is None:
                raise RuntimeError("Final synthesis did not pass")
            with SessionLocal() as db:
                analysis = AnalysisRun(
                    id="RUN-real-glm",
                    case_id=case.id,
                    status="COMPLETED",
                    provider="openai_compatible",
                    model=args.model,
                    prompt_version="external-validation",
                    result_json=json_dumps(synthesized),
                    evidence_json=json_dumps(evidence),
                )
                trace = create_live_agent_run(
                    db,
                    operation="case_chat",
                    case_id=case.id,
                    resource_type="conversation_message",
                    resource_id="MSG-real-glm",
                    input_summary={"case_id": case.id},
                )
                db.add(analysis)
                db.commit()
            model = provider("enabled", max_tokens=min(args.max_tokens, 4096))
            case_chat.get_llm_provider = lambda: model
            answer, citations, usage = async_result(case_chat._generate_case_answer(
                type("Context", (), {
                    "update": lambda self, *_args, **_kwargs: None,
                    "raise_if_cancelled": lambda self: None,
                })(),
                case=case,
                question="哪些故障树节点已确认，哪些仍需补充证据？",
                current_message_id="MSG-real-glm",
                agent_run_id=trace.id,
            ))
            if not answer.strip() or not citations:
                raise ValueError("Case chat returned no answer or citations")
            return {
                "answer_chars": len(answer),
                "citation_count": len(citations),
                "usage": usage or _usage(model),
            }

        run_probe("case_chat_background_answer", case_chat_probe)

        def revision_probe() -> dict[str, Any]:
            synthesized = synthesis_holder.get("result")
            evidence = synthesis_holder.get("evidence")
            if synthesized is None or evidence is None:
                raise RuntimeError("Final synthesis did not pass")
            with SessionLocal() as db:
                source = db.get(AnalysisRun, "RUN-real-glm")
                if source is None:
                    raise RuntimeError("Source analysis is missing")
                db.expunge(source)
            model = provider("enabled")
            analysis_revision.get_llm_provider = lambda: model
            proposed, change_summary, assistant_message, usage = async_result(
                analysis_revision._generate_revision(
                    case=case,
                    source=source,
                    instruction="把证据不足的节点在报告中明确列为待采集项，不得改写已有证据状态。",
                    current_message_id="MSG-revision-real-glm",
                    evidence=evidence,
                    conversation_history=[],
                )
            )
            expected = synthesized.get("fault_tree_conclusions", [])
            if len(proposed.get("fault_tree_conclusions", [])) != len(expected):
                raise ValueError("Diagnosis revision omitted fault-tree conclusions")
            return {
                "change_summary_chars": len(change_summary),
                "assistant_message_chars": len(assistant_message),
                "fault_tree_conclusion_count": len(expected),
                "usage": usage or _usage(model),
            }

        run_probe("diagnosis_and_report_revision", revision_probe)

        curation_holder: dict[str, Any] = {}
        source_evidence = """[SRC-0001] synthetic-incident.txt
L1: Symptom: AP frequently goes offline.
L2: GW log: Status=[0] and src=CtrlPointVerify.
L3: AP log: listen port check failed.
L4: Resolution: restart the UDM process after collecting diagnostics.
L5: Verification: AP stayed online during the synthetic observation window.
"""

        def curation_generate_probe() -> dict[str, Any]:
            session = KnowledgeCurationSession(
                id="KCUR-real-glm",
                title_hint="Synthetic AP offline case",
                device_type="AP",
                module="WLAN",
            )
            model = provider("disabled", max_tokens=min(args.max_tokens, 8192))
            raw = async_result(model.generate_json(
                _initial_system_prompt(),
                _initial_user_prompt(session, source_evidence),
                schema_name="knowledge_case_curation",
                purpose="knowledge_case_curation",
            ))
            generated = GeneratedCaseDraft.model_validate(raw)
            markdown = _normalize_markdown(generated.title, generated.markdown)
            validation = validate_curation_markdown(markdown, {"SRC-0001": 5})
            if not validation.get("confirmable"):
                raise ValueError("Generated knowledge draft is not confirmable")
            curation_holder.update({"markdown": markdown, "title": generated.title})
            return {
                "draft_chars": len(markdown),
                "citation_count": validation.get("citation_count"),
                "usage": _usage(model),
            }

        run_probe("knowledge_folder_curation_generation", curation_generate_probe)

        def curation_refine_probe() -> dict[str, Any]:
            markdown = curation_holder.get("markdown")
            if not markdown:
                raise RuntimeError("Initial curation did not pass")
            model = provider("disabled", max_tokens=min(args.max_tokens, 8192))
            user_prompt = f"""当前草稿：
<CURRENT_DRAFT>
{markdown}
</CURRENT_DRAFT>

工程师本轮说明：
请在适用范围中明确这是合成验证案例，其他事实和引用保持不变。

可引用的脱敏来源证据：
<SOURCE_EVIDENCE>
{source_evidence}
</SOURCE_EVIDENCE>
"""
            raw = async_result(model.generate_json(
                _refinement_system_prompt(),
                user_prompt,
                schema_name="knowledge_case_refinement",
                purpose="knowledge_case_refinement",
            ))
            refined = RefinedCaseDraft.model_validate(raw)
            revised = _normalize_markdown(curation_holder["title"], refined.revised_markdown)
            validation = validate_curation_markdown(revised, {"SRC-0001": 5})
            if not validation.get("confirmable"):
                raise ValueError("Refined knowledge draft is not confirmable")
            return {
                "draft_chars": len(revised),
                "assistant_message_chars": len(refined.assistant_message),
                "citation_count": validation.get("citation_count"),
                "usage": _usage(model),
            }

        run_probe("knowledge_curation_conversational_refinement", curation_refine_probe)

        def patch_probe() -> dict[str, Any]:
            model = provider("disabled", max_tokens=min(args.max_tokens, 4096))
            response = async_result(model.generate_text(
                "你是 C/C++ 网络设备代码审查工程师，生成最小、可审查、未自动应用的候选补丁。",
                json_dumps({
                    "instruction": "Prevent a null pointer dereference.",
                    "case": {"title": "Synthetic validation", "device": "AP"},
                    "symbol": {
                        "file_path": "src/example.c",
                        "line_start": 1,
                        "line_end": 4,
                        "code": "int read_value(int *ptr) { return *ptr; }",
                    },
                    "output": "只输出 unified diff；无法安全修复时说明 NEED_HUMAN_REVIEW。",
                }),
                purpose="patch_suggestion",
            ))
            normalized = response.strip().casefold()
            if not response.strip() or not (
                "diff --git" in normalized
                or "--- a/" in normalized
                or "need_human_review" in normalized
            ):
                raise ValueError("Patch suggestion was neither a unified diff nor NEED_HUMAN_REVIEW")
            return {"response_chars": len(response), "usage": _usage(model)}

        run_probe("repository_patch_suggestion", patch_probe)

        report["summary"] = {
            "passed": sum(item["status"] == "PASSED" for item in report["probes"]),
            "failed": sum(item["status"] == "FAILED" for item in report["probes"]),
            "total": len(report["probes"]),
            "fault_tree_document_count": sum(item.role == "FAULT_TREE" for item in methods),
            "fault_tree_item_count": len(fault_tree_items),
            "method_document_hashes": [item.content_sha256 for item in methods],
            "fault_tree_item_ids_hash": hashlib.sha256(
                "\n".join(item.id for item in fault_tree_items).encode("utf-8")
            ).hexdigest(),
        }
        rendered = json.dumps(report, ensure_ascii=False, indent=2)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(rendered + "\n", encoding="utf-8")
        print(rendered, flush=True)
        engine.dispose()
        return 0 if report["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
