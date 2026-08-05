"""Executable golden-dataset gates for the platform's core cognitive abilities."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from pydantic import BaseModel
import shutil
import subprocess
import tempfile
from time import perf_counter
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import aliased, sessionmaker

from app.core.db import Base, configure_sqlite_engine
from app.models import (
    AgentMemory,
    Artifact,
    Case,
    CodeRelation,
    CodeSymbol,
    KnowledgeChunk,
    KnowledgeDocument,
    Repository,
)
from app.services import code_index, commit_graph
from app.services.agentic_search import build_search_plan
from app.services.agentic.executor import (
    AgentBudget,
    BoundedAgentExecutor,
    PlannerDecision,
    sequence_planner,
)
from app.services.agentic.tools import ToolContext, ToolRegistry, ToolSpec
from app.services.curation_documents import prepare_curation_document
from app.services.knowledge_curation import (
    GeneratedCaseDraft,
    validate_curation_markdown,
)
from app.services.knowledge_methods import parse_markdown_sections
from app.services.log_parsers import registry
from app.services.memory import search_memories
from app.services.rag import retriever
from app.services.retrieval_evaluation import calculate_ranking_metrics
from app.services.text_files import open_text_lines


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CORPUS_ROOT = REPO_ROOT / "sample_data" / "golden_incident"


@dataclass(frozen=True)
class EvaluationOutcome:
    metrics: dict[str, Any]
    failures: list[str]


class _JobContext:
    def __init__(self) -> None:
        self.updates: list[tuple[int, str]] = []

    def update(self, progress: int, message: str) -> None:
        self.updates.append((progress, message))


class _ModuleInput(BaseModel):
    module: str


class _ModuleOutput(BaseModel):
    module: str
    evidence_count: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run_check(
    name: str,
    evaluator: Callable[[], EvaluationOutcome],
    *,
    max_duration_ms: int | None = None,
) -> dict[str, Any]:
    started = perf_counter()
    try:
        outcome = evaluator()
        failures = list(outcome.failures)
        metrics = outcome.metrics
    except Exception as exc:  # noqa: BLE001 - evaluator must report, not abort the suite
        failures = [f"{type(exc).__name__}: {exc}"]
        metrics = {}
    duration_ms = round((perf_counter() - started) * 1000, 3)
    if max_duration_ms is not None and duration_ms > max_duration_ms:
        failures.append(
            f"duration {duration_ms} ms exceeded budget {max_duration_ms} ms"
        )
    return {
        "name": name,
        "status": "PASS" if not failures else "FAIL",
        "duration_ms": duration_ms,
        "budget_ms": max_duration_ms,
        "metrics": metrics,
        "failures": failures,
    }


def _evaluate_fixture_integrity(root: Path, corpus: dict[str, Any]) -> EvaluationOutcome:
    failures: list[str] = []
    checked: dict[str, str] = {}
    required = ["corpus.json", "fake_model_responses.json", *corpus["documents"]]
    for relative in required:
        path = root / relative
        if not path.is_file():
            failures.append(f"missing fixture: {relative}")
            continue
        checked[relative] = _sha256(path)
    for relative, expected_hash in corpus.get("fixture_sha256", {}).items():
        actual = checked.get(relative)
        if actual != expected_hash:
            failures.append(
                f"fixture hash mismatch for {relative}: expected {expected_hash}, got {actual}"
            )
    if not corpus.get("privacy", {}).get("synthetic_only"):
        failures.append("corpus must declare synthetic_only=true")
    if corpus.get("privacy", {}).get("contains_company_data"):
        failures.append("golden corpus must not contain company data")
    return EvaluationOutcome(
        metrics={"files_checked": len(checked), "sha256": checked},
        failures=failures,
    )


def _event_projection(event: Any) -> dict[str, Any]:
    return {
        "event_code": event.event_code,
        "line_start": event.line_start,
        "line_end": event.line_end,
        "timestamp_raw": event.timestamp_raw,
    }


def _evaluate_parser(root: Path, corpus: dict[str, Any]) -> EvaluationOutcome:
    specification = corpus["parser"]
    path = root / specification["path"]
    text = path.read_text(encoding="utf-8")
    parser = registry.select(path, text[:10_000])
    events = parser.parse(path, specification["path"], text)
    actual = [_event_projection(event) for event in events]
    expected = specification["expected_events"]
    matches = sum(1 for item in expected if item in actual)
    failures: list[str] = []
    if parser.parser_id != specification["parser_id"]:
        failures.append(
            f"expected parser {specification['parser_id']}, got {parser.parser_id}"
        )
    if actual != expected:
        failures.append(f"event projection mismatch: expected {expected}, got {actual}")
    return EvaluationOutcome(
        metrics={
            "parser_id": parser.parser_id,
            "expected_events": len(expected),
            "actual_events": len(actual),
            "event_accuracy": round(matches / max(len(expected), 1), 6),
            "events": actual,
        },
        failures=failures,
    )


def _plain_text_line_count(path: Path) -> int:
    opened = open_text_lines(path)
    if opened is None:
        return 0
    _, lines = opened
    return sum(1 for _ in lines)


def _evaluate_curation(root: Path, corpus: dict[str, Any]) -> EvaluationOutcome:
    specification = corpus["curation"]
    responses = json.loads((root / "fake_model_responses.json").read_text(encoding="utf-8"))
    generated = GeneratedCaseDraft.model_validate(responses["initial"])
    line_counts: dict[str, int] = {}
    extraction_methods: dict[str, str] = {}
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="gwap-golden-docs-") as temporary:
        extraction_root = Path(temporary)
        for index, relative in enumerate(corpus["documents"], start=1):
            source = root / relative
            source_ref = f"SRC-{index:04d}"
            if source.suffix.casefold() in {".docx", ".pdf", ".html", ".htm"}:
                prepared = prepare_curation_document(
                    source,
                    relative,
                    extraction_root / f"{source_ref}.txt",
                )
                if prepared is None:
                    failures.append(f"document extractor did not accept {relative}")
                    continue
                line_counts[source_ref] = prepared.line_count
                extraction_methods[source_ref] = prepared.method
            else:
                line_counts[source_ref] = _plain_text_line_count(source)
                extraction_methods[source_ref] = "plain_text"

    markdown = generated.markdown
    validation = validate_curation_markdown(markdown, line_counts)
    structure = parse_markdown_sections(markdown)
    required_keys = {
        "error_form",
        "log_analysis",
        "localization",
        "solution",
        "validation",
        "scope",
    }
    available_keys = set(structure["sections"])
    missing_keys = sorted(required_keys - available_keys)
    if missing_keys:
        failures.append(f"missing semantic sections: {', '.join(missing_keys)}")
    lowered = markdown.casefold()
    missing_facts = [
        fact for fact in specification["required_facts"] if fact.casefold() not in lowered
    ]
    if missing_facts:
        failures.append(f"missing required facts: {missing_facts}")
    hallucinations = [
        claim
        for claim in specification["forbidden_claims"]
        if claim.casefold() in lowered
    ]
    if hallucinations:
        failures.append(f"forbidden unsupported claims: {hallucinations}")
    if not validation["confirmable"]:
        failures.append(f"draft is not confirmable: {validation['warnings']}")
    if validation["line_citation_count"] < specification["min_line_citations"]:
        failures.append(
            "line citation count below threshold: "
            f"{validation['line_citation_count']} < {specification['min_line_citations']}"
        )
    citation_accuracy = 1.0 if not validation["invalid_source_refs"] and not validation["invalid_line_citations"] else 0.0
    return EvaluationOutcome(
        metrics={
            "section_completeness": structure["completeness"],
            "semantic_section_coverage": round(
                len(required_keys.intersection(available_keys)) / len(required_keys), 6
            ),
            "line_citations": validation["line_citation_count"],
            "citation_accuracy": citation_accuracy,
            "hallucination_count": len(hallucinations),
            "extraction_methods": extraction_methods,
            "source_line_counts": line_counts,
        },
        failures=failures,
    )


def _git(root: Path, *arguments: str, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
    return completed.stdout.strip()


@contextmanager
def _patched_index_runtime(factory: Any, repository_root: Path):
    original_code_session = code_index.SessionLocal
    original_commit_session = commit_graph.SessionLocal
    original_resolver = code_index.storage.resolve_path
    code_index.SessionLocal = factory
    commit_graph.SessionLocal = factory
    code_index.storage.resolve_path = lambda _value: repository_root
    try:
        yield
    finally:
        code_index.SessionLocal = original_code_session
        commit_graph.SessionLocal = original_commit_session
        code_index.storage.resolve_path = original_resolver


def _graph_fixture(root: Path, temporary: Path) -> tuple[Any, Any, Path, dict[str, Any]]:
    repository_root = temporary / "repository"
    shutil.copytree(root / "repository", repository_root)
    _git(repository_root, "init", "--initial-branch=main")
    _git(repository_root, "config", "user.name", "Golden Harness")
    _git(repository_root, "config", "user.email", "golden@example.invalid")
    _git(repository_root, "add", ".")
    environment = {
        **__import__("os").environ,
        "GIT_AUTHOR_DATE": "2026-03-02T03:20:00+00:00",
        "GIT_COMMITTER_DATE": "2026-03-02T03:20:00+00:00",
    }
    _git(repository_root, "commit", "-m", "add synthetic authentication baseline", env=environment)
    auth_path = repository_root / "auth.py"
    auth_path.write_text(
        auth_path.read_text(encoding="utf-8")
        + "\n# Regression fix: preserve AUTH_TIMEOUT evidence when shared keys differ.\n",
        encoding="utf-8",
    )
    _git(repository_root, "add", "auth.py")
    environment["GIT_AUTHOR_DATE"] = "2026-03-02T03:25:00+00:00"
    environment["GIT_COMMITTER_DATE"] = "2026-03-02T03:25:00+00:00"
    _git(
        repository_root,
        "commit",
        "-m",
        "fix authentication timeout shared key regression",
        env=environment,
    )

    engine = create_engine(
        f"sqlite:///{temporary / 'graph.db'}",
        connect_args={"check_same_thread": False},
    )
    configure_sqlite_engine(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    with factory() as db:
        db.add(Case(id="CASE-golden-graph", title="Golden graph case", device_type="AP"))
        db.add(Artifact(
            id="ART-golden-graph",
            case_id="CASE-golden-graph",
            kind="source_repository",
            original_name="golden.bundle",
            stored_path="synthetic/repository",
            sha256="a" * 64,
            size_bytes=1,
            status="EXTRACTED",
        ))
        db.flush()
        db.add(Repository(
            id="REPO-golden-graph",
            case_id="CASE-golden-graph",
            artifact_id="ART-golden-graph",
            name="golden-repository",
            root_path="synthetic/repository",
            status="UPLOADED",
        ))
        db.commit()
    with _patched_index_runtime(factory, repository_root):
        index_result = code_index._index_repository_impl(
            _JobContext(),
            "REPO-golden-graph",
            generation_id="CGEN-golden",
        )
    return engine, factory, repository_root, index_result


def _relation_projection(db: Any) -> set[tuple[str, str, str]]:
    source_symbol = aliased(CodeSymbol)
    target_symbol = aliased(CodeSymbol)
    rows = db.execute(
        select(
            source_symbol.name,
            target_symbol.name,
            CodeRelation.target_name,
            CodeRelation.relation_type,
        )
        .join(source_symbol, CodeRelation.source_symbol_id == source_symbol.id)
        .outerjoin(target_symbol, CodeRelation.target_symbol_id == target_symbol.id)
        .where(CodeRelation.repository_id == "REPO-golden-graph")
    ).all()
    return {
        (source_name, target_name or target_hint, relation_type)
        for source_name, target_name, target_hint, relation_type in rows
    }


def _evaluate_graphs(root: Path, corpus: dict[str, Any]) -> tuple[EvaluationOutcome, EvaluationOutcome]:
    with tempfile.TemporaryDirectory(prefix="gwap-golden-graph-") as temporary_value:
        temporary = Path(temporary_value)
        engine, factory, _repository_root, index_result = _graph_fixture(root, temporary)
        try:
            with factory() as db:
                actual_edges = _relation_projection(db)
                expected_edges = {
                    tuple(item) for item in corpus["code_graph"]["expected_edges"]
                }
                missing_edges = sorted(expected_edges - actual_edges)
                code_failures = (
                    [f"missing expected code edges: {missing_edges}"] if missing_edges else []
                )
                code_outcome = EvaluationOutcome(
                    metrics={
                        "symbols": index_result["symbols"],
                        "relations": index_result["relations"],
                        "expected_edges": len(expected_edges),
                        "matched_edges": len(expected_edges.intersection(actual_edges)),
                        "edge_recall": round(
                            len(expected_edges.intersection(actual_edges))
                            / max(len(expected_edges), 1),
                            6,
                        ),
                    },
                    failures=code_failures,
                )

                commit_specification = corpus["commit_graph"]
                commits = commit_graph.search_commits(
                    db,
                    "REPO-golden-graph",
                    commit_specification["query"],
                    limit=5,
                )
                path_found = False
                actual_path: list[str] = []
                for commit in commits:
                    file_paths = {item["file_path"] for item in commit["files"]}
                    symbols = commit_graph.symbols_for_commit_paths(
                        db,
                        "REPO-golden-graph",
                        file_paths,
                    )
                    if "auth.py" in file_paths and any(
                        symbol.name == "verify_shared_key" for symbol in symbols
                    ):
                        path_found = True
                        actual_path = [
                            "query",
                            "commit",
                            "auth.py",
                            "verify_shared_key",
                        ]
                        break
                expected_path = commit_specification["expected_path"]
                commit_failures = [] if path_found and actual_path == expected_path else [
                    f"expected commit path {expected_path}, got {actual_path or 'no path'}"
                ]
                commit_outcome = EvaluationOutcome(
                    metrics={
                        "matched_commits": len(commits),
                        "path_found": path_found,
                        "path": actual_path,
                        "indexed_commits": index_result["commit_graph"].get("commits", 0),
                    },
                    failures=commit_failures,
                )
        finally:
            engine.dispose()
    return code_outcome, commit_outcome


def _memory_row(
    memory_id: str,
    case_id: str,
    memory_type: str,
    title: str,
    content: str,
    *,
    confidence: float,
) -> AgentMemory:
    return AgentMemory(
        id=memory_id,
        case_id=case_id,
        memory_type=memory_type,
        source_kind="golden",
        source_id=memory_id,
        title=title,
        content=content,
        context_json="{}",
        evidence_json="[]",
        outcome="SUCCESS" if memory_type != "FAILURE" else "FAILED",
        confidence=confidence,
        fingerprint=hashlib.sha256(memory_id.encode("utf-8")).hexdigest(),
    )


def _evaluate_memory(corpus: dict[str, Any]) -> EvaluationOutcome:
    with tempfile.TemporaryDirectory(prefix="gwap-golden-memory-") as temporary:
        engine = create_engine(f"sqlite:///{Path(temporary) / 'memory.db'}")
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        Base.metadata.create_all(bind=engine)
        with factory() as db:
            db.add_all([
                Case(id="CASE-memory-current", title="Current golden case", device_type="AP"),
                Case(id="CASE-memory-other", title="Other isolated case", device_type="GW"),
            ])
            db.flush()
            db.add_all([
                _memory_row(
                    "MEM-procedural-auth",
                    "CASE-memory-current",
                    "PROCEDURAL",
                    "AUTH_TIMEOUT shared key procedure",
                    "Reuse the evidence-first shared key verification procedure.",
                    confidence=0.95,
                ),
                _memory_row(
                    "MEM-failure-other-case",
                    "CASE-memory-other",
                    "FAILURE",
                    "AUTH_TIMEOUT unsupported hardware guess",
                    "Failed because hardware replacement lacked evidence.",
                    confidence=0.9,
                ),
                _memory_row(
                    "MEM-secret-cross-case",
                    "CASE-memory-other",
                    "EPISODIC",
                    "AUTH_TIMEOUT private case detail",
                    "Must remain isolated to the other case.",
                    confidence=1.0,
                ),
            ])
            db.commit()
            specification = corpus["memory"]
            results = search_memories(
                db,
                specification["query"],
                case_id="CASE-memory-current",
                limit=20,
            )
            actual_ids = [memory.id for memory, _score in results]
        engine.dispose()
    missing = sorted(set(specification["must_reuse"]) - set(actual_ids))
    polluted = sorted(set(specification["must_not_reuse"]).intersection(actual_ids))
    failures: list[str] = []
    if missing:
        failures.append(f"expected reusable memories missing: {missing}")
    if polluted:
        failures.append(f"cross-case memories polluted results: {polluted}")
    return EvaluationOutcome(
        metrics={
            "returned_ids": actual_ids,
            "reuse_recall": 1.0 if not missing else 0.0,
            "pollution_count": len(polluted),
        },
        failures=failures,
    )


def _evaluate_rag(corpus: dict[str, Any]) -> EvaluationOutcome:
    specification = corpus["rag"]
    with tempfile.TemporaryDirectory(prefix="gwap-golden-rag-") as temporary:
        engine = create_engine(f"sqlite:///{Path(temporary) / 'rag.db'}")
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        Base.metadata.create_all(bind=engine)
        with factory() as db:
            documents = [
                KnowledgeDocument(
                    id="GOLD-DOC-AUTH",
                    title="AUTH_TIMEOUT shared-key recovery",
                    source_type="fault_case",
                    device_type="AP",
                    module="WLAN",
                    trust_level="HIGH",
                    content="AUTH_TIMEOUT shared-key mismatch recovery procedure",
                    active=True,
                    review_status="ACTIVE",
                ),
                KnowledgeDocument(
                    id="GOLD-DOC-DHCP",
                    title="DHCP lease renewal",
                    source_type="fault_case",
                    device_type="AP",
                    module="LAN",
                    content="DHCP discover timeout and lease renewal",
                    active=True,
                    review_status="ACTIVE",
                ),
                KnowledgeDocument(
                    id="GOLD-DOC-PON",
                    title="PON optical alarm",
                    source_type="fault_case",
                    device_type="GW",
                    module="PON",
                    content="Optical LOS diagnostics",
                    active=True,
                    review_status="ACTIVE",
                ),
            ]
            db.add_all(documents)
            db.flush()
            db.add_all([
                KnowledgeChunk(
                    id="GOLD-KB-AUTH",
                    document_id="GOLD-DOC-AUTH",
                    chunk_index=0,
                    heading="Recovery",
                    content="Verify and correct the shared-key mismatch causing AUTH_TIMEOUT.",
                ),
                KnowledgeChunk(
                    id="GOLD-KB-DHCP",
                    document_id="GOLD-DOC-DHCP",
                    chunk_index=0,
                    heading="DHCP",
                    content="Renew the client lease after DHCP timeout.",
                ),
                KnowledgeChunk(
                    id="GOLD-KB-PON",
                    document_id="GOLD-DOC-PON",
                    chunk_index=0,
                    heading="PON",
                    content="Inspect optical signal levels.",
                ),
            ])
            db.commit()
            hits = retriever.search(
                specification["query"],
                db=db,
                device_type="AP",
                top_k=specification["top_k"],
                include_code_symbols=False,
                apply_models=False,
            )
            actual_ids = [hit.evidence_id for hit in hits]
        engine.dispose()
    metrics = calculate_ranking_metrics(
        specification["expected_evidence_ids"],
        actual_ids,
        top_k=specification["top_k"],
    )
    cited_ids = actual_ids[:1]
    expected = set(specification["expected_evidence_ids"])
    citation_accuracy = (
        len(expected.intersection(cited_ids)) / max(len(cited_ids), 1)
        if cited_ids
        else 0.0
    )
    metrics["citation_accuracy"] = round(citation_accuracy, 6)
    metrics["ranked_evidence_ids"] = actual_ids
    failures = [
        f"{key}={metrics.get(key)} below minimum {minimum}"
        for key, minimum in specification["minimums"].items()
        if float(metrics.get(key) or 0.0) < float(minimum)
    ]
    return EvaluationOutcome(metrics=metrics, failures=failures)


def _evaluate_agentic_plan(corpus: dict[str, Any]) -> EvaluationOutcome:
    specification = corpus["agentic_search"]
    plan = build_search_plan(
        specification["query"],
        repository_count=1,
        requested_modules=None,
        max_hops=specification["max_hops"],
        domain_graph_available=False,
    )
    selected = plan["selected_modules"]
    expected = specification["expected_modules"]
    steps = len(selected) + 3
    stop_reason = "COMPLETED" if selected == expected and steps <= specification["max_steps"] else "QUALITY_GATE_FAILED"
    failures: list[str] = []
    if selected != expected:
        failures.append(f"expected modules {expected}, got {selected}")
    if plan["max_hops"] > specification["max_hops"]:
        failures.append("planner exceeded max_hops")
    if steps > specification["max_steps"]:
        failures.append(f"planner steps {steps} exceeded {specification['max_steps']}")
    if stop_reason != specification["expected_stop_reason"]:
        failures.append(
            f"expected stop reason {specification['expected_stop_reason']}, got {stop_reason}"
        )
    return EvaluationOutcome(
        metrics={
            "selected_modules": selected,
            "max_hops": plan["max_hops"],
            "steps": steps,
            "stop_reason": stop_reason,
        },
        failures=failures,
    )


def _evaluate_bounded_executor(corpus: dict[str, Any]) -> EvaluationOutcome:
    specification = corpus["agentic_search"]
    registry = ToolRegistry()
    for module in specification["expected_modules"]:
        registry.register(ToolSpec(
            name=f"search.{module}",
            description=f"Synthetic read-only {module} search",
            input_schema=_ModuleInput,
            output_schema=_ModuleOutput,
            handler=lambda context, value: {
                "module": value.module,
                "evidence_count": 1,
            },
        ))
    decisions = [
        PlannerDecision(
            action="tool",
            tool_name=f"search.{module}",
            arguments={"module": module},
            hop=min(index + 1, specification["max_hops"]),
            estimated_tokens=10,
        )
        for index, module in enumerate(specification["expected_modules"])
    ]
    result = BoundedAgentExecutor(registry).run(
        sequence_planner(decisions),
        context=ToolContext(role="ENGINEER", case_id="GOLDEN-CASE"),
        budget=AgentBudget(
            max_steps=specification["max_steps"],
            max_hops=specification["max_hops"],
            max_tokens=1000,
            max_cost=0,
            max_duration_ms=1000,
        ),
    )
    selected = [item["output"]["module"] for item in result.outputs]
    failures: list[str] = []
    if selected != specification["expected_modules"]:
        failures.append(
            f"typed executor selected {selected}, expected {specification['expected_modules']}"
        )
    if result.steps > specification["max_steps"]:
        failures.append("typed executor exceeded max_steps")
    if result.stop_reason.value != specification["expected_stop_reason"]:
        failures.append(
            f"typed executor stopped with {result.stop_reason.value}"
        )
    if any(item.get("tool_permission") != "READ" for item in result.trajectory):
        failures.append("golden search trajectory used a non-read-only tool")
    return EvaluationOutcome(
        metrics={
            "selected_modules": selected,
            "steps": result.steps,
            "tokens": result.tokens,
            "stop_reason": result.stop_reason.value,
            "trajectory_hashes_present": all(
                len(str(item.get("input_summary_hash") or "")) == 64
                for item in result.trajectory
            ),
        },
        failures=failures,
    )


def run_golden_suite(corpus_root: Path | None = None) -> dict[str, Any]:
    """Run all deterministic gates and return a machine-readable report."""
    root = (corpus_root or DEFAULT_CORPUS_ROOT).resolve()
    corpus = json.loads((root / "corpus.json").read_text(encoding="utf-8"))
    checks = [
        _run_check(
            "fixture_integrity",
            lambda: _evaluate_fixture_integrity(root, corpus),
            max_duration_ms=1000,
        ),
        _run_check(
            "log_parser",
            lambda: _evaluate_parser(root, corpus),
            max_duration_ms=int(corpus["parser"]["max_duration_ms"]),
        ),
        _run_check(
            "knowledge_curation",
            lambda: _evaluate_curation(root, corpus),
            max_duration_ms=int(corpus["curation"]["max_duration_ms"]),
        ),
    ]
    graph_started = perf_counter()
    try:
        code_outcome, commit_outcome = _evaluate_graphs(root, corpus)
        graph_exception: Exception | None = None
    except Exception as exc:  # noqa: BLE001
        code_outcome = EvaluationOutcome({}, [f"{type(exc).__name__}: {exc}"])
        commit_outcome = EvaluationOutcome({}, [f"{type(exc).__name__}: {exc}"])
        graph_exception = exc
    graph_duration = round((perf_counter() - graph_started) * 1000, 3)
    for name, outcome in (
        ("code_graph", code_outcome),
        ("commit_graph", commit_outcome),
    ):
        failures = list(outcome.failures)
        if graph_duration > 5000:
            failures.append(f"shared graph evaluation exceeded 5000 ms: {graph_duration} ms")
        checks.append({
            "name": name,
            "status": "PASS" if not failures else "FAIL",
            "duration_ms": graph_duration,
            "budget_ms": 5000,
            "metrics": outcome.metrics,
            "failures": failures,
        })
    del graph_exception
    checks.extend([
        _run_check("memory", lambda: _evaluate_memory(corpus), max_duration_ms=5000),
        _run_check("rag", lambda: _evaluate_rag(corpus), max_duration_ms=5000),
        _run_check(
            "agentic_search",
            lambda: _evaluate_agentic_plan(corpus),
            max_duration_ms=int(corpus["agentic_search"]["max_duration_ms"]),
        ),
        _run_check(
            "bounded_agent_executor",
            lambda: _evaluate_bounded_executor(corpus),
            max_duration_ms=1000,
        ),
    ])
    failures = [
        f"{check['name']}: {failure}"
        for check in checks
        for failure in check["failures"]
    ]
    return {
        "schema_version": 1,
        "corpus_id": corpus["corpus_id"],
        "status": "PASS" if not failures else "FAIL",
        "check_count": len(checks),
        "checks": checks,
        "failures": failures,
    }
