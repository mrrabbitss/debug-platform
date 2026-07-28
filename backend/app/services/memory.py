import hashlib
import math
import re
from collections import Counter
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.utils import json_dumps, json_loads, mask_sensitive, new_id, utcnow
from app.models import AgentMemory, AnalysisRun, Case
from app.services.rag import tokenize


MEMORY_TYPES = {"EPISODIC", "PROCEDURAL", "FAILURE"}


def _fingerprint(memory_type: str, title: str, content: str) -> str:
    normalized = re.sub(r"\s+", " ", f"{memory_type}\n{title}\n{content}").strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def upsert_memory(
    db: Session,
    *,
    memory_type: str,
    case_id: str | None,
    source_kind: str,
    source_id: str | None,
    title: str,
    content: str,
    context: dict[str, Any] | None = None,
    evidence: list[dict[str, Any]] | None = None,
    outcome: str = "UNKNOWN",
    confidence: float = 0.5,
    dedup_key: str | None = None,
) -> AgentMemory:
    normalized_type = memory_type.upper()
    if normalized_type not in MEMORY_TYPES:
        raise ValueError(f"Unsupported memory type: {memory_type}")
    title = title.strip()[:512]
    content = content.strip()
    if not title or not content:
        raise ValueError("Memory title and content cannot be empty")
    fingerprint = (
        hashlib.sha256(
            f"{normalized_type}\n{dedup_key.strip().lower()}".encode("utf-8")
        ).hexdigest()
        if dedup_key and dedup_key.strip()
        else _fingerprint(normalized_type, title, content)
    )
    case_clause = (
        AgentMemory.case_id.is_(None)
        if case_id is None
        else AgentMemory.case_id == case_id
    )
    existing = db.scalar(select(AgentMemory).where(
        AgentMemory.memory_type == normalized_type,
        AgentMemory.fingerprint == fingerprint,
        case_clause,
    ))
    if existing:
        existing.occurrence_count += 1
        existing.title = title
        existing.content = content
        existing.source_kind = source_kind
        existing.source_id = source_id
        existing.context_json = json_dumps(context or json_loads(existing.context_json, {}))
        existing.evidence_json = json_dumps(evidence or json_loads(existing.evidence_json, []))
        existing.outcome = outcome
        existing.confidence = max(existing.confidence, max(0.0, min(float(confidence), 1.0)))
        existing.updated_at = utcnow()
        return existing
    memory = AgentMemory(
        id=new_id("MEM"),
        case_id=case_id,
        memory_type=normalized_type,
        source_kind=source_kind,
        source_id=source_id,
        title=title,
        content=content,
        context_json=json_dumps(context or {}),
        evidence_json=json_dumps(evidence or []),
        outcome=outcome,
        confidence=max(0.0, min(float(confidence), 1.0)),
        fingerprint=fingerprint,
    )
    db.add(memory)
    return memory


def _hypothesis_evidence(result: dict[str, Any]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    seen: set[str] = set()
    for hypothesis in result.get("hypotheses", []):
        for evidence_id in hypothesis.get("supporting_evidence", []):
            value = str(evidence_id)
            if value and value not in seen:
                seen.add(value)
                evidence.append({
                    "evidence_id": value,
                    "role": "supporting",
                    "hypothesis": hypothesis.get("title"),
                })
    return evidence[:100]


def extract_memories_from_analysis(
    db: Session,
    case: Case,
    run: AnalysisRun,
    result: dict[str, Any],
) -> list[AgentMemory]:
    memories: list[AgentMemory] = []
    hypotheses = result.get("hypotheses", [])
    hypothesis_lines = [
        f"- {item.get('title', '未命名假设')}（置信度 {item.get('confidence_score', 0):.2f}）"
        for item in hypotheses[:8]
    ]
    episodic_content = "\n".join([
        f"案例：{case.title}",
        f"设备：{case.device_type} {case.device_model or ''}".strip(),
        f"诊断摘要：{result.get('summary') or '无'}",
        "主要假设：",
        *(hypothesis_lines or ["- 未形成明确假设"]),
        f"涉及模块：{', '.join(result.get('suspected_modules', [])) or '未知'}",
    ])
    outcome = "SUCCESS" if hypotheses else "PARTIAL"
    episodic_confidence = max(
        [float(item.get("confidence_score", 0.0)) for item in hypotheses] or [0.35]
    )
    memories.append(upsert_memory(
        db,
        memory_type="EPISODIC",
        case_id=case.id,
        source_kind="analysis_run",
        source_id=run.id,
        title=f"{case.title}：诊断情景",
        content=episodic_content,
        context={
            "device_type": case.device_type,
            "device_model": case.device_model,
            "firmware_version": case.firmware_version,
            "suspected_modules": result.get("suspected_modules", []),
        },
        evidence=_hypothesis_evidence(result),
        outcome=outcome,
        confidence=episodic_confidence,
    ))

    actions = result.get("recommended_actions", [])
    if actions:
        action_lines = [
            (
                f"{index}. [{item.get('priority', 'UNKNOWN')}] "
                f"{item.get('action', '')}；原因：{item.get('reason', '')}；"
                f"期望：{item.get('expected_result', '')}"
            )
            for index, item in enumerate(actions[:30], start=1)
        ]
        memories.append(upsert_memory(
            db,
            memory_type="PROCEDURAL",
            case_id=None,
            source_kind="analysis_run",
            source_id=run.id,
            title=mask_sensitive(
                f"{case.device_type} {case.device_model or ''} 故障分析程序".strip()
            ),
            content=mask_sensitive("\n".join(action_lines)),
            context={
                "device_type": case.device_type,
                "device_model": mask_sensitive(case.device_model or "") or None,
                "suspected_modules": result.get("suspected_modules", []),
                "sanitized_for_global_reuse": True,
            },
            evidence=[],
            outcome=outcome,
            confidence=max(0.5, episodic_confidence),
        ))

    failure_items = [
        *[str(item) for item in result.get("missing_information", [])],
        *[str(item) for item in result.get("warnings", [])],
        *[str(item) for item in result.get("limitations", [])],
    ]
    if failure_items:
        memories.append(upsert_memory(
            db,
            memory_type="FAILURE",
            case_id=case.id,
            source_kind="analysis_run",
            source_id=run.id,
            title=f"{case.title}：诊断限制与失败经验",
            content="\n".join(f"- {item}" for item in failure_items[:50]),
            context={"analysis_engine": result.get("analysis_engine")},
            evidence=[],
            outcome="PARTIAL",
            confidence=0.7,
        ))
    return memories


def record_failed_analysis_memory(
    db: Session,
    case: Case,
    *,
    source_id: str | None,
    error_message: str,
) -> AgentMemory:
    return upsert_memory(
        db,
        memory_type="FAILURE",
        case_id=case.id,
        source_kind="analysis_run",
        source_id=source_id,
        title=f"{case.title}：分析任务失败",
        content=(
            f"失败类型：分析任务未完成\n"
            f"错误：{error_message[:4000]}\n"
            "复用规则：下次先检查相同错误、依赖、模型配置、输入完整性和任务状态。"
        ),
        context={
            "device_type": case.device_type,
            "device_model": case.device_model,
        },
        evidence=[],
        outcome="FAILED",
        confidence=0.9,
    )


def extract_memories_from_chat(
    db: Session,
    case: Case,
    *,
    message_id: str,
    question: str,
    answer: str,
    citations: list[dict[str, Any]],
) -> list[AgentMemory]:
    evidence = [
        {
            "evidence_id": item.get("evidence_id"),
            "source_type": item.get("source_type"),
        }
        for item in citations[:50]
        if item.get("evidence_id")
    ]
    memories = [upsert_memory(
        db,
        memory_type="EPISODIC",
        case_id=case.id,
        source_kind="case_chat",
        source_id=message_id,
        title=f"问答：{question[:180]}",
        content=f"问题：{question}\n回答：{answer[:12000]}",
        context={
            "device_type": case.device_type,
            "device_model": case.device_model,
        },
        evidence=evidence,
        outcome="SUCCESS" if citations else "PARTIAL",
        confidence=0.75 if citations else 0.45,
    )]
    procedural_intent = any(
        term in question.lower()
        for term in ("如何", "怎么", "步骤", "流程", "排查", "how", "steps", "procedure")
    )
    if procedural_intent:
        memories.append(upsert_memory(
            db,
            memory_type="PROCEDURAL",
            case_id=case.id,
            source_kind="case_chat",
            source_id=message_id,
            title=f"{case.title}：问答中形成的处理程序",
            content=answer[:12000],
            context={"question": question[:1000]},
            evidence=evidence,
            outcome="SUCCESS" if citations else "PARTIAL",
            confidence=0.65,
        ))
    failure_signal = not citations or any(
        term in answer.lower()
        for term in ("无法", "失败", "缺少", "未配置", "cannot", "failed", "missing")
    )
    if failure_signal:
        memories.append(upsert_memory(
            db,
            memory_type="FAILURE",
            case_id=case.id,
            source_kind="case_chat",
            source_id=message_id,
            title=f"{case.title}：问答限制",
            content=(
                f"问题：{question}\n"
                f"限制或失败信号：{answer[:6000]}"
            ),
            context={"citation_count": len(citations)},
            evidence=evidence,
            outcome="PARTIAL",
            confidence=0.6,
        ))
    return memories


def extract_memories_from_search(
    db: Session,
    case: Case,
    *,
    query: str,
    plan: dict[str, Any],
    traces: list[dict[str, Any]],
    results: list[dict[str, Any]],
    path_count: int,
) -> list[AgentMemory]:
    evidence = [
        {
            "evidence_id": item.get("evidence_id"),
            "source_type": item.get("source_type"),
            "title": str(item.get("title") or "")[:500],
        }
        for item in results[:30]
        if item.get("evidence_id")
    ]
    modules = [str(item) for item in plan.get("selected_modules", [])]
    search_dedup_key = (
        f"agentic_search|{case.id}|{query.strip()}|{','.join(sorted(modules))}"
    )
    memories = [upsert_memory(
        db,
        memory_type="EPISODIC",
        case_id=case.id,
        source_kind="agentic_search",
        source_id=None,
        title=f"认知检索：{query[:180]}",
        content="\n".join([
            f"问题：{query}",
            f"检索模块：{', '.join(modules) or '无'}",
            f"返回证据：{len(results)} 条",
            f"可解释路径：{path_count} 条",
            *[
                f"- [{item.get('source_type')}] {item.get('title')}"
                for item in results[:15]
            ],
        ]),
        context={
            "device_type": case.device_type,
            "device_model": case.device_model,
            "modules": modules,
            "intent_signals": plan.get("intent_signals", []),
        },
        evidence=evidence,
        outcome="RETRIEVED" if results else "NO_RESULT",
        confidence=0.75 if results else 0.4,
        dedup_key=search_dedup_key,
    )]
    failed_stages = [
        item for item in traces if item.get("status") == "FAILED"
    ]
    if failed_stages or not results:
        details = [
            (
                f"- {item.get('stage', 'unknown')}: "
                f"{item.get('error') or item.get('reason') or '未产生结果'}"
            )
            for item in failed_stages
        ]
        if not results:
            details.append("- 最终没有召回可用证据")
        memories.append(upsert_memory(
            db,
            memory_type="FAILURE",
            case_id=case.id,
            source_kind="agentic_search",
            source_id=None,
            title=f"认知检索限制：{query[:180]}",
            content="\n".join([
                f"问题：{query}",
                "失败或限制：",
                *details,
                "复用规则：下次先检查索引状态、模型配置、查询词和仓库历史可用性。",
            ]),
            context={"modules": modules},
            evidence=evidence,
            outcome="FAILED" if not results else "PARTIAL",
            confidence=0.8 if failed_stages else 0.55,
            dedup_key=f"agentic_search_failure|{search_dedup_key}",
        ))
    return memories


def memory_to_dict(memory: AgentMemory, score: float | None = None) -> dict[str, Any]:
    result = {
        "id": memory.id,
        "case_id": memory.case_id,
        "memory_type": memory.memory_type,
        "source_kind": memory.source_kind,
        "source_id": memory.source_id,
        "title": memory.title,
        "content": memory.content,
        "context": json_loads(memory.context_json, {}),
        "evidence": json_loads(memory.evidence_json, []),
        "outcome": memory.outcome,
        "confidence": memory.confidence,
        "occurrence_count": memory.occurrence_count,
        "reuse_count": memory.reuse_count,
        "last_used_at": memory.last_used_at,
        "created_at": memory.created_at,
        "updated_at": memory.updated_at,
    }
    if score is not None:
        result["score"] = round(score, 6)
    return result


def search_memories(
    db: Session,
    query: str,
    *,
    case_id: str | None,
    memory_types: set[str] | None = None,
    limit: int = 20,
) -> list[tuple[AgentMemory, float]]:
    scope = AgentMemory.case_id.is_(None)
    if case_id:
        scope = or_(AgentMemory.case_id == case_id, AgentMemory.case_id.is_(None))
    statement = select(AgentMemory).where(scope)
    if memory_types:
        statement = statement.where(AgentMemory.memory_type.in_({
            value.upper() for value in memory_types if value.upper() in MEMORY_TYPES
        }))
    memories = list(db.scalars(
        statement.order_by(AgentMemory.updated_at.desc()).limit(2000)
    ).all())
    query_tokens = tokenize(query)
    if not query_tokens:
        return []
    document_tokens = [
        tokenize(f"{memory.title}\n{memory.content}\n{memory.context_json}")
        for memory in memories
    ]
    document_frequency = Counter()
    for tokens in document_tokens:
        document_frequency.update(set(tokens))
    average_length = sum(map(len, document_tokens)) / max(len(document_tokens), 1)
    scored: list[tuple[AgentMemory, float]] = []
    for memory, tokens in zip(memories, document_tokens, strict=True):
        frequencies = Counter(tokens)
        score = 0.0
        matched = False
        for token in query_tokens:
            frequency = frequencies.get(token, 0)
            if not frequency:
                continue
            matched = True
            inverse_frequency = math.log(
                1
                + (len(memories) - document_frequency[token] + 0.5)
                / (document_frequency[token] + 0.5)
            )
            denominator = frequency + 1.5 * (
                1 - 0.75 + 0.75 * len(tokens) / max(average_length, 1)
            )
            score += inverse_frequency * frequency * 2.5 / denominator
        if not matched:
            continue
        overlap = len(set(query_tokens).intersection(tokens)) / max(len(set(query_tokens)), 1)
        score += overlap * 2.0
        score += memory.confidence * 0.5
        score += min(memory.occurrence_count, 5) * 0.05
        score += min(memory.reuse_count, 10) * 0.02
        if memory.outcome == "SUCCESS":
            score += 0.2
        if score > 0.25:
            scored.append((memory, score))
    return sorted(scored, key=lambda item: item[1], reverse=True)[:limit]


def mark_memories_reused(db: Session, memory_ids: set[str]) -> None:
    if not memory_ids:
        return
    for memory in db.scalars(select(AgentMemory).where(AgentMemory.id.in_(memory_ids))):
        memory.reuse_count += 1
        memory.last_used_at = utcnow()
