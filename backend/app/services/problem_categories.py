"""Versioned problem categories and explicit coverage of published diagnostic Skills."""
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.core.utils import json_dumps, json_loads, new_id
from app.models import AuditEvent, KnowledgeDocument
from app.workbench_models import WorkbenchRecord


def category_rows(db, *, include_inactive=False):
    from app.services.workbench import DEFAULT_CATEGORIES
    items = {item["id"]: {**item, "version": 1, "active": True} for item in DEFAULT_CATEGORIES}
    for row in db.scalars(select(WorkbenchRecord).where(WorkbenchRecord.kind == "problem_category")
                          .order_by(WorkbenchRecord.created_at, WorkbenchRecord.id)):
        value = json_loads(row.payload_json, {})
        items[value["id"]] = {**value, "version": row.version, "active": value.get("active", True)}
    return [item for item in items.values() if include_inactive or item["active"]]


def published_skills(db):
    from app.services.knowledge_access import knowledge_kind
    return [row for row in db.scalars(select(KnowledgeDocument).where(
        KnowledgeDocument.active.is_(True), KnowledgeDocument.review_status == "ACTIVE",
        KnowledgeDocument.confidentiality.in_(["PUBLIC", "INTERNAL"]))) if knowledge_kind(row) == "SKILL"]


def skill_status(category, documents):
    scopes = [getattr(row, "problem_categories", None) or
              json_loads(getattr(row, "metadata_json", "{}"), {}).get("problem_categories", ["general"])
              for row in documents]
    general = sum("general" in scope for scope in scopes)
    dedicated = sum(category in scope and "general" not in scope for scope in scopes)
    if category == "unknown" and documents:
        return {"mode": "CROSS_CATEGORY", "count": len(documents), "warning":
                "问题类别未知：跨类查找已发布 Skill；诊断将说明适用范围并在有证据时建议类别。"}
    if dedicated or (category == "general" and general):
        return {"mode": "DEDICATED", "count": dedicated + general, "warning": None}
    if general:
        return {"mode": "GENERAL_ONLY", "count": general,
                "warning": "缺少专属 Skill：本次使用通用 Skill，并依据日志证据诊断。"}
    return {"mode": "EVIDENCE_ONLY", "count": 0,
            "warning": "未使用任何 Skill：此类别与通用知识均无已发布 Skill，仍允许仅按日志证据诊断。"}


def categories_with_status(db):
    documents = published_skills(db)
    return [{**item, "skill_status": skill_status(item["id"], documents)} for item in category_rows(db)]


def add_diagnosis_skill_warning(result, plan):
    warning = plan.get("method_coverage", {}).get("skill_status", {}).get("warning")
    if warning:
        result.setdefault("warnings", []).append(warning)
        result.setdefault("limitations", []).append(warning)


def _lock_catalogue(db):
    key = "problem-category-catalogue"
    if db.get(WorkbenchRecord, key) is None:
        try:
            with db.begin_nested():
                db.add(WorkbenchRecord(id=key, kind="category_catalogue", payload_json="{}"))
                db.flush()
        except IntegrityError:
            pass
    db.execute(update(WorkbenchRecord).where(WorkbenchRecord.id == key)
               .values(version=WorkbenchRecord.version + 1).execution_options(synchronize_session=False))
    db.expire_all()


def change_category(db, actor, *, category_id=None, version=None, name=None, deactivate=False):
    _lock_catalogue(db)
    options = category_rows(db, include_inactive=True)
    current = next((item for item in options if item["id"] == category_id), None)
    if category_id and (not current or current["version"] != version or not current["active"]):
        raise ValueError("问题类别已变化，请刷新后重试")
    if deactivate:
        if category_id in {"general", "unknown"}:
            raise ValueError("未知与通用是诊断回退类别，不能停用")
        for doc in db.scalars(select(KnowledgeDocument).where(KnowledgeDocument.active.is_(True))):
            if category_id in json_loads(doc.metadata_json, {}).get("problem_categories", []):
                raise ValueError("此类别仍有生效知识或 Skill，请先将内容移到其他类别并发布")
    else:
        name = (name or "").strip()
        if not name or len(name) > 60:
            raise ValueError("分类名称须为1至60字")
        if any(item["name"].casefold() == name.casefold() and item["id"] != category_id and item["active"]
               for item in options):
            raise ValueError("分类名称已存在")
    row = next((row for row in db.scalars(select(WorkbenchRecord).where(WorkbenchRecord.kind == "problem_category"))
                if json_loads(row.payload_json, {}).get("id") == category_id), None) if category_id else None
    if row is None:
        row = WorkbenchRecord(id=new_id("PCAT"), kind="problem_category", owner_id=actor)
        db.add(row)
        if current:
            # Persist the implicit built-in version before applying its first
            # edit; SQLAlchemy always initializes inserted version counters at 1.
            row.payload_json = json_dumps({key: current[key] for key in ("id", "name", "active")})
            db.flush()
    category_id = category_id or row.id
    row.payload_json = json_dumps({"id": category_id, "name": name or current["name"], "active": not deactivate})
    db.add(AuditEvent(id=new_id("AUD"), actor_id=actor, actor_type="user",
        action="knowledge.category." + ("deactivated" if deactivate else "updated" if current else "created"),
        resource_type="problem_category", resource_id=category_id, outcome="SUCCESS",
        details_json=json_dumps({"previous_version": version, "content_recorded": False})))
    db.flush()
    return {**json_loads(row.payload_json, {}), "version": row.version}
