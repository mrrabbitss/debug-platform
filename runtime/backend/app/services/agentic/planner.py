from __future__ import annotations

from typing import Any

from app.services.rag import tokenize


SEARCH_MODULES = {"knowledge", "domain_graph", "code", "commit", "memory"}
CODE_INTENT_TERMS = {
    "代码", "函数", "方法", "调用", "引用", "继承", "实现", "接口", "类", "宏",
    "文件", "源码", "堆栈", "崩溃", "定位", "symbol", "function", "call", "reference",
    "inherit", "implement", "class", "interface", "source", "stack", "crash",
}
COMMIT_INTENT_TERMS = {
    "commit", "提交", "修改", "变更", "引入", "回归", "版本", "历史", "修复记录",
    "何时", "谁改", "regression", "change", "introduced", "history", "blame", "fix",
}
MEMORY_INTENT_TERMS = {
    "以前", "之前", "类似", "经验", "历史案例", "失败", "复用", "曾经",
    "previous", "similar", "memory", "experience", "failed",
}


def _query_signals(query: str) -> set[str]:
    lower = query.lower()
    signals = set(tokenize(query))
    for term in CODE_INTENT_TERMS | COMMIT_INTENT_TERMS | MEMORY_INTENT_TERMS:
        if term in lower:
            signals.add(term)
    return signals


def build_search_plan(
    query: str,
    *,
    repository_count: int,
    requested_modules: list[str] | None,
    max_hops: int,
    domain_graph_available: bool = False,
) -> dict[str, Any]:
    signals = _query_signals(query)
    if requested_modules is not None:
        modules = [module for module in requested_modules if module in SEARCH_MODULES]
        if modules:
            rationale = ["使用调用方明确指定的检索模块"]
        else:
            modules = ["knowledge", "memory"]
            rationale = ["未选择有效模块，回退到知识库和记忆检索"]
    else:
        modules = ["knowledge", "memory"]
        rationale = ["知识与经验是诊断检索的默认第一跳"]
        if domain_graph_available:
            modules.insert(1, "domain_graph")
            rationale.append(
                "Active domain knowledge graph detected; enable GraphRAG expansion"
            )
        if repository_count and signals.intersection(CODE_INTENT_TERMS):
            modules.append("code")
            rationale.append("检测到代码定位/调用关系意图，启用代码图谱")
        if repository_count and signals.intersection(COMMIT_INTENT_TERMS):
            if "code" not in modules:
                modules.append("code")
            modules.append("commit")
            rationale.append("检测到变更、回归或历史意图，启用 Commit → 文件 → 代码路径")
        if signals.intersection(MEMORY_INTENT_TERMS):
            rationale.append("检测到历史经验意图，提高记忆模块优先级")
    modules = list(dict.fromkeys(modules))
    return {
        "selected_modules": modules,
        "intent_signals": sorted(signals.intersection(
            CODE_INTENT_TERMS | COMMIT_INTENT_TERMS | MEMORY_INTENT_TERMS
        )),
        "max_hops": max_hops,
        "algorithms": [
            "BM25",
            "dense_embedding",
            "reciprocal_rank_fusion",
            "reranker",
            "graph_multi_hop",
            "graphrag",
        ],
        "rationale": rationale,
    }
