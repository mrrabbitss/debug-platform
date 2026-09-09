"""Validate model proposals against fully read immutable sources and exact anchors."""
from difflib import unified_diff
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
from app.core.utils import new_id
from app.models import KnowledgeDocument
from app.services.workbench import categories
from app.services.assistant_state import digest
from app.services.assistant_sources import document_fingerprint, get_source, build_manifest, relative_path, verify_receipts


class TextEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    old: str = Field(min_length=1, max_length=12000)
    new: str = Field(max_length=20000)


class SourceSlice(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str = Field(min_length=1, max_length=512)
    start: int = Field(default=0, ge=0)
    end: int | None = Field(default=None, ge=1)

    @field_validator("path")
    @classmethod
    def valid_path(cls, value):
        return relative_path(value)


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["create", "merge", "replace", "link", "skip"]
    title: str = Field(default="", max_length=255)
    categories: list[str] = Field(default_factory=list, max_length=10)
    role: Literal["log_analysis", "diagnosis", "fault_tree", "report_template", "prior_knowledge"] = "prior_knowledge"
    content_kind: Literal["SKILL", "KNOWLEDGE"] | None = None
    target_id: str | None = Field(default=None, min_length=1, max_length=40)
    reason: str = Field(min_length=1, max_length=6000)
    sources: list[SourceSlice] = Field(default_factory=list, max_length=128)
    edits: list[TextEdit] = Field(default_factory=list, max_length=100)
    append_source: bool = False


def candidate_content(decision, source, target):
    if decision.append_source and (decision.action != "merge" or not source):
        raise ValueError("仅合并可追加明确的来源正文")
    if decision.action in {"skip", "link"}:
        if decision.edits or decision.append_source:
            raise ValueError("跳过或关联不能修改正文")
        return target or ""
    result = source if decision.action in {"create", "replace"} else (target or "")
    for edit in decision.edits:
        if result.count(edit.old) != 1:
            raise ValueError("修改锚点不唯一或已变化，请读取目标原文并明确位置")
        result = result.replace(edit.old, edit.new, 1)
    if decision.append_source and decision.action == "merge":
        result += "\n\n" + source
    return result


def exact_diff(before, after):
    lines = unified_diff(before.splitlines(True), after.splitlines(True), fromfile="原文", tofile="拟发布版本")
    return "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in lines)


def require_read(value, item):
    coverage = value.get("coverage", {}).get(item["path"], {})
    if not coverage.get("complete") or coverage.get("sha256") != digest(item["content"]):
        raise ValueError("来源或目标尚未完整读取")


def validate_target(document, operation):
    if operation.get("target_id") and (document is None
            or document.version != operation.get("expected_version")
            or document.lock_version != operation.get("expected_lock")
            or digest(document.content) != operation.get("expected_sha256")
            or document_fingerprint(document) != operation.get("expected_fingerprint")):
        raise ValueError("目标知识已变化，请重新读取并核对差异")


def materialize(db, session_id, value, raw, previous):
    proposal = Decision.model_validate(raw)
    if value.get("mode") == "answer" and proposal.action != "skip":
        raise ValueError("本轮是只读问答，不能提出知识变更")
    if proposal.action != "skip" and (not proposal.title.strip() or not proposal.categories
            or set(proposal.categories) - {c["id"] for c in categories(db)}):
        raise ValueError("标题或分类无效")
    source_texts, slices = [], []
    for source in proposal.sources:
        item = get_source(db, value, session_id, source.path)
        verify_receipts(db, session_id, value, item)
        end = source.end if source.end is not None else len(item["content"])
        if not 0 <= source.start < end <= len(item["content"]):
            raise ValueError("来源章节区间无效")
        source_texts.append(item["content"][source.start:end])
        slices.append({"path": source.path, "start": source.start, "end": end, "sha256": digest(item["content"])})
    target = db.get(KnowledgeDocument, proposal.target_id) if proposal.target_id else None
    if proposal.action in {"merge", "replace", "link"} and target is None:
        raise ValueError("操作缺少有效目标")
    if proposal.action == "create" and proposal.target_id:
        raise ValueError("新增不能覆盖已有目标")
    target_item = None
    if proposal.target_id:
        if not target:
            raise ValueError("关联目标不存在")
        target_item = get_source(db, value, session_id, "knowledge/" + target.id)
        verify_receipts(db, session_id, value, target_item)
        if target_item["fingerprint"] != document_fingerprint(target):
            raise ValueError("已读目标版本发生变化")
    before = target.content if target else ""
    for op in previous:
        if proposal.target_id and op.get("target_id") == proposal.target_id and op["action"] != "skip":
            before = op["after"]
    after = candidate_content(proposal, "\n\n".join(source_texts), before)
    if proposal.action != "skip" and (not after.strip() or len(after) > 1000000):
        raise ValueError("候选正文为空或超过100万字符")
    if proposal.action in {"create", "replace"} and not slices:
        raise ValueError("新增或替换必须指明已完整读取的来源")
    op = {**proposal.model_dump(), "operation_id": new_id("AOP"), "sources": slices,
        "source_paths": list(dict.fromkeys(s["path"] for s in slices)), "before": before, "after": after,
        "expected_version": target.version if target else None, "expected_lock": target.lock_version if target else None,
        "expected_sha256": digest(target.content) if target else None,
        "expected_fingerprint": document_fingerprint(target) if target else None,
        "new_id": new_id("DOC") if proposal.action == "create" else None,
        "diff": exact_diff(before, after)}
    from app.services.knowledge_access import knowledge_kind
    op["content_kind"] = proposal.content_kind or (knowledge_kind(target) if target else "SKILL")
    if target and not op["source_paths"]:
        op["source_paths"] = [target_item["path"]]
    return op


def review_digest(value):
    return digest({key: value.get(key) for key in ("plan", "coverage", "bundle_manifest", "mode", "request_version",
        "model_egress_approved", "answer", "answer_evidence")})


def validate_review(db, session_id, value):
    if value.get("mode") == "answer":
        raise ValueError("问答只解释知识，不执行变更")
    operations = value.get("plan", [])
    if not any(op["action"] != "skip" for op in operations):
        raise ValueError("没有需要发布的知识变更")
    for item in value.get("files", []):
        verify_receipts(db, session_id, value, item)
        intervals = sorted((s["start"], s["end"]) for op in operations for s in op.get("sources", []) if s["path"] == item["path"])
        end = 0
        for start, stop in intervals:
            if start > end:
                raise ValueError("存在尚未安排操作的来源章节；请明确保留或跳过")
            end = max(end, stop)
        if end != len(item["content"]):
            raise ValueError("存在尚未安排操作的来源章节；请明确保留或跳过")
    allowed = {c["id"] for c in categories(db)}
    previous = {}
    for op in operations:
        raw = {key: op[key] for key in Decision.model_fields if key in op}
        raw["sources"] = [{key: source[key] for key in SourceSlice.model_fields if key in source}
                          for source in op.get("sources", [])]
        proposal = Decision.model_validate(raw)
        target = db.get(KnowledgeDocument, op["target_id"]) if op.get("target_id") else None
        validate_target(target, op)
        if target:
            verify_receipts(db, session_id, value, get_source(db, value, session_id, "knowledge/" + target.id))
        if op["action"] != "skip" and (set(op["categories"]) - allowed):
            raise ValueError("方案分类已失效")
        if target and op["action"] != "skip" and target.confidentiality not in {"PUBLIC", "INTERNAL"}:
            raise ValueError("受限知识不能通过全员发布方案扩展权限")
        texts = []
        for source in proposal.sources:
            item = get_source(db, value, session_id, source.path)
            verify_receipts(db, session_id, value, item)
            end = source.end if source.end is not None else len(item["content"])
            if not 0 <= source.start < end <= len(item["content"]):
                raise ValueError("来源章节区间无效")
            texts.append(item["content"][source.start:end])
        before = previous.get(target.id, target.content) if target else ""
        after = candidate_content(proposal, "\n\n".join(texts), before)
        if op["before"] != before or op["after"] != after or op["diff"] != exact_diff(before, after):
            raise ValueError("具体差异与来源不一致，请重新整理")
        if target and op["action"] != "skip":
            previous[target.id] = after
    manifest = build_manifest(db, session_id, value, operations)
    publishing = {op.get("target_id") or op.get("new_id") for op in operations if op["action"] != "skip"}
    dependencies = {key for entry in manifest for ref in entry.get("resolved_references", [])
                    for key in ref.get("document_ids", [])}
    for key in dependencies - publishing:
        document = db.get(KnowledgeDocument, key)
        if not document or not document.active or document.review_status != "ACTIVE" or document.confidentiality not in {"PUBLIC", "INTERNAL"}:
            raise ValueError("保留的依赖目标尚未向全员发布，不能仅跳过来源")
    if value.get("bundle_manifest") is not None and manifest != value["bundle_manifest"]:
        raise ValueError("依赖清单发生变化，请重新核对")
    return manifest
