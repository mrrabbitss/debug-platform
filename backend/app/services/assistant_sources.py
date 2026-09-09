"""Relative bundle paths, immutable target snapshots and bounded reading receipts."""
import posixpath
import re
from urllib.parse import unquote, urlsplit
from sqlalchemy import select
from app.core.utils import json_dumps, json_loads, SECRET_PATTERNS
from app.models import KnowledgeDocument
from app.workbench_models import WorkbenchRecord
from app.services.assistant_state import digest

SEGMENT_CHARS = 4000
PAGE_SIZE = 8


def redacted_source(content):
    # Match before segmentation so a secret crossing a page boundary cannot leak.
    # Keep offsets and line endings identical to the administrator's source view.
    for pattern, _ in SECRET_PATTERNS:
        content = pattern.sub(lambda match: "".join(c if c in "\r\n" else "*" for c in match.group()), content)
    return content


def relative_path(name):
    if not isinstance(name, str):
        raise ValueError("文件相对路径无效")
    name = name.replace("\\", "/")
    parts = name.split("/")
    if (not name or len(name) > 512 or name.startswith("/") or any(x in {"", ".", ".."} for x in parts)
            or ":" in name or any(ord(c) < 32 or ord(c) == 127 for c in name)
            or any(x.endswith((".", " ")) for x in parts)):
        raise ValueError("文件相对路径无效")
    return name


def references(content):
    # Markdown links (including definition links), code paths and Skill YAML paths.
    links = [angle or plain for angle, plain in re.findall(r'\]\(\s*(?:<([^>\n]+)>|([^\s)>]+))', content)]
    links += [angle or plain for angle, plain in re.findall(r'^\s*\[[^\]]+\]:\s*(?:<([^>\n]+)>|([^\s>]+))', content, re.M)]
    links += re.findall(r'`([^`\n]+\.(?:md|markdown|txt|json|ya?ml|py|ps1|sh|c|h|cpp)(?:#[^`\n]*)?)`', content)
    links += re.findall(r'^\s*(?:-\s*)?(?:path|file|script|include):\s*[\'"]?([^\s\'"#]+)', content, re.M)
    return list(dict.fromkeys(links))


def resolve_reference(source_path, reference):
    parsed = urlsplit(reference.replace("\\", "/"))
    if parsed.scheme and parsed.scheme.lower() not in {"http", "https", "mailto"}:
        raise ValueError("依赖引用协议不安全")
    if parsed.scheme or parsed.netloc:
        return {"reference": reference, "external": True}
    path = unquote(parsed.path)
    target = posixpath.normpath(posixpath.join(posixpath.dirname(source_path), path)) if path else source_path
    if path.startswith("/") or target == ".." or target.startswith("../") or ":" in target:
        raise ValueError("依赖引用超出上传目录")
    return {"reference": reference, "path": relative_path(target), "fragment": parsed.fragment,
            "directory": path.endswith("/")}


def source_key(session_id, path):
    return "ASRC-" + digest([session_id, path])


def document_fingerprint(doc):
    return digest({key: getattr(doc, key) for key in ("content", "title", "version", "lock_version", "active",
        "review_status", "metadata_json", "confidentiality", "source_type", "trust_level", "device_type",
        "device_model", "firmware_range", "module")})


def snapshot_document(db, session_id, document_id, request_version=None):
    from fastapi import HTTPException
    from app.services.knowledge_access import require_knowledge_access
    try:
        doc = require_knowledge_access(db, document_id, session_principal(db, session_id))
    except HTTPException:
        raise ValueError("目标知识不存在或不在当前用户可见范围") from None
    if not doc:
        raise ValueError("目标知识不存在")
    if not doc.content.strip() or len(doc.content) > 1000000:
        raise ValueError("已有知识为空或超过100万字符；没有截断内容")
    path = "knowledge/" + doc.id
    key = source_key(session_id, path)
    row = db.get(WorkbenchRecord, key)
    snapshot = {"path": path, "target_id": doc.id, "title": doc.title, "content": doc.content,
        "sha256": digest(doc.content), "version": doc.version, "lock_version": doc.lock_version,
        "fingerprint": document_fingerprint(doc), "references": references(doc.content),
        "metadata": json_loads(doc.metadata_json, {}), "request_version": request_version}
    if row is not None:
        previous = json_loads(row.payload_json, {})
        if previous.get("request_version") == request_version and previous.get("fingerprint") != snapshot["fingerprint"]:
            raise ValueError("本轮已读目标版本发生变化，请纠偏后重新读取")
    if row is None:
        row = WorkbenchRecord(id=key, kind="assistant_source", owner_id=session_id, payload_json="{}")
        db.add(row)
    row.payload_json = json_dumps(snapshot)
    return snapshot


def get_source(db, value, session_id, path):
    for item in value.get("files", []):
        if item["path"] == path:
            if digest(item["content"]) != item.get("sha256"):
                raise ValueError("来源正文校验失败")
            return item
    row = db.get(WorkbenchRecord, source_key(session_id, path))
    if row and row.kind == "assistant_source" and row.owner_id == session_id:
        item = json_loads(row.payload_json, {})
        if digest(item["content"]) != item.get("sha256"):
            raise ValueError("目标快照校验失败")
        return item
    raise ValueError("来源未定位或未读取")


def receipt_key(session_id, item, start):
    return "AREAD-" + digest([session_id, item["path"], digest(item["content"]), start, SEGMENT_CHARS])


def reading_page(db, session_id, value, path, cursor=0):
    item = get_source(db, value, session_id, path)
    rows = []
    total = (len(item["content"]) + SEGMENT_CHARS - 1) // SEGMENT_CHARS
    for index in range(cursor, min(cursor + PAGE_SIZE, total)):
        row = db.get(WorkbenchRecord, receipt_key(session_id, item, index * SEGMENT_CHARS))
        rows.append(json_loads(row.payload_json, {}) if row else {"segment": index + 1, "complete": False})
    return {"items": rows, "total": total, "next_cursor": cursor + len(rows) if cursor + len(rows) < total else None}


def source_page(db, session_id, value, path, cursor=0):
    item = get_source(db, value, session_id, path)
    if cursor < 0 or cursor > len(item["content"]):
        raise ValueError("正文游标无效")
    end = min(cursor + SEGMENT_CHARS, len(item["content"]))
    return {"path": path, "content": item["content"][cursor:end], "start": cursor, "end": end,
        "total_characters": len(item["content"]), "sha256": item["sha256"],
        "next_cursor": end if end < len(item["content"]) else None}


def verify_receipts(db, session_id, value, item):
    total = (len(item["content"]) + SEGMENT_CHARS - 1) // SEGMENT_CHARS
    coverage = value.get("coverage", {}).get(item["path"], {})
    if (not total or coverage.get("sha256") != digest(item["content"]) or not coverage.get("complete")
            or coverage.get("read") != total or coverage.get("total") != total):
        raise ValueError("来源或目标尚未完整读取")
    for start in range(0, len(item["content"]), SEGMENT_CHARS):
        row = db.get(WorkbenchRecord, receipt_key(session_id, item, start))
        receipt = json_loads(row.payload_json, {}) if row else {}
        end = min(start + SEGMENT_CHARS, len(item["content"]))
        if (not receipt.get("complete") or receipt.get("sha256") != item["sha256"]
                or receipt.get("start") != start or receipt.get("end") != end
                or receipt.get("text_sha256") != digest(item["content"][start:end])):
            raise ValueError("全文阅读凭据缺失或校验失败")
        if value.get("model_snapshot") and receipt.get("model_fingerprint") != digest(value["model_snapshot"]):
            raise ValueError("阅读模型已变化，需要由本轮选定模型重新完整阅读")


def session_principal(db, session_id):
    from app.services.model_access import principal_for_model_user
    row = db.get(WorkbenchRecord, session_id)
    value = json_loads(row.payload_json, {}) if row else {}
    return principal_for_model_user(db, value.get("requested_by") or (row.owner_id if row else None))


def catalogue_page(db, cursor="", query="", category=None, role=None, *, session_id=None):
    """Keyset pagination never puts the entire catalogue or document metadata in a prompt."""
    statement = select(KnowledgeDocument).where(KnowledgeDocument.id > cursor).order_by(KnowledgeDocument.id)
    if session_id:
        from app.services.knowledge_access import visible_knowledge_clause
        statement = statement.where(visible_knowledge_clause(session_principal(db, session_id)))
    else:
        statement = statement.where(KnowledgeDocument.active.is_(True), KnowledgeDocument.review_status == "ACTIVE")
    if query:
        statement = statement.where(KnowledgeDocument.title.contains(query, autoescape=True)
                                    | KnowledgeDocument.content.contains(query, autoescape=True))
    items, scanned, next_cursor = [], 0, None
    # Bound the scan too, even when filtering legacy JSON metadata on SQLite.
    candidates = list(db.scalars(statement.limit(80)))
    for doc in candidates:
        scanned += 1
        next_cursor = doc.id
        meta = json_loads(doc.metadata_json, {})
        if category and category not in meta.get("problem_categories", ["general"]):
            continue
        if role and role != meta.get("knowledge_role"):
            continue
        items.append({"id": doc.id, "title": doc.title, "version": doc.version,
            "categories": meta.get("problem_categories", ["general"]), "role": meta.get("knowledge_role"),
            "characters": len(doc.content), "status": doc.review_status})
        from app.services.knowledge_access import knowledge_kind
        items[-1]["content_kind"] = knowledge_kind(doc)
        if len(items) == PAGE_SIZE:
            break
    more = scanned < len(candidates) or len(candidates) == 80
    return {"items": items, "next_cursor": next_cursor if more else None, "scanned": scanned}


def build_manifest(db, session_id, value, operations):
    mappings = {}
    for op in operations:
        target_id = op.get("target_id") or op.get("new_id")
        if op["action"] == "skip" and not op.get("target_id"):
            continue
        for path in op["source_paths"]:
            mappings.setdefault(path, []).append((target_id, op))
    sources = {item["path"]: item for item in value.get("files", [])}
    for path in mappings:
        sources.setdefault(path, get_source(db, value, session_id, path))
    manifest = []
    canonical = {path.casefold(): path for path in mappings}
    for path, links in mappings.items():
        item = sources[path]
        refs = []
        original_refs = references(item["content"]) if not item.get("target_id") else []
        for ref in original_refs:
            resolved = resolve_reference(path, ref)
            if not resolved.get("external"):
                resolved["path"] = canonical.get(resolved["path"].casefold(), resolved["path"])
                paths = [resolved["path"]]
                if resolved.get("directory"):
                    paths = [p for p in sources if p.casefold().startswith(resolved["path"].casefold() + "/")]
                if not paths or any(p not in mappings for p in paths):
                    raise ValueError("依赖文件未关联到可发布知识，请补齐来源或明确关联目标")
                destinations = list(dict.fromkeys(target for p in paths for target, _ in mappings.get(p, [])))
                if not destinations:
                    raise ValueError("依赖文件未关联到可发布知识，请补齐来源或明确关联目标")
                resolved.update(document_id=destinations[0], document_ids=destinations)
            refs.append(resolved)
        destinations = list(dict.fromkeys(key for key, _ in links))
        manifest.append({"path": path, "document_id": destinations[0], "document_ids": destinations,
            "sha256": digest(item["content"]), "references": refs, "original_references": original_refs,
            "resolved_references": refs,
            "source_ranges": [{**s, "document_id": key} for key, op in links
                              for s in op.get("sources", []) if s["path"] == path]})
    return manifest
