from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.db import Base, configure_sqlite_engine
from app.core.utils import json_dumps
from app.models import Artifact, Case
from app.services import demo_cases, demo_diagnostic_methods, host_diagnostic_runtime
from app.services.demo_case_contract import (
    DEMO_CASE_ID_PREFIX,
    DEMO_DATASET_VERSION,
    DEMO_FIXTURES,
)
from app.services.demo_diagnostic_methods import (
    DEMO_HOST_METHOD_LOG_ID,
    DEMO_HOST_METHOD_TREE_ID,
    BundledDemoMethodError,
    load_bundled_demo_diagnostic_methods,
    load_bundled_demo_methods_for_case,
    validate_bundled_demo_method_compilation,
)
from app.services.diagnostic_methods import (
    DiagnosticMethodDocument,
    compile_diagnostic_patterns,
)
from app.services.diagnostic_fault_tree_baseline import (
    complete_fault_tree_with_deterministic_evidence,
)
from app.services.diagnostic_planning_coverage import initial_fault_tree_coverage
from app.services.fault_tree_coverage import compile_fault_tree_items
from app.services.storage import StorageService


def _database(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'demo-methods.db'}",
        connect_args={"check_same_thread": False},
    )
    configure_sqlite_engine(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    return engine, factory


def _demo_case() -> Case:
    return Case(
        id=f"{DEMO_CASE_ID_PREFIX}-0123456789",
        title="Synthetic AP frequent-offline demo",
        description="[SYNTHETIC DEMO]",
        device_type="AP",
    )


def _demo_artifacts(case_id: str) -> list[Artifact]:
    return [Artifact(
        id=f"ART-demo-method-{index}",
        case_id=case_id,
        kind="debug_log",
        original_name=fixture.filename,
        stored_path=f"artifacts/demo/{fixture.filename}",
        sha256=fixture.sha256,
        size_bytes=100,
        status="PARSED",
        source_device_type=fixture.device_type,
        source_device_role=fixture.device_role,
        metadata_json=json_dumps({
            "demo_fixture": {
                "dataset_version": DEMO_DATASET_VERSION,
                "synthetic": True,
                "source_sha256": fixture.sha256,
            },
        }),
        active_parse_run_id=f"PRUN-demo-method-{index}",
    ) for index, fixture in enumerate(DEMO_FIXTURES, start=1)]


def test_bundled_demo_methods_are_integrity_pinned_and_compile_27_nodes() -> None:
    methods = load_bundled_demo_diagnostic_methods()
    patterns = compile_diagnostic_patterns(methods)
    items = compile_fault_tree_items(methods)

    validate_bundled_demo_method_compilation(methods, patterns, items)
    assert {method.id for method in methods} == {
        DEMO_HOST_METHOD_LOG_ID,
        DEMO_HOST_METHOD_TREE_ID,
    }
    assert {method.role for method in methods} == {
        "LOG_ANALYSIS_METHOD",
        "FAULT_TREE",
    }
    assert all("[SYNTHETIC DEMO METHOD]" in method.content for method in methods)
    assert len(items) == 27
    assert {
        item.label for item in items if item.category == "ROOT_CAUSE"
    } == {"场景1", "场景2", "场景3"}


def test_demo_method_scope_requires_exact_case_and_artifact_contract() -> None:
    case = _demo_case()
    artifacts = _demo_artifacts(case.id)
    methods = load_bundled_demo_methods_for_case(case, artifacts)
    assert methods is not None
    assert len(methods) == 2

    ordinary = Case(id="CASE-ordinary", title="Ordinary case", description="")
    assert load_bundled_demo_methods_for_case(ordinary, artifacts) is None

    artifacts[0].sha256 = "0" * 64
    with pytest.raises(BundledDemoMethodError, match="artifact contract failed"):
        load_bundled_demo_methods_for_case(case, artifacts)


def test_demo_method_manifest_drift_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(
        demo_diagnostic_methods,
        "DEMO_HOST_METHOD_MANIFEST_SHA256",
        "0" * 64,
    )
    with pytest.raises(BundledDemoMethodError, match="manifest integrity"):
        load_bundled_demo_diagnostic_methods()


def test_host_snapshot_injects_demo_methods_without_using_global_loader(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine, factory = _database(tmp_path)
    case = _demo_case()
    with factory() as db:
        db.add(case)
        db.add_all(_demo_artifacts(case.id))
        db.commit()

    monkeypatch.setattr(
        host_diagnostic_runtime,
        "load_applicable_diagnostic_methods",
        lambda _db, _case: (_ for _ in ()).throw(
            AssertionError("global diagnostic methods were loaded for the demo")
        ),
    )
    snapshot = host_diagnostic_runtime.load_host_diagnostic_snapshot(
        case.id,
        session_factory=factory,
    )
    assert {method.id for method in snapshot.methods} == {
        DEMO_HOST_METHOD_LOG_ID,
        DEMO_HOST_METHOD_TREE_ID,
    }
    assert len(snapshot.fault_tree_items) == 27
    engine.dispose()


def test_imported_demo_builds_self_contained_host_context(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine, factory = _database(tmp_path)
    monkeypatch.setattr(demo_cases, "storage", StorageService(tmp_path / "storage"))
    with factory() as db:
        imported = demo_cases.import_ap_frequent_offline_demo(
            db,
            principal={
                "id": "mcp-demo-method-test",
                "username": "mcp-demo-method-test",
                "role": "ADMIN",
                "type": "mcp_bearer",
            },
        )
        case_id = imported["case"].id

    monkeypatch.setattr(
        host_diagnostic_runtime,
        "load_applicable_diagnostic_methods",
        lambda _db, _case: (_ for _ in ()).throw(
            AssertionError("global diagnostic methods were loaded for the demo")
        ),
    )
    snapshot = host_diagnostic_runtime.load_host_diagnostic_snapshot(
        case_id,
        session_factory=factory,
    )
    context = host_diagnostic_runtime.host_diagnostic_context(snapshot)
    assert len(context["method_manifest"]) == 2
    assert len(context["fault_tree_coverage"]) == 27
    assert {
        item["status"] for item in context["fault_tree_coverage"].values()
    } == {"PENDING"}
    assert len(context["artifacts"]) == 2

    completed = complete_fault_tree_with_deterministic_evidence(
        initial_fault_tree_coverage(snapshot.fault_tree_items),
        items=snapshot.fault_tree_items,
        evidence=snapshot.initial_evidence,
        patterns=snapshot.patterns,
        round_number=1,
        reason="TEST_BUNDLED_DEMO_METHODS",
    )
    roots = {
        item["label"]: item["status"]
        for item in completed["items"]
        if item["category"] == "ROOT_CAUSE"
    }
    assert completed["complete"] is True
    assert roots == {
        "场景1": "EXCLUDED",
        "场景2": "SUPPORTED",
        "场景3": "INSUFFICIENT_EVIDENCE",
    }
    engine.dispose()


def test_host_snapshot_does_not_inject_demo_methods_into_ordinary_cases(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine, factory = _database(tmp_path)
    with factory() as db:
        db.add(Case(id="CASE-ordinary", title="Ordinary case", description=""))
        db.commit()

    ordinary_method = DiagnosticMethodDocument(
        id="METHOD-ordinary",
        title="Ordinary method",
        source_type="analysis_method",
        version=1,
        device_type="GENERAL",
        module=None,
        content="Check ordinary evidence.",
        content_sha256="1" * 64,
        role="LOG_ANALYSIS_METHOD",
    )
    monkeypatch.setattr(
        host_diagnostic_runtime,
        "load_applicable_diagnostic_methods",
        lambda _db, _case: [ordinary_method],
    )
    snapshot = host_diagnostic_runtime.load_host_diagnostic_snapshot(
        "CASE-ordinary",
        session_factory=factory,
    )
    assert [method.id for method in snapshot.methods] == ["METHOD-ordinary"]
    assert all(not method.id.startswith("DEMO-HOST-") for method in snapshot.methods)
    engine.dispose()
