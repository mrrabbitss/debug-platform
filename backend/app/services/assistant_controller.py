"""Bounded read-only planning tools; all proposals become exact server-built diffs."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.core.db import SessionLocal
from app.core.utils import json_dumps
from app.services.assistant_plan import Decision, SourceSlice, materialize, review_digest, validate_review
from app.services.assistant_sources import (PAGE_SIZE, catalogue_page, snapshot_document, get_source,
    source_page, reading_page, verify_receipts, resolve_reference, redacted_source)
from app.services.workbench import categories, KNOWLEDGE_ROLES

MAX_STEPS = 96
MAX_SELECTED = 64


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["search", "read", "notes", "source", "files", "operations", "propose", "finish"]
    checkpoint: str = Field(default="", max_length=3000)
    query: str = Field(default="", max_length=500)
    catalogue_cursor: str = Field(default="", max_length=40)
    category: str | None = Field(default=None, max_length=80)
    role: str | None = Field(default=None, max_length=40)
    document_ids: list[str] = Field(default_factory=list, max_length=8)
    path: str = Field(default="", max_length=512)
    cursor: int = Field(default=0, ge=0)
    operations: list[Decision] = Field(default_factory=list, max_length=8)
    answer: str = Field(default="", max_length=8000)
    evidence: list[SourceSlice] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def appropriate_fields(self):
        fields = {"search": {"query", "catalogue_cursor", "category", "role"}, "read": {"document_ids"},
            "notes": {"path", "cursor"}, "source": {"path", "cursor"}, "files": {"cursor"},
            "operations": {"cursor"}, "propose": {"operations"}, "finish": {"answer", "evidence"}}
        for key in self.model_fields_set - fields[self.action] - {"action", "checkpoint"}:
            if getattr(self, key) not in (None, "", 0, []):
                raise ValueError("工具参数不属于此操作")
        if self.action == "read" and (not self.document_ids or any(not 1 <= len(key) <= 40 for key in self.document_ids)):
            raise ValueError("需要明确文档编号")
        if self.action == "propose" and not self.operations:
            raise ValueError("需要具体操作")
        if self.action == "finish" and not self.answer.strip():
            raise ValueError("需要对话回答")
        return self


def file_page(db, runtime, value, cursor=0):
    paths = list(dict.fromkeys([item["path"] for item in value.get("files", [])] + value.get("selected_paths", [])))
    items = []
    for path in paths[cursor:cursor + PAGE_SIZE]:
        item = get_source(db, value, runtime.session_id, path)
        items.append({"path": path, "characters": len(item["content"]), "sha256": item["sha256"],
            "target_id": item.get("target_id"), "version": item.get("version"),
            "coverage": value.get("coverage", {}).get(path, {})})
    return {"items": items, "total": len(paths), "next_cursor": cursor + len(items) if cursor + len(items) < len(paths) else None}


def operation_page(value, cursor):
    operations = value.get("pending_plan", [])
    items = [{key: op[key] for key in ("operation_id", "action", "title", "role", "target_id", "source_paths")}
             for op in operations[cursor:cursor + PAGE_SIZE]]
    return {"items": items, "total": len(operations),
        "next_cursor": cursor + len(items) if cursor + len(items) < len(operations) else None}


def dependencies(item):
    metadata = item.get("metadata", {})
    manifest = metadata.get("bundle_manifest", [])
    mapping = {entry["path"]: entry.get("document_ids") or [entry.get("document_id")] for entry in manifest}
    ids = []
    for entry in manifest:
        if entry["path"] not in metadata.get("source_paths", []):
            continue
        for ref in entry.get("references", []):
            resolved = ref if isinstance(ref, dict) else resolve_reference(entry["path"], ref)
            if not resolved.get("external"):
                found = resolved.get("document_ids") or mapping.get(resolved["path"], [])
                if not found or any(not key for key in found):
                    raise ValueError("已有Skill依赖未映射到知识，请先补齐关联")
                ids.extend(found)
    return list(dict.fromkeys(ids))


def read_documents(runtime, ids):
    pending, seen, result = list(ids), set(), []
    while pending:
        key = pending.pop(0)
        if key in seen:
            continue
        seen.add(key)
        with SessionLocal() as db:
            row, value = runtime.state(db)
            paths = value.get("selected_paths", [])
            if "knowledge/" + key not in paths and len(paths) >= MAX_SELECTED:
                raise ValueError("本次定位知识超过64篇；请缩小请求，没有截断正文")
            item = snapshot_document(db, runtime.session_id, key, runtime.version)
            paths = list(dict.fromkeys([*paths, item["path"]]))
            value["selected_paths"] = paths
            row.payload_json = json_dumps(value)
            db.commit()
        runtime.read(item["path"])
        pending.extend(dep for dep in dependencies(item) if dep not in seen)
        result.append(item["path"])
    return {"read_paths": result, "complete": True, "notes_tool": "notes", "source_tool": "source"}


def finish_plan(db, runtime, row, value, step):
    evidence = []
    for source in step.evidence:
        item = get_source(db, value, runtime.session_id, source.path)
        verify_receipts(db, runtime.session_id, value, item)
        end = source.end if source.end is not None else len(item["content"])
        if not 0 <= source.start < end <= len(item["content"]):
            raise ValueError("回答证据区间无效")
        evidence.append({"path": source.path, "start": source.start, "end": end, "sha256": item["sha256"],
                         "document_id": item.get("target_id"), "document_version": item.get("version")})
    if (value.get("files") or value.get("selected_paths")) and not evidence:
        raise ValueError("回答必须引用已读来源的具体区间")
    value.update(plan=value.get("pending_plan", []), answer=step.answer, answer_evidence=evidence)
    if any(op["action"] != "skip" for op in value["plan"]):
        value["bundle_manifest"] = validate_review(db, runtime.session_id, value)
    value.update(status="REVIEW", error=None, planning_checkpoint=None)
    value["review_digest"] = review_digest(value)
    value.setdefault("messages", []).append({"role": "assistant", "content": step.answer, "evidence": evidence})
    row.payload_json = json_dumps(value)
    result = {"session_id": runtime.session_id, "operations": len(value["plan"])}
    runtime.ctx.complete_in_transaction(db, result, "全文阅读与草稿完成，等待管理员核对")
    return result


def execute(runtime, step):
    if step.action == "read":
        return read_documents(runtime, step.document_ids)
    with SessionLocal() as db:
        row, value = runtime.state(db)
        if step.action == "search":
            return catalogue_page(db, step.catalogue_cursor, step.query, step.category, step.role, session_id=runtime.session_id)
        if step.action == "files":
            return file_page(db, runtime, value, step.cursor)
        if step.action == "operations":
            return operation_page(value, step.cursor)
        if step.action in {"notes", "source"}:
            function = reading_page if step.action == "notes" else source_page
            item = get_source(db, value, runtime.session_id, step.path)
            verify_receipts(db, runtime.session_id, value, item)
            result = function(db, runtime.session_id, value, step.path, step.cursor)
            if step.action == "source":
                result["content"] = redacted_source(item["content"])[result["start"]:result["end"]]
            return result
        if step.action == "finish":
            result = finish_plan(db, runtime, row, value, step)
            db.commit()
            return result
        plan = value.setdefault("pending_plan", [])
        for proposal in step.operations:
            plan.append(materialize(db, runtime.session_id, value, proposal.model_dump(), plan))
        if len(plan) > 128 or sum(len(op["before"]) + len(op["after"]) + len(op["diff"]) for op in plan) > 16000000:
            raise ValueError("具体方案超过128项或1600万字符，请缩小请求；没有截断方案")
        value["planning_checkpoint"] = {"summary": step.checkpoint, "action": step.action,
                                        "result": operation_page(value, max(0, len(plan) - len(step.operations)))}
        row.payload_json = json_dumps(value)
        db.commit()
        return operation_page(value, max(0, len(plan) - len(step.operations)))


def plan(runtime):
    from app.services.assistant_runtime import RunPaused
    for _ in range(MAX_STEPS):
        with SessionLocal() as db:
            _, value = runtime.state(db)
            payload = {"task": "回答请求并提出可核对的具体方案。可用分页工具search检索目录、read全文读取已有知识及依赖、"
                "files分页列来源、notes分页读段落记录、source按字符游标读原文、operations分页核对草稿。"
                "propose可分批追加多项草稿，finish给出带来源区间证据的自然语言回答。"
                "所有上传文件已经分段阅读全文；你可随时重读notes/source；每段含真实字符区间。"
                "上传文件的每一字符都必须归入操作来源区间，允许重叠和skip明确跳过。"
                "单文件可拆成多个create/merge/replace/link/skip，多个merge可以顺序修改同一目标。"
                "已有目标须先read。替换/新增使用sources原文或唯一锚点edits；无上传的修改可使用已有知识作来源。"
                "mode=answer仅回答。auto根据用户最新请求选择回答或修改；问答不要propose修改。"
                "先比较search目录里的已有知识再决定新增。根SKILL依赖文件必须保留到新建或已有关联目标。"
                "每项操作明确content_kind：SKILL是强制诊断方法；KNOWLEDGE是案例/Wiki普通知识，不进入必读方法。"
                "修改已有目标时保留其类型；仅在用户明确要求并核对差异时提出类型转换。"
                "上下文仅保留上一工具结果与checkpoint；更早的原文和阅读记录全部可通过分页工具重读，未被删除。",
                "mode": value.get("mode", "auto"), "conversation": value["messages"],
                "files": file_page(db, runtime, value), "catalogue": catalogue_page(db, session_id=runtime.session_id),
                "categories": categories(db), "roles": KNOWLEDGE_ROLES,
                "pending_operations": len(value.get("pending_plan", [])),
                "last_step": value.get("planning_checkpoint")}
        step = runtime.call(payload, Step, "knowledge_assistant_plan")
        result = execute(runtime, step)
        if step.action == "finish":
            return result
        runtime.save({"planning_checkpoint": {"summary": step.checkpoint, "action": step.action, "result": result}})
    raise RunPaused("本次规划已达96步；草稿与阅读记录已保存，请继续任务")
