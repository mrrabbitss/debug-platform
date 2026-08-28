import hashlib
import re
from copy import deepcopy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.utils import json_dumps, json_loads, new_id, utcnow
from app.models import KnowledgeDerivation, KnowledgeDocument
from app.services.knowledge_taxonomy import get_default_category_id, set_document_category


STRUCTURED_SOURCE_TYPES = {
    "fault_case",
    "fault_tree",
    "historical_bug",
    "analysis_skill",
}

FAULT_CASE_TEMPLATE = """# 故障案例：请填写标题

## 错误形式

- 用户可见现象：
- 错误码/告警：
- 影响范围：
- 触发条件：

## 日志分析

- 关键日志模式：
- 首次异常时间：
- 前置事件：
- 后续连锁事件：
- 正常样本对比：

## 错误定位

1. 需要确认的输入和环境：
2. 排查顺序：
3. 分支判断条件：
4. 最终根因：
5. 相关模块、文件、函数或 Commit：

## 解决方案

1. 修复或配置步骤：
2. 风险和影响：
3. 回退方案：

## 验证结果

- 验证步骤：
- 期望结果：
- 实际结果：
- 是否复发：

## 适用范围与限制

- 设备型号：
- 固件版本：
- 不适用场景：
"""

HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
LIST_PREFIX = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)、]\s*)")
HEADING_ALIASES = {
    "error_form": (
        "错误形式",
        "故障现象",
        "错误现象",
        "问题现象",
        "适用场景",
        "输入信号",
        "symptom",
        "error pattern",
        "failure pattern",
    ),
    "log_analysis": (
        "日志分析",
        "关键日志",
        "日志证据",
        "日志检查",
        "证据收集",
        "log analysis",
        "log evidence",
    ),
    "localization": (
        "错误定位",
        "故障定位",
        "根因定位",
        "定位过程",
        "分析步骤",
        "排查步骤",
        "决策流程",
        "工作流",
        "root cause",
        "localization",
        "procedure",
        "workflow",
    ),
    "solution": (
        "解决方案",
        "处理方案",
        "修复方案",
        "solution",
        "fix",
        "remediation",
    ),
    "validation": (
        "验证结果",
        "验证方法",
        "回归验证",
        "validation",
        "verification",
    ),
    "scope": (
        "适用范围",
        "限制",
        "边界",
        "scope",
        "limitations",
    ),
}
REQUIRED_FAULT_CASE_SECTIONS = ("error_form", "log_analysis", "localization", "solution")


def _normalize_heading(value: str) -> str:
    return re.sub(r"[\s:：/\\_-]+", " ", value.strip().lower()).strip()


def _section_key(heading: str) -> str | None:
    normalized = _normalize_heading(heading)
    for key, aliases in HEADING_ALIASES.items():
        if any(_normalize_heading(alias) in normalized for alias in aliases):
            return key
    return None


def _has_substantive_content(text: str) -> bool:
    for raw_line in text.splitlines():
        line = LIST_PREFIX.sub("", raw_line).strip()
        if not line or line.startswith(("```", "~~~", "<!--")):
            continue
        if line.endswith((":", "：")):
            continue
        if re.sub(r"[-*_`#>\s]", "", line):
            return True
    return False


def parse_markdown_sections(content: str) -> dict[str, Any]:
    title = ""
    raw_sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    preamble: list[str] = []
    fence_marker: str | None = None
    for line in content.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        stripped = line.lstrip()
        marker = stripped[:3] if stripped.startswith(("```", "~~~")) else None
        if marker:
            if fence_marker is None:
                fence_marker = marker
            elif marker == fence_marker:
                fence_marker = None
            if current is None:
                preamble.append(line)
            else:
                current["lines"].append(line)
            continue
        heading_match = HEADING_PATTERN.match(line) if fence_marker is None else None
        if heading_match:
            level = len(heading_match.group(1))
            heading = heading_match.group(2).strip()
            if level == 1 and not title and _section_key(heading) is None:
                title = heading
                current = None
                continue
            current = {"heading": heading, "level": level, "lines": []}
            raw_sections.append(current)
            continue
        if current is None:
            preamble.append(line)
        else:
            current["lines"].append(line)

    structured: dict[str, str] = {}
    other_sections: list[dict[str, str]] = []
    for section in raw_sections:
        text = "\n".join(section["lines"]).strip()
        key = _section_key(section["heading"])
        if key:
            if text:
                structured[key] = (
                    f"{structured[key]}\n\n{text}".strip() if key in structured else text
                )
        elif text:
            other_sections.append({
                "heading": str(section["heading"]),
                "content": text,
            })

    missing = [
        key
        for key in REQUIRED_FAULT_CASE_SECTIONS
        if not _has_substantive_content(structured.get(key, ""))
    ]
    completed = len(REQUIRED_FAULT_CASE_SECTIONS) - len(missing)
    return {
        "format": "fault_case_markdown_v1",
        "title": title,
        "preamble": "\n".join(preamble).strip(),
        "sections": structured,
        "other_sections": other_sections,
        "required_sections": list(REQUIRED_FAULT_CASE_SECTIONS),
        "missing_sections": missing,
        "complete": not missing,
        "completeness": round(completed / len(REQUIRED_FAULT_CASE_SECTIONS), 3),
    }


def enrich_knowledge_metadata(
    source_type: str,
    content: str,
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    result = deepcopy(metadata or {})
    if source_type in STRUCTURED_SOURCE_TYPES:
        result["markdown_structure"] = parse_markdown_sections(content)
        result["knowledge_format"] = (
            "analysis_skill_markdown"
            if source_type == "analysis_skill"
            else "structured_fault_case_markdown"
        )
    elif source_type == "analysis_method":
        result["knowledge_format"] = "extracted_analysis_method_markdown"
    else:
        result.pop("markdown_structure", None)
    return result


def _clean_items(text: str) -> list[str]:
    items: list[str] = []
    seen: set[str] = set()
    for raw_line in text.splitlines():
        line = LIST_PREFIX.sub("", raw_line).strip()
        if not line or line.startswith("#") or line.endswith((":", "：")):
            continue
        normalized = re.sub(r"\s+", " ", line).strip("：: ")
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        items.append(normalized)
    return items


def _render_list(items: list[str], fallback: str) -> str:
    values = items or [fallback]
    return "\n".join(f"{index}. {value}" for index, value in enumerate(values, start=1))


def extract_analysis_method(source: KnowledgeDocument) -> tuple[str, dict[str, Any]]:
    parsed = parse_markdown_sections(source.content)
    sections: dict[str, str] = parsed["sections"]
    signals = _clean_items(sections.get("error_form", ""))
    log_steps = _clean_items(sections.get("log_analysis", ""))
    localization_steps = _clean_items(sections.get("localization", ""))
    solution_steps = _clean_items(sections.get("solution", ""))
    validation_steps = _clean_items(sections.get("validation", ""))
    fallback_steps: list[str] = []
    for section in parsed["other_sections"]:
        fallback_steps.extend(_clean_items(section["content"]))

    analysis_steps = log_steps + [
        item for item in localization_steps if item not in set(log_steps)
    ]
    if not analysis_steps:
        analysis_steps = fallback_steps
    if not any((signals, analysis_steps, solution_steps, validation_steps)):
        raise ValueError(
            "The document does not contain extractable Markdown headings or analysis steps"
        )

    decision_points = [
        item
        for item in analysis_steps
        if any(term in item.lower() for term in ("如果", "若", "否则", "when", "if ", "then"))
    ]
    source_title = parsed["title"] or source.title
    method_title = f"{source_title}：可复用错误分析方法"
    method_content = f"""# {method_title}

> 由知识文档 `{source.id}` 确定性提炼；只重组原文，不补造新的故障结论。

## 适用场景与输入信号

{_render_list(signals, "根据原始案例标题、设备范围和故障描述确认适用性。")}

## 日志与证据检查顺序

{_render_list(log_steps, "先收集完整日志并确定首次异常时间和上下文。")}

## 定位步骤

{_render_list(localization_steps or analysis_steps, "按证据强度逐步缩小模块、文件和函数范围。")}

## 分支决策点

{_render_list(decision_points, "每一步都记录支持证据和反证；证据不足时停止下结论。")}

## 解决与回退

{_render_list(solution_steps, "只采用经过评审的最小修复，并准备可执行回退。")}

## 验证与结束条件

{_render_list(validation_steps, "复现原问题、验证修复结果并执行相关回归测试。")}

## 来源与限制

- 来源文档：`{source.id}` / {source.title}
- 来源类型：`{source.source_type}`
- 结构完整度：{parsed["completeness"]:.0%}
- 缺失章节：{", ".join(parsed["missing_sections"]) or "无"}
- 本方法是检索和诊断候选，不替代当前设备证据与工程师复核。
"""
    fingerprint = hashlib.sha256(method_content.encode("utf-8")).hexdigest()
    metadata = {
        "derived_from_document_id": source.id,
        "derivation_type": "analysis_method",
        "extraction_mode": "deterministic_markdown_v1",
        "source_structure": parsed,
        "method_fingerprint": fingerprint,
        "extracted_at": utcnow().isoformat(),
        "signals": signals,
        "analysis_steps": analysis_steps,
        "solution_steps": solution_steps,
        "validation_steps": validation_steps,
    }
    return method_content, metadata


def analysis_method_is_unmodified(document: KnowledgeDocument) -> bool:
    metadata = json_loads(document.metadata_json, {})
    expected = str(metadata.get("method_fingerprint") or "")
    actual = hashlib.sha256(document.content.encode("utf-8")).hexdigest()
    return bool(expected) and expected == actual


def derive_analysis_method(
    db: Session,
    source: KnowledgeDocument,
) -> tuple[KnowledgeDocument, bool]:
    from app.services.knowledge import index_document

    source.metadata_json = json_dumps(enrich_knowledge_metadata(
        source.source_type,
        source.content,
        json_loads(source.metadata_json, {}),
    ))
    method_content, method_metadata = extract_analysis_method(source)
    derivation = db.scalar(
        select(KnowledgeDerivation).where(
            KnowledgeDerivation.source_document_id == source.id,
            KnowledgeDerivation.derivation_type == "analysis_method",
        )
    )
    created = derivation is None
    derived = db.get(KnowledgeDocument, derivation.derived_document_id) if derivation else None
    if derived is None:
        derived = KnowledgeDocument(
            id=new_id("DOC"),
            title=f"{source.title}：分析方法",
            source_type="analysis_method",
            device_type=source.device_type,
            device_model=source.device_model,
            firmware_range=source.firmware_range,
            module=source.module,
            trust_level=source.trust_level,
            confidentiality=source.confidentiality,
            content=method_content,
            metadata_json=json_dumps(method_metadata),
            active=False,
            review_status="DRAFT",
        )
        db.add(derived)
        db.flush()
        set_document_category(
            db,
            derived.id,
            get_default_category_id(db, "analysis_method"),
        )
        derivation = KnowledgeDerivation(
            id=new_id("KDRV"),
            source_document_id=source.id,
            derived_document_id=derived.id,
            derivation_type="analysis_method",
            metadata_json=json_dumps({"mode": "deterministic_markdown_v1"}),
        )
        db.add(derivation)
    else:
        derived.title = f"{source.title}：分析方法"
        derived.device_type = source.device_type
        derived.device_model = source.device_model
        derived.firmware_range = source.firmware_range
        derived.module = source.module
        derived.trust_level = source.trust_level
        derived.confidentiality = source.confidentiality
        derived.content = method_content
        derived.metadata_json = json_dumps(method_metadata)
        derivation.updated_at = utcnow()

    from app.services.knowledge_governance import (
        advance_document_version,
        create_document_revision,
    )

    if not created:
        advance_document_version(
            db,
            derived,
            created_by="derivation-engine",
            change_summary=f"Regenerated from source {source.id}",
        )
    source_metadata = json_loads(source.metadata_json, {})
    source_metadata["derived_analysis_method_id"] = derived.id
    source.metadata_json = json_dumps(source_metadata)
    # Existing published methods have already transitioned to DRAFT before
    # index_document performs its internal commits.
    index_document(db, derived)
    if created:
        create_document_revision(
            db,
            derived,
            created_by="derivation-engine",
            change_summary="Initial deterministic method extraction",
        )
    db.commit()
    db.refresh(derived)
    return derived, created
