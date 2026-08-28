from __future__ import annotations

from typing import Any

from pydantic import ValidationError


_VALUE_ERROR_CODES = {
    "Model did not attest reading every applicable method document": (
        "METHOD_READ_ATTESTATION_MISSING",
        "模型返回的已读文档清单不完整。",
        "read_document_ids",
    ),
    "Model did not assess every applicable method document": (
        "METHOD_ASSESSMENT_INCOMPLETE",
        "模型没有逐份评估全部适用方法文档。",
        "method_assessments",
    ),
    "Model planned checks or searches for unknown method documents": (
        "UNKNOWN_METHOD_DOCUMENT_ID",
        "模型引用了当前方法目录之外的文档 ID。",
        "checks/search_queries",
    ),
    "Model rejected a fault tree that overlaps the case symptom": (
        "SYMPTOM_FAULT_TREE_REJECTED",
        "模型错误排除了与案例现象直接重合的故障树。",
        "method_assessments.relevance",
    ),
    "Relevant methods do not have an executable check": (
        "RELEVANT_METHOD_CHECK_MISSING",
        "相关方法没有对应的可执行检查。",
        "checks",
    ),
    "Every relevant method must drive an initial planned search query": (
        "INITIAL_METHOD_SEARCH_MISSING",
        "首轮没有为每份相关方法安排检索。",
        "search_queries",
    ),
    "Model selected unknown diagnostic pattern IDs": (
        "UNKNOWN_PATTERN_ID",
        "模型选择了当前规则目录之外的 Pattern ID。",
        "selected_pattern_ids",
    ),
    "Model returned unknown evidence IDs": (
        "UNKNOWN_EVIDENCE_ID",
        "Model returned unknown evidence IDs; 已保留确定性诊断结果。",
        "evidence_ids",
    ),
    "Fault-tree coverage contains duplicate item assessments": (
        "FAULT_TREE_DUPLICATE_ASSESSMENT",
        "模型重复提交了同一故障树节点的结论。",
        "fault_tree_assessments.item_id",
    ),
    "Model referenced unknown fault-tree coverage items": (
        "UNKNOWN_FAULT_TREE_ITEM_ID",
        "模型引用了当前故障树目录之外的节点 ID。",
        "fault_tree_item_ids",
    ),
    "Fault-tree item assessment is bound to the wrong method document": (
        "FAULT_TREE_METHOD_BINDING_INVALID",
        "故障树节点与方法文档 ID 的绑定不正确。",
        "fault_tree_assessments.method_document_id",
    ),
    "Supported or excluded fault-tree conclusions require evidence IDs": (
        "FAULT_TREE_EVIDENCE_REQUIRED",
        "证据支持或已排除的节点结论没有引用 evidence ID。",
        "fault_tree_assessments.evidence_ids",
    ),
    "Fault-tree conclusion cited unknown evidence IDs": (
        "FAULT_TREE_UNKNOWN_EVIDENCE_ID",
        "故障树节点结论引用了当前证据目录之外的 ID。",
        "fault_tree_assessments.evidence_ids",
    ),
    "Fault-tree log searches require at least one keyword or compiled pattern ID": (
        "FAULT_TREE_LOG_QUERY_EMPTY",
        "故障树日志检索没有提供关键词或已编译 Pattern ID。",
        "tool_calls.arguments",
    ),
    "Planning round did not bind an unattempted fault-tree item": (
        "FAULT_TREE_NEW_ITEM_NOT_TARGETED",
        "本轮没有把未尝试节点同时绑定到检查和只读证据工具。",
        "checks/tool_calls.fault_tree_item_ids",
    ),
    "Every newly targeted fault-tree item must also have an explicit executable check": (
        "FAULT_TREE_CHECK_BINDING_INCOMPLETE",
        "本轮新检索的故障树节点缺少对应的显式检查。",
        "checks.fault_tree_item_ids",
    ),
    "Unattempted fault-tree items cannot receive terminal conclusions": (
        "FAULT_TREE_PREMATURE_CONCLUSION",
        "尚未执行证据工具的故障树节点不能提前进入终态。",
        "fault_tree_assessments.status",
    ),
    "Tool requested unknown diagnostic method documents": (
        "TOOL_UNKNOWN_METHOD_DOCUMENT_ID",
        "模型工具调用请求了方法目录之外的文档 ID。",
        "tool_calls.arguments.document_ids",
    ),
    "Tool search referenced unknown method documents": (
        "TOOL_SEARCH_UNKNOWN_METHOD_DOCUMENT_ID",
        "模型检索工具引用了方法目录之外的文档 ID。",
        "tool_calls.arguments.method_document_ids",
    ),
    "Tool log search referenced unknown diagnostic patterns": (
        "TOOL_UNKNOWN_PATTERN_ID",
        "模型日志工具引用了规则目录之外的 Pattern ID。",
        "tool_calls.arguments.pattern_ids",
    ),
}

_LLM_ERROR_MESSAGES = {
    "MODEL_CONFIGURATION_MISSING": "模型配置缺少 API Key、Base URL 或模型名称。",
    "MODEL_ENDPOINT_CONFIGURATION_INVALID": "模型端点或代理配置未通过校验。",
    "MODEL_PROXY_CONNECTION_FAILED": "模型代理拒绝连接。",
    "MODEL_TIMEOUT": "模型请求超时；请检查代理超时和模型 Profile 的超时秒数。",
    "MODEL_TLS_VERIFICATION_FAILED": "模型请求未通过 TLS 证书链或主机名校验。",
    "MODEL_AUTHENTICATION_FAILED": "模型 API 鉴权失败，请检查 API Key。",
    "MODEL_PERMISSION_DENIED": "模型服务拒绝访问，请检查账号或模型权限。",
    "MODEL_RATE_LIMITED": "模型服务触发限流或配额限制。",
    "MODEL_BAD_REQUEST": "模型网关拒绝了请求参数。",
    "MODEL_NOT_FOUND": "模型名称或模型端点不存在。",
    "MODEL_UPSTREAM_ERROR": "模型网关返回服务端 HTTP 错误。",
    "MODEL_CONNECTION_FAILED": "无法连接模型服务或配置的代理。",
    "MODEL_OUTPUT_TRUNCATED": "模型输出达到最大 Tokens，JSON 未完整返回。",
    "MODEL_INVALID_JSON": "模型返回内容不是可解析的 JSON 对象。",
    "MODEL_REQUEST_FAILED": "模型请求失败。",
}


def planning_failure_details(exc: Exception, provider: Any | None = None) -> dict[str, Any]:
    """Return content-safe diagnostics suitable for persistence and UI display."""
    error_type = type(exc).__name__
    code = "PLANNER_VALIDATION_ERROR"
    message = "模型规划未通过结构或证据约束校验。"
    field_path = ""
    if isinstance(exc, ValidationError):
        errors = exc.errors(include_url=False, include_context=False, include_input=False)
        paths = [".".join(str(part) for part in item.get("loc", ())) for item in errors]
        field_path = ", ".join(path for path in paths if path)[:500]
        code = "PLANNER_SCHEMA_VALIDATION_ERROR"
        if errors:
            message = f"模型规划存在 {len(errors)} 个结构字段错误。"
    else:
        rendered = str(exc)
        mapped = next(
            (value for marker, value in _VALUE_ERROR_CODES.items() if marker in rendered),
            None,
        )
        if mapped:
            code, message, field_path = mapped
        elif error_type == "LLMError":
            code = str(getattr(exc, "code", None) or "MODEL_REQUEST_FAILED")
            message = _LLM_ERROR_MESSAGES.get(code, "模型请求失败。")
        elif rendered:
            message = rendered[:500]
    return {
        "code": code,
        "message": message,
        "field_path": field_path or None,
        "error_type": error_type,
        "upstream_error_type": getattr(exc, "upstream_error_type", None),
        "finish_reason": getattr(provider, "last_finish_reason", None),
        "thinking_mode": getattr(provider, "last_thinking_mode", None),
        "retry_count": int(getattr(provider, "last_validation_retry_count", 0) or 0),
    }
