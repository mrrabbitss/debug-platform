import asyncio
from collections import Counter
from typing import Any

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.utils import json_dumps, json_loads, new_id, utcnow
from app.models import AnalysisRun, Case, CodeSymbol, ConversationMessage, LogEvent, Repository
from app.services.jobs import JobContext
from app.services.llm import LLMError, get_active_chat_model_info, get_llm_provider
from app.services.rag import RetrievalHit, retriever


HYPOTHESIS_RULES: dict[str, dict[str, Any]] = {
    "KERNEL_OOPS": {
        "title": "内核或驱动异常导致设备服务不可用",
        "description": "日志出现内核 Oops、panic、调用栈或段错误，应优先检查驱动、内核模块和崩溃前后的资源状态。",
        "priority": "P0",
        "actions": ["保留完整调用栈和崩溃时间前后日志", "确认固件与驱动版本", "结合符号表定位崩溃函数"],
    },
    "PROCESS_CRASH": {
        "title": "关键进程异常退出",
        "description": "用户态进程出现崩溃或被信号终止，可能由非法配置、内存错误或依赖服务异常触发。",
        "priority": "P0",
        "actions": ["检查 core dump 和 backtrace", "核对崩溃前配置变更", "使用 ASan/静态分析复现相关模块"],
    },
    "HOSTAPD_START_FAILED": {
        "title": "hostapd 配置或驱动交互失败",
        "description": "无线服务在启动/重载阶段失败，常见原因包括信道、国家码、加密参数、接口状态或驱动能力不匹配。",
        "priority": "P0",
        "actions": ["对比生效配置与产品约束", "检查驱动初始化和无线接口状态", "核对失败前后的配置下发日志"],
    },
    "AUTH_FAILED": {
        "title": "无线认证或密钥协商失败",
        "description": "认证、EAP 或四次握手失败，需结合安全模式、密钥、时间同步和终端兼容性分析。",
        "priority": "P1",
        "actions": ["确认认证模式与密钥配置", "检查 EAP/4-way handshake 前后日志", "必要时采集空口报文"],
    },
    "DHCP_FAILED": {
        "title": "DHCP 地址分配链路异常",
        "description": "客户端或 WAN 侧未完成 DHCP 交互，可能与接口状态、地址池、转发/VLAN 或对端响应有关。",
        "priority": "P1",
        "actions": ["检查 DISCOVER/OFFER/REQUEST/ACK 链路", "核对 VLAN 和桥接配置", "使用抓包确认报文是否到达"],
    },
    "PPPOE_FAILED": {
        "title": "PPPoE 建链或认证失败",
        "description": "PADI/PADO 或 PAP/CHAP 阶段失败，需区分链路不可达、账号认证和会话异常。",
        "priority": "P1",
        "actions": ["确认 WAN 链路与 VLAN", "核对 PADI/PADO 时序", "检查账号认证返回码"],
    },
    "PON_LOS": {
        "title": "PON 光链路异常",
        "description": "检测到 LOS、光信号丢失或 PON 状态异常，应优先检查光功率、链路和注册状态。",
        "priority": "P0",
        "actions": ["检查光功率和 LOS 告警", "确认 ONU 注册状态", "核对异常前后的 PON 状态变化"],
    },
    "OMCI_ERROR": {
        "title": "OMCI 配置或交互异常",
        "description": "OMCI 消息超时、失败或属性不匹配，可能影响业务配置下发和 ONU 管理。",
        "priority": "P1",
        "actions": ["定位失败的 ME/属性", "对比 OLT 下发与设备响应", "检查同版本历史案例"],
    },
    "TR069_ERROR": {
        "title": "TR-069/CWMP 管理链路异常",
        "description": "远程管理参数或会话异常，需检查 ACS 连通性、参数合法性和会话状态。",
        "priority": "P2",
        "actions": ["检查 Inform/响应流程", "核对参数路径和类型", "确认 ACS 网络连通性"],
    },
    "MEMORY_PRESSURE": {
        "title": "内存压力或资源泄漏",
        "description": "日志出现 OOM、分配失败或内存泄漏特征，可能导致进程异常、看门狗重启或业务退化。",
        "priority": "P0",
        "actions": ["对比故障前后内存指标", "检查长期增长进程", "使用 Valgrind/ASan 或内存统计复现"],
    },
    "CONFIG_INVALID": {
        "title": "配置项缺失或取值不合法",
        "description": "系统明确报告配置错误，应追踪配置来源、版本迁移和参数校验链路。",
        "priority": "P1",
        "actions": ["确认配置来源和最后修改时间", "核对字段范围及默认值", "检查配置转换和落盘结果"],
    },
}


def _event_to_evidence(event: LogEvent) -> dict[str, Any]:
    return {
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
    }


def _retrieval_to_evidence(hit: RetrievalHit) -> dict[str, Any]:
    return {
        "evidence_id": hit.evidence_id,
        "source_type": hit.source_type,
        "title": hit.title,
        "content": hit.content,
        "score": hit.score,
        "metadata": hit.metadata,
    }


def _build_rule_result(case: Case, events: list[LogEvent], hits: list[RetrievalHit], code_symbols: list[CodeSymbol]) -> dict[str, Any]:
    counts = Counter(event.event_code for event in events)
    facts = []
    for event in events[:20]:
        facts.append({
            "statement": f"{event.timestamp_normalized or event.timestamp_raw or '未知时间'}，{event.component} 出现 {event.event_code}：{event.message[:240]}",
            "evidence_ids": [event.id],
        })

    hypotheses = []
    for code, count in counts.most_common():
        if code not in HYPOTHESIS_RULES:
            continue
        rule = HYPOTHESIS_RULES[code]
        supporting = [event.id for event in events if event.event_code == code][:8]
        independent_sources = len({event.source_file for event in events if event.event_code == code})
        score = min(0.95, 0.45 + min(count, 5) * 0.07 + min(independent_sources, 3) * 0.08)
        hypotheses.append({
            "rank": 0,
            "title": rule["title"],
            "description": rule["description"],
            "supporting_evidence": supporting,
            "contradicting_evidence": [],
            "confidence_score": round(score, 2),
            "confidence_level": "HIGH" if score >= 0.78 else "MEDIUM",
            "priority": rule["priority"],
            "needs_human_review": True,
            "event_code": code,
        })
    hypotheses.sort(key=lambda item: item["confidence_score"], reverse=True)
    for idx, item in enumerate(hypotheses, start=1):
        item["rank"] = idx

    recommendations = []
    seen_actions = set()
    for hypothesis in hypotheses[:5]:
        rule = HYPOTHESIS_RULES[hypothesis["event_code"]]
        for action in rule["actions"]:
            if action in seen_actions:
                continue
            seen_actions.add(action)
            recommendations.append({
                "priority": rule["priority"],
                "action": action,
                "reason": f"用于验证根因候选：{hypothesis['title']}",
                "expected_result": "获得支持或排除该根因的确定性证据",
            })

    module_counts = Counter(event.module for event in events)
    missing = []
    if not case.issue_time:
        missing.append("未提供精确的问题发生时间，建议补充以缩小日志分析窗口")
    if not events:
        missing.append("未提取到结构化异常事件，需要确认日志包是否完整或扩展解析器")
    if not any(event.event_code in {"KERNEL_OOPS", "PROCESS_CRASH"} for event in events):
        missing.append("若存在进程崩溃，建议补充 core dump、backtrace 或对应进程日志")

    summary = f"共识别 {len(events)} 条关键事件，主要集中在 " + "、".join(
        f"{module}({count})" for module, count in module_counts.most_common(4)
    )
    return {
        "summary": summary,
        "case": {
            "id": case.id, "title": case.title, "device_type": case.device_type,
            "device_model": case.device_model, "firmware_version": case.firmware_version,
        },
        "confirmed_facts": facts,
        "hypotheses": hypotheses,
        "recommended_actions": recommendations,
        "missing_information": missing,
        "suspected_modules": [module for module, _ in module_counts.most_common(6)],
        "retrieved_knowledge": [
            {"evidence_id": hit.evidence_id, "title": hit.title, "source_type": hit.source_type, "score": hit.score}
            for hit in hits
        ],
        "related_code": [
            {
                "symbol_id": symbol.id, "kind": symbol.kind, "name": symbol.name,
                "file_path": symbol.file_path, "line_start": symbol.line_start,
                "line_end": symbol.line_end,
            }
            for symbol in code_symbols[:20]
        ],
        "limitations": [
            "时间相邻仅表示关联，不自动等同于因果关系",
            "模型结论必须结合设备实际配置、拓扑和复现结果由工程师确认",
        ],
        "analysis_engine": "rule+routing+rag",
    }


def _find_related_symbols(case_id: str, events: list[LogEvent]) -> list[CodeSymbol]:
    terms = {event.component.lower() for event in events[:50]}
    terms.update(event.event_code.lower().split("_")[0] for event in events[:50])
    with SessionLocal() as db:
        all_symbols = db.scalars(
            select(CodeSymbol).join(Repository, CodeSymbol.repository_id == Repository.id)
            .where(Repository.case_id == case_id)
            .limit(5000)
        ).all()
    scored = []
    for symbol in all_symbols:
        haystack = f"{symbol.name} {symbol.file_path} {symbol.module or ''} {symbol.signature or ''}".lower()
        score = sum(1 for term in terms if term and term in haystack)
        if score:
            scored.append((score, symbol))
    return [symbol for _, symbol in sorted(scored, key=lambda item: item[0], reverse=True)[:30]]


async def _augment_with_llm(case: Case, result: dict, evidence: list[dict]) -> dict:
    provider = get_llm_provider()
    if provider.is_mock:
        return result
    compact_evidence = evidence[:40]
    prompt = {
        "case": result["case"],
        "deterministic_result": result,
        "evidence": compact_evidence,
        "requirements": [
            "只能引用给定 evidence_id",
            "严格区分已确认事实和推测",
            "不得把时间相邻直接断言为因果",
            "输出 summary、confirmed_facts、hypotheses、recommended_actions、missing_information、suspected_modules、limitations",
            "保留确定性规则结果中有证据支持的内容，可补充反证和排序",
        ],
    }
    try:
        llm_result = await provider.generate_json(
            "你是面向 GW/AP 网络设备的高级故障诊断工程师。所有结论必须有证据、可审计并提示不确定性。",
            json_dumps(prompt),
            "gw_ap_diagnosis",
        )
        if isinstance(llm_result, dict) and llm_result.get("hypotheses"):
            llm_result["analysis_engine"] = "rule+rag+llm"
            llm_result["deterministic_baseline"] = result
            return llm_result
    except LLMError as exc:
        result.setdefault("warnings", []).append(str(exc))
    return result


def analyze_case_job(ctx: JobContext, case_id: str) -> dict:
    model_info = get_active_chat_model_info()
    with SessionLocal() as db:
        case = db.get(Case, case_id)
        if not case:
            raise ValueError("Case not found")
        case.status = "ANALYZING"
        run = AnalysisRun(
            id=new_id("RUN"), case_id=case_id, status="RUNNING",
            provider=str(model_info["provider"]),
            model=str(model_info["model"]),
        )
        db.add(run)
        db.commit()
        run_id = run.id

    ctx.update(10, "Collecting high-signal log events")
    with SessionLocal() as db:
        case = db.get(Case, case_id)
        events = db.scalars(
            select(LogEvent).where(LogEvent.case_id == case_id)
            .order_by(LogEvent.timestamp_normalized.asc().nullslast(), LogEvent.line_start.asc())
        ).all()
    severity_order = {"CRITICAL": 0, "ERROR": 1, "WARN": 2, "INFO": 3}
    events = sorted(events, key=lambda event: (severity_order.get(event.level, 4), -event.confidence))[:300]

    query_parts = [case.title, case.description, case.device_type, case.device_model or "", case.firmware_version or ""]
    query_parts += [f"{event.event_code} {event.component} {event.message[:160]}" for event in events[:30]]
    query = "\n".join(query_parts)
    ctx.update(30, "Retrieving protocol, product and historical evidence")
    hits = retriever.search(query, device_type=case.device_type, top_k=12)
    code_symbols = _find_related_symbols(case_id, events)
    result = _build_rule_result(case, events, hits, code_symbols)
    evidence = [_event_to_evidence(event) for event in events[:100]] + [_retrieval_to_evidence(hit) for hit in hits]

    ctx.update(60, "Running constrained LLM synthesis")
    result = asyncio.run(_augment_with_llm(case, result, evidence))
    result["analysis_run_id"] = run_id
    result["generated_at"] = utcnow().isoformat()

    with SessionLocal() as db:
        run = db.get(AnalysisRun, run_id)
        case = db.get(Case, case_id)
        if run and case:
            run.status = "COMPLETED"
            run.result_json = json_dumps(result)
            run.evidence_json = json_dumps(evidence)
            run.completed_at = utcnow()
            case.status = "COMPLETED"
            if result.get("hypotheses"):
                case.severity = result["hypotheses"][0].get("priority", "UNKNOWN")
            db.commit()
    ctx.update(95, "Diagnosis completed")
    return {"analysis_run_id": run_id, "summary": result.get("summary"), "hypotheses": len(result.get("hypotheses", []))}


async def chat_about_case(case_id: str, question: str) -> tuple[str, list[dict]]:
    with SessionLocal() as db:
        case = db.get(Case, case_id)
        if not case:
            raise ValueError("Case not found")
        latest = db.scalars(
            select(AnalysisRun).where(AnalysisRun.case_id == case_id, AnalysisRun.status == "COMPLETED")
            .order_by(AnalysisRun.created_at.desc()).limit(1)
        ).first()
    diagnosis = json_loads(latest.result_json, {}) if latest else {}
    hits = retriever.search(f"{case.title} {case.description} {question}", device_type=case.device_type, top_k=6)
    citations = [_retrieval_to_evidence(hit) for hit in hits]
    if latest:
        citations.insert(0, {"evidence_id": latest.id, "source_type": "analysis", "title": "最新诊断结果", "content": latest.result_json[:5000]})

    provider = get_llm_provider()
    if provider.is_mock:
        hypothesis_text = "；".join(item.get("title", "") for item in diagnosis.get("hypotheses", [])[:3]) or "暂无明确根因"
        answer = f"基于当前案例，主要根因候选为：{hypothesis_text}。你的问题是“{question}”。建议结合引用证据逐条核验；当前为 Mock 模式，未进行额外模型推理。"
    else:
        answer = await provider.generate_text(
            "你是 GW/AP 故障诊断助手。仅基于提供的案例、诊断和证据回答；引用证据编号，明确不确定性。",
            json_dumps({"question": question, "case": {"title": case.title, "description": case.description}, "diagnosis": diagnosis, "evidence": citations}),
        )
    with SessionLocal() as db:
        db.add(ConversationMessage(id=new_id("MSG"), case_id=case_id, role="user", content=question))
        db.add(ConversationMessage(id=new_id("MSG"), case_id=case_id, role="assistant", content=answer, citations_json=json_dumps(citations)))
        db.commit()
    return answer, citations
