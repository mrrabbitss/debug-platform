"""Case-owner submissions and expert review, preserving original reports."""
from app.core.utils import json_dumps, json_loads, utcnow
from app.models import AnalysisRun, Case
from app.services.access_control import case_permission
from app.services.workbench import validate_case_options
from app.workbench_models import WorkbenchRecord


def prepare_submission(db, identity, payload):
    if identity.get("role") not in {"ADMIN", "EXPERT", "ENGINEER"}:
        raise PermissionError("只读账号不能提交案例")
    validate_case_options(db, payload)
    snapshot = dict(payload)
    case_id, analysis_id = payload.get("case_id"), payload.get("analysis_id")
    if analysis_id and not case_id:
        raise ValueError("提交报告必须关联来源案例")
    if case_id:
        case = db.get(Case, case_id)
        if not case:
            raise LookupError("来源案例不存在")
        if identity.get("role") not in {"ADMIN", "EXPERT"} and case_permission(db, case_id, identity) != "OWNER":
            raise PermissionError("只有案例负责人可以提交定位结果")
        if analysis_id:
            analysis = db.get(AnalysisRun, analysis_id)
            if not analysis or analysis.case_id != case_id or analysis.status != "COMPLETED":
                raise ValueError("请选择该案例已完成的诊断")
            from app.services.report import get_report_context
            from app.services.category_report import report_markdown
            snapshot["report_markdown"] = report_markdown(get_report_context(case_id, analysis.id, db=db))
    snapshot.update(status="PENDING", reviewer=None)
    return snapshot


def review_submission(db, identity, record_id, version, approve):
    if identity.get("role") not in {"ADMIN", "EXPERT"}:
        raise PermissionError("仅管理员和专家可以确认共享案例和报告")
    row = db.get(WorkbenchRecord, record_id)
    if not row or row.kind != "library":
        raise LookupError("案例不存在")
    value = json_loads(row.payload_json, {})
    if row.version != version or value.get("status") != "PENDING":
        raise ValueError("提交内容已变更，请刷新")
    from sqlalchemy import select
    from app.knowledge_contribution_models import KnowledgeContribution
    contribution = db.scalar(select(KnowledgeContribution).where(KnowledgeContribution.source_library_id == row.id))
    if contribution:
        raise ValueError("此案例结论已进入知识审核队列，请核对当前贡献版本后审批")
    value.update(status="CONFIRMED" if approve else "REJECTED", reviewer=identity.get("id"),
                 reviewed_at=utcnow().isoformat())
    row.payload_json = json_dumps(value)
    # WorkbenchRecord's optimistic version lock makes concurrent reviews exclusive.
    db.flush()
    return row


def conclusion_contribution(db, identity, record_id):
    """Bridge a submitted case/report to the same editable, exact-content review queue."""
    from fastapi import HTTPException
    from sqlalchemy import select
    from app.knowledge_contribution_models import KnowledgeContribution
    from app.services.knowledge_access import is_knowledge_manager, require_contributor
    from app.services.knowledge_contributions import create_contribution, submit_contribution
    require_contributor(identity)
    record = db.get(WorkbenchRecord, record_id)
    if not record or record.kind != "library" or (record.owner_id != identity["id"] and not is_knowledge_manager(identity)):
        raise HTTPException(404, "Case submission not found")
    existing = db.scalar(select(KnowledgeContribution).where(KnowledgeContribution.source_library_id == record.id))
    if existing:
        return existing
    value = json_loads(record.payload_json, {})
    if value.get("status") != "PENDING" or not record.owner_id:
        raise HTTPException(409, "Only pending case conclusions enter the review queue")
    owner = {"id": record.owner_id, "role": "ENGINEER", "type": "user"}
    content = value.get("report_markdown") or value.get("content") or value.get("conclusion") or value.get("summary")
    if not content:
        content = "\n\n".join(f"## {key}\n{value[key]}" for key in ("description", "symptom", "root_cause", "solution") if value.get(key))
    if not content:
        raise HTTPException(422, "A case conclusion or report is required")
    row = create_contribution(db, owner, {"title": value.get("title") or "案例结论", "content": content,
        "operation": "CREATE", "content_kind": "KNOWLEDGE", "source_type": "fault_case",
        "metadata": {"library_record_id": record.id, "library_record_version": record.version,
            "case_id": value.get("case_id"), "analysis_id": value.get("analysis_id"),
            "problem_categories": [value.get("problem_category", "unknown")]}})
    row.source_library_id = record.id
    submit_contribution(db, row, owner, row.version)
    return row


def publish_reviewed_conclusion(db, contribution):
    """Publish a reviewed library view; never rewrite an AnalysisRun or original report."""
    row = db.get(WorkbenchRecord, contribution.source_library_id)
    if not row or row.kind != "library":
        raise ValueError("Source case submission no longer exists")
    value = json_loads(row.payload_json, {})
    candidate = json_loads(contribution.candidate_json, {})
    expected = candidate.get("metadata", {}).get("library_record_version")
    if row.version != expected or value.get("status") != "PENDING":
        raise ValueError("Case submission changed after review")
    value.update(status="CONFIRMED", reviewer=contribution.reviewed_by,
        reviewed_at=utcnow().isoformat(), reviewed_conclusion=candidate["content"],
        contribution_id=contribution.id, knowledge_document_id=contribution.published_document_id)
    row.payload_json = json_dumps(value)
    db.flush()
