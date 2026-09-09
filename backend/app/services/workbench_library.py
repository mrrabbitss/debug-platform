"""Human submissions and administrator confirmation for shared case knowledge."""
from app.core.utils import json_dumps, json_loads, utcnow
from app.models import AnalysisRun, Case
from app.services.access_control import case_permission
from app.services.workbench import validate_case_options
from app.workbench_models import WorkbenchRecord


def prepare_submission(db, identity, payload):
    if identity.get("role") not in {"ADMIN", "ENGINEER"}:
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
        if identity.get("role") != "ADMIN" and case_permission(db, case_id, identity) != "OWNER":
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
    if identity.get("role") != "ADMIN":
        raise PermissionError("仅管理员可以确认共享案例和报告")
    row = db.get(WorkbenchRecord, record_id)
    if not row or row.kind != "library":
        raise LookupError("案例不存在")
    value = json_loads(row.payload_json, {})
    if row.version != version or value.get("status") != "PENDING":
        raise ValueError("提交内容已变更，请刷新")
    value.update(status="CONFIRMED" if approve else "REJECTED", reviewer=identity.get("id"),
                 reviewed_at=utcnow().isoformat())
    row.payload_json = json_dumps(value)
    # WorkbenchRecord's optimistic version lock makes concurrent reviews exclusive.
    db.flush()
    return row
