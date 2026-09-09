"""Problem scopes, per-case model choices and versioned report templates."""
from contextvars import ContextVar
from contextlib import contextmanager, nullcontext
from functools import wraps
import hashlib
import inspect
from sqlalchemy import select
from app.core.db import SessionLocal
from app.core.utils import json_loads, json_dumps, new_id
from app.models import Case, KnowledgeDocument, ModelProfile
from app.workbench_models import WorkbenchRecord

DEFAULT_CATEGORIES = [
    {"id": "network", "name": "组网问题"}, {"id": "connection", "name": "连接问题"},
    {"id": "unknown", "name": "未知"}, {"id": "general", "name": "通用知识"},
]
KNOWLEDGE_ROLES = {"log_analysis": "日志分析", "diagnosis": "综合诊断", "fault_tree": "故障树",
                   "report_template": "报告格式", "prior_knowledge": "先验知识"}
SOURCE_TYPES = {"log_analysis": "analysis_skill", "diagnosis": "analysis_method", "fault_tree": "fault_tree",
                "report_template": "document", "prior_knowledge": "diagnostic_rule"}
case_model_id = ContextVar("case_model_id", default=None)
case_model_snapshot = ContextVar("case_model_snapshot", default=None)
case_category = ContextVar("case_category", default=None)
case_template = ContextVar("case_template", default=None)
case_knowledge = ContextVar("case_knowledge", default=None)
case_context_id = ContextVar("case_context_id", default=None)
case_graph_generation = ContextVar("case_graph_generation", default=None)
case_categories = ContextVar("case_categories", default=None)


class WorkbenchConfigurationError(ValueError):
    """A saved run cannot safely resolve its selected configuration."""


def categories(db):
    from app.services.problem_categories import category_rows
    return category_rows(db)


def validate_case_options(db, values, principal=None):
    category = values.get("problem_category")
    if "problem_category" in values and (not category or category == "general" or category not in {item["id"] for item in categories(db)}):
        raise ValueError("请选择有效的问题类别")
    profile_id = values.get("chat_profile_id")
    if profile_id:
        from app.services.model_access import require_model_profile, shared_model_clause
        profile = (require_model_profile(db, principal, profile_id, require_enabled=True) if principal else
                   db.scalar(select(ModelProfile).where(ModelProfile.id == profile_id, shared_model_clause())))
        if not profile or not profile.enabled or profile.task_type != "chat":
            raise ValueError("请选择自己可用且已启用的诊断模型")


def selected_profile():
    profile_id = case_model_id.get()
    if not profile_id:
        if case_context_id.get():
            raise WorkbenchConfigurationError("诊断快照缺少模型选择，请重新发起")
        return None
    with SessionLocal() as db:
        from app.services.model_access import resolve_chat_model_snapshot
        return resolve_chat_model_snapshot(db, case_model_snapshot.get() or {"selected_chat_profile_id": profile_id})


def case_model_job(function):
    """Job workers have isolated context; no global profile changes are made."""
    @wraps(function)
    def run(*args, **kwargs):
        bound = inspect.signature(function).bind(*args, **kwargs)
        case_id = bound.arguments.get("case_id")
        existing_db = bound.arguments.get("db")
        factory = bound.arguments.get("session_factory", SessionLocal)
        with nullcontext(existing_db) if existing_db is not None else factory() as db:
            from app.models import AnalysisRun, AgentRun
            from app.diagnostic_models import AnalysisRevision, LogTriageRun
            run_id = bound.arguments.get("analysis_run_id") or bound.arguments.get("source_analysis_id")
            agent_id = bound.arguments.get("agent_run_id")
            if bound.arguments.get("triage_run_id"):
                triage = db.get(LogTriageRun, bound.arguments["triage_run_id"])
                if triage:
                    case_id, agent_id = triage.case_id, triage.agent_run_id
            if bound.arguments.get("revision_id"):
                revision = db.get(AnalysisRevision, bound.arguments["revision_id"])
                if revision:
                    case_id, run_id = revision.case_id, revision.source_analysis_id
                    agent_id = revision.agent_run_id or agent_id
            # A revision is a new request by its own initiator. Its agent snapshot
            # must take precedence over the original analysis author's private API.
            source = db.get(AgentRun, agent_id) if agent_id else None
            source = source or (db.get(AnalysisRun, run_id) if run_id else None)
            case = db.get(Case, case_id) if case_id else bound.arguments.get("case")
            if not case:
                return function(*args, **kwargs)
            config = json_loads(source.model_config_json, {}) if source else {}
            try:
                new_request = bound.arguments.get("request") is not None or bool(bound.arguments.get("created_by"))
                if (new_request or not config.get("workbench_snapshot_id")) and not case_context_id.get():
                    identity = model_request_principal(db, bound.arguments, case)
                    config = capture_configuration(db, case, principal=identity)
                elif new_request or not config.get("workbench_snapshot_id"):
                    config = run_configuration()
                configuration = resolve_configuration(db, config)
                from app.services.model_access import resolve_chat_model_snapshot
                resolve_chat_model_snapshot(db, configuration)
            except ValueError as error:
                if existing_db is None and bound.arguments.get("ctx") is not None:
                    fail_configuration_job(db, bound.arguments, str(error))
                raise WorkbenchConfigurationError(str(error)) from error
            if existing_db is None:
                db.commit()
        with use_configuration(configuration):
            return function(*args, **kwargs)
    return run


def model_request_principal(db, arguments, case):
    """Prefer the request initiator, not another member who owns the case."""
    from app.services.model_access import principal_for_model_user
    request = arguments.get("request")
    identity = getattr(getattr(request, "state", None), "principal", None)
    if identity:
        return identity
    actor_id = arguments.get("created_by") or case.owner_id
    return principal_for_model_user(db, actor_id) if actor_id else None


def fail_configuration_job(db, arguments, message):
    """Finish only this queued request when its fixed context cannot be loaded."""
    from sqlalchemy import update
    from app.core.utils import utcnow
    from app.models import AnalysisRun, AgentRun, ConversationMessage, Job
    from app.diagnostic_models import AnalysisRevision, LogTriageRun
    from app.services.jobs import JobLeaseLostError
    ctx = arguments["ctx"]
    ctx.raise_if_cancelled()
    locked = db.execute(update(Job).where(Job.id == ctx.job_id, Job.status == "RUNNING",
        Job.lease_owner == ctx.lease_owner).values(status=Job.status))
    if locked.rowcount != 1:
        raise JobLeaseLostError("Configuration failure no longer belongs to this worker")
    targets = ((AnalysisRun, "analysis_run_id"), (AnalysisRevision, "revision_id"),
               (LogTriageRun, "triage_run_id"), (ConversationMessage, "message_id"), (AgentRun, "agent_run_id"))
    for model, name in targets:
        key = arguments.get(name)
        row = db.get(model, key) if key else None
        if row and row.status in {"QUEUED", "RUNNING"}:
            row.status = "FAILED"
            if hasattr(row, "error_message"):
                row.error_message = message
            if hasattr(row, "completed_at"):
                row.completed_at = utcnow()
            if isinstance(row, AgentRun):
                row.stop_reason = "CONFIGURATION_UNAVAILABLE"
    db.commit()


def capture_configuration(db, case, principal=None):
    from app.services.model_profiles import get_active_model_profile
    from app.services.model_access import (
        chat_model_snapshot, principal_for_model_user, resolve_chat_model_snapshot, resolve_user_chat_profile,
    )
    from app.services.workbench_snapshot import capture_knowledge
    if principal is None and case.owner_id:
        principal = principal_for_model_user(db, case.owner_id)
    if principal:
        # A saved case field predates unified personal settings. New authenticated
        # tasks follow their initiator's current preference, while queued tasks
        # retain the model already pinned in their run context.
        profile = resolve_user_chat_profile(db, principal)
        model_snapshot = chat_model_snapshot(db, principal, profile)
    else:
        # Unowned legacy/local cases can only use shared configuration.
        profile = (resolve_chat_model_snapshot(db, {"selected_chat_profile_id": case.chat_profile_id})
                   if case.chat_profile_id else get_active_model_profile("chat", db))
        if not profile:
            raise ValueError("尚未配置共享默认诊断模型")
        from app.services.model_access import model_profile_fingerprint
        model_snapshot = {"selected_chat_profile_id": profile.id,
                          "model_profile_fingerprint": model_profile_fingerprint(db, profile)}
    category = case.problem_category or "unknown"
    from app.models import KnowledgeGraphState
    graph = db.get(KnowledgeGraphState, "domain")
    value = {"problem_category": category, **model_snapshot,
             "problem_categories": categories(db),
             "knowledge_graph_generation_id": graph.active_generation_id if graph else None,
             "report_template": template_snapshot(db, category), "knowledge_snapshot": capture_knowledge(db)}
    text = json_dumps(value)
    key = "RC-" + hashlib.sha256(text.encode()).hexdigest()
    if db.get(WorkbenchRecord, key) is None:
        from sqlalchemy.exc import IntegrityError
        try:
            with db.begin_nested():
                db.add(WorkbenchRecord(id=key, kind="run_context", payload_json=text))
                db.flush()
        except IntegrityError:
            if db.get(WorkbenchRecord, key) is None:
                raise
    return {"workbench_snapshot_id": key}


def resolve_configuration(db, config):
    key = config.get("workbench_snapshot_id")
    if not key:
        return config
    row = db.get(WorkbenchRecord, key)
    if not row or row.kind != "run_context" or key != "RC-" + hashlib.sha256(row.payload_json.encode()).hexdigest():
        raise ValueError("诊断配置快照不可用或校验失败")
    return {**json_loads(row.payload_json, {}), "workbench_snapshot_id": key}


@contextmanager
def use_configuration(config):
    values = ((case_model_id, config.get("selected_chat_profile_id")),
              (case_model_snapshot, {key: config.get(key) for key in
                  ("selected_chat_profile_id", "model_actor_id", "model_profile_fingerprint")}),
              (case_category, config.get("problem_category")),
              (case_template, config.get("report_template")),
              (case_knowledge, config.get("knowledge_snapshot")),
              (case_graph_generation, config.get("knowledge_graph_generation_id")),
              (case_categories, config.get("problem_categories")),
              (case_context_id, config.get("workbench_snapshot_id")))
    tokens = [(variable, variable.set(value)) for variable, value in values]
    try:
        yield
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)


def run_configuration():
    template = case_template.get() or {}
    return {"workbench_snapshot_id": case_context_id.get(), "problem_category": case_category.get(),
            **(case_model_snapshot.get() or {}),
            "selected_chat_profile_id": case_model_id.get(),
            "report_template": {key: template.get(key) for key in ("id", "version", "category", "sha256")}}


def category_instructions(options=None):
    options = options or case_categories.get() or DEFAULT_CATEGORIES
    return {"available_problem_categories": [item for item in options if item["id"] != "general"],
            "category_instruction": "network表示GW/AP组网协同，connection表示终端接入。未知时可建议已有类别ID，"
                "同时给出category_reason并以[[evidence_id]]引用本案例证据。无依据时两个建议字段均为null。"
                "建议不自动修改案例；跨类参考必须说明原因。"}


def validate_category_suggestion(result, options=None):
    category = result.get("suggested_problem_category")
    options = options or case_categories.get() or DEFAULT_CATEGORIES
    if category and category not in {item["id"] for item in options if item["id"] not in {"unknown", "general"}}:
        raise ValueError("建议类别必须来自已有问题类别；无依据时不提供建议")


def knowledge_scope(document):
    meta = json_loads(document.metadata_json, {})
    return meta.get("problem_categories") or ["general"]


def matches_category(document, category):
    return category in {None, "unknown"} or bool({category, "general"} & set(knowledge_scope(document)))


def scope_methods(rows, case):
    category = case_category.get() or getattr(case, "problem_category", "unknown")
    selected = [row for row in rows if matches_category(row, category)]
    return selected


def template_snapshot(db, category):
    from app.services.knowledge_access import knowledge_kind
    documents = list(db.scalars(select(KnowledgeDocument).where(
        KnowledgeDocument.active.is_(True), KnowledgeDocument.review_status == "ACTIVE",
        KnowledgeDocument.confidentiality.in_(["PUBLIC", "INTERNAL"]))))
    for desired in (category, "network"):
        matching = [doc for doc in documents if knowledge_kind(doc) == "SKILL"
                    and json_loads(doc.metadata_json, {}).get("knowledge_role") == "report_template"
                    and desired in knowledge_scope(doc)]
        matching.sort(key=lambda doc: (doc.published_at.isoformat() if doc.published_at else "", doc.id), reverse=True)
        preference = db.get(WorkbenchRecord, "template-" + str(desired))
        preferred_id = json_loads(preference.payload_json, {}).get("document_id") if preference else None
        matching.sort(key=lambda doc: doc.id != preferred_id)
        if matching:
            doc = matching[0]
            return {"id": doc.id, "version": doc.version, "category": desired, "content": doc.content,
                    "sha256": hashlib.sha256(doc.content.encode()).hexdigest()}
    from app.services.report_contract import builtin_report_template
    return builtin_report_template()


def record_payload(record):
    return {"id": record.id, "kind": record.kind, "owner_id": record.owner_id, "version": record.version,
            "created_at": record.created_at.isoformat(), **json_loads(record.payload_json, {})}


def make_record(db, kind, owner, payload):
    row = WorkbenchRecord(id=new_id("WB"), kind=kind, owner_id=owner, payload_json=json_dumps(payload))
    db.add(row)
    db.flush()
    return row
