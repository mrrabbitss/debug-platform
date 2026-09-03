from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.db import Base, configure_sqlite_engine
from app.host_agent_models import HostAgentSession
from app.models import AgentRun, Case
from app.services import host_agent_sessions
from app.services.host_agent_session_contracts import (
    HostAgentEvidenceCacheUpdate,
    HostAgentSessionCancel,
    HostAgentSessionCoverageUpdate,
    HostAgentSessionCreate,
    HostAgentSessionLeaseRelease,
    HostAgentSessionLeaseRequest,
    HostAgentSessionSnapshotUpdate,
    HostAgentSessionStatusTransition,
    HostAgentToolReceiptInput,
)
from app.services.host_agent_sessions import (
    HostAgentSessionConflictError,
    HostAgentSessionError,
    HostAgentSessionExpiredError,
    HostAgentSessionLeaseConflictError,
    HostAgentSessionTransitionError,
    acquire_host_agent_session_lease,
    cancel_host_agent_session,
    create_host_agent_session,
    expire_due_host_agent_sessions,
    hash_host_agent_tool_arguments,
    merge_host_agent_evidence_cache,
    merge_host_agent_session_coverage,
    record_host_agent_tool_receipt,
    release_host_agent_session_lease,
    renew_host_agent_session_lease,
    replace_host_agent_session_snapshot,
    require_host_agent_session,
    transition_host_agent_session,
)


BASE_TIME = datetime(2026, 9, 2, 8, 0, tzinfo=UTC)


def _factory(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'host-agent.db'}")
    configure_sqlite_engine(engine)
    Base.metadata.create_all(bind=engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def _request(case_id: str = "CASE-host", *, ttl_seconds: int = 3600):
    return HostAgentSessionCreate(
        case_id=case_id,
        executor="claude_code",
        client_model_claim="claude-code-current-session",
        prompt_version="host-diagnosis-v1",
        skill_version="gw-ap-debug@1.0.0",
        case_snapshot_hash="a" * 64,
        parse_snapshot_hash="b" * 64,
        method_snapshot_hash="c" * 64,
        snapshot={
            "case_id": case_id,
            "parse_run_ids": ["PRUN-one"],
            "raw_text": "must not persist",
            "description": "password=private 10.0.0.1",
        },
        ttl_seconds=ttl_seconds,
    )


def _seed_case(factory, case_id: str = "CASE-host") -> None:
    with factory() as db:
        db.add(Case(id=case_id, title="Host agent", device_type="AP"))
        db.commit()


def test_create_pins_snapshot_and_creates_provider_neutral_agent_run(
    tmp_path: Path,
) -> None:
    engine, factory = _factory(tmp_path)
    _seed_case(factory)
    with factory() as db:
        view = create_host_agent_session(
            db, _request(), created_by="USER-test", now=BASE_TIME
        )
        run = db.get(AgentRun, view.agent_run_id)

        assert view.status == "CREATED"
        assert view.version == 1
        assert view.executor == "claude_code"
        assert view.client_model_claim_verified is False
        assert view.snapshot["description"] == "password=<MASKED> <IP>"
        assert "raw_text" not in view.snapshot
        assert run is not None
        assert run.execution_mode == "host_cli"
        assert run.model_name == "claude-code-current-session"
        assert run.input_tokens == run.output_tokens == run.total_tokens == 0
        assert db.get(HostAgentSession, view.id) is not None

    service_source = Path(host_agent_sessions.__file__).read_text(encoding="utf-8")
    assert "get_llm_provider" not in service_source
    assert "chat.completions" not in service_source
    engine.dispose()


def test_compare_and_swap_rejects_a_stale_writer(tmp_path: Path) -> None:
    engine, factory = _factory(tmp_path)
    _seed_case(factory)
    with factory() as creator:
        created = create_host_agent_session(creator, _request(), now=BASE_TIME)

    with factory() as first, factory() as stale:
        assert stale.get(HostAgentSession, created.id).version == 1
        advanced = transition_host_agent_session(
            first,
            created.id,
            HostAgentSessionStatusTransition(
                expected_version=1,
                status="METHODS_READ",
                reason="method scan complete",
            ),
            now=BASE_TIME + timedelta(seconds=1),
        )
        assert advanced.version == 2
        with pytest.raises(HostAgentSessionConflictError):
            transition_host_agent_session(
                stale,
                created.id,
                HostAgentSessionStatusTransition(
                    expected_version=1,
                    status="METHODS_READ",
                ),
                now=BASE_TIME + timedelta(seconds=2),
            )
        assert require_host_agent_session(stale, created.id).version == 2
    engine.dispose()


def test_receipts_are_idempotent_and_cache_only_redacted_returned_evidence(
    tmp_path: Path,
) -> None:
    engine, factory = _factory(tmp_path)
    _seed_case(factory)
    arguments_hash = hash_host_agent_tool_arguments({
        "query": "AUTH timeout",
        "top_k": 5,
    })
    assert arguments_hash == hash_host_agent_tool_arguments({
        "top_k": 5,
        "query": "AUTH timeout",
    })

    with factory() as db:
        view = create_host_agent_session(db, _request(), now=BASE_TIME)
        view = acquire_host_agent_session_lease(
            db,
            view.id,
            HostAgentSessionLeaseRequest(
                expected_version=view.version,
                owner="claude-window-1",
                lease_seconds=120,
            ),
            now=BASE_TIME + timedelta(seconds=1),
        )
        view = transition_host_agent_session(
            db,
            view.id,
            HostAgentSessionStatusTransition(
                expected_version=view.version,
                status="METHODS_READ",
                lease_owner="claude-window-1",
            ),
            now=BASE_TIME + timedelta(seconds=2),
        )
        view = transition_host_agent_session(
            db,
            view.id,
            HostAgentSessionStatusTransition(
                expected_version=view.version,
                status="SEARCHING",
                lease_owner="claude-window-1",
            ),
            now=BASE_TIME + timedelta(seconds=3),
        )
        receipt_version = view.version
        receipt = HostAgentToolReceiptInput(
            expected_version=view.version,
            call_id="call-1",
            tool_name="search_log",
            arguments_hash=arguments_hash,
            evidence_ids=["EVT-safe"],
            evidence={
                "EVT-safe": {
                    "evidence_id": "EVT-safe",
                    "source_type": "log_event",
                    "source_file": "ap.log",
                    "line_start": 7,
                    "content": (
                        "password=hunter2 src=10.1.2.3 "
                        "aa:bb:cc:dd:ee:ff"
                    ),
                    "raw_text": "unredacted raw line",
                    "headers": {"authorization": "Bearer secret"},
                }
            },
            lease_owner="claude-window-1",
        )
        view = record_host_agent_tool_receipt(
            db, view.id, receipt, now=BASE_TIME + timedelta(seconds=4)
        )
        assert view.version == receipt_version + 1
        assert view.allowed_evidence_ids == ["EVT-safe"]
        assert view.evidence_cache["EVT-safe"]["content"] == (
            "password=<MASKED> src=<IP> <MAC>"
        )
        assert "raw_text" not in view.evidence_cache["EVT-safe"]
        assert "headers" not in view.evidence_cache["EVT-safe"]
        assert view.tool_receipts[0].model_dump().keys() == {
            "call_id", "tool_name", "arguments_hash", "evidence_ids", "recorded_at",
        }

        # A transport retry with the same call_id and receipt is a no-op even
        # when it repeats the pre-write version.
        duplicate = record_host_agent_tool_receipt(
            db, view.id, receipt, now=BASE_TIME + timedelta(seconds=5)
        )
        assert duplicate.version == view.version
        assert len(duplicate.tool_receipts) == 1

        stored = db.get(HostAgentSession, view.id)
        assert "unredacted raw line" not in stored.evidence_cache_json
        assert "hunter2" not in stored.evidence_cache_json
        assert "AUTH timeout" not in stored.tool_receipts_json
        with pytest.raises(HostAgentSessionError):
            merge_host_agent_evidence_cache(
                db,
                view.id,
                HostAgentEvidenceCacheUpdate(
                    expected_version=view.version,
                    evidence={"EVT-never-returned": {"content": "not allowed"}},
                    lease_owner="claude-window-1",
                ),
                now=BASE_TIME + timedelta(seconds=6),
            )

        view = merge_host_agent_session_coverage(
            db,
            view.id,
            HostAgentSessionCoverageUpdate(
                expected_version=view.version,
                coverage_patch={
                    "fault_tree": {"FT-auth": {"status": "SUPPORTED"}}
                },
                allowed_evidence_ids=["KCHUNK-safe"],
                lease_owner="claude-window-1",
            ),
            now=BASE_TIME + timedelta(seconds=7),
        )
        view = merge_host_agent_evidence_cache(
            db,
            view.id,
            HostAgentEvidenceCacheUpdate(
                expected_version=view.version,
                evidence={
                    "KCHUNK-safe": {
                        "source_type": "knowledge",
                        "content": "token=private diagnostic excerpt",
                    }
                },
                lease_owner="claude-window-1",
            ),
            now=BASE_TIME + timedelta(seconds=8),
        )
        assert view.coverage["fault_tree"]["FT-auth"]["status"] == "SUPPORTED"
        assert view.evidence_cache["KCHUNK-safe"]["content"].startswith(
            "token=<MASKED>"
        )
        assert set(view.allowed_evidence_ids) == {"EVT-safe", "KCHUNK-safe"}
        for offset, status in enumerate(
            ("DRAFT_SUBMITTED", "VALIDATED", "COMPLETED"), start=9
        ):
            view = transition_host_agent_session(
                db,
                view.id,
                HostAgentSessionStatusTransition(
                    expected_version=view.version,
                    status=status,
                    lease_owner="claude-window-1",
                ),
                now=BASE_TIME + timedelta(seconds=offset),
            )
        assert view.status == "COMPLETED"
        run = db.get(AgentRun, view.agent_run_id)
        assert run.status == "COMPLETED"
        assert run.stop_reason == "HOST_AGENT_COMPLETED"
        assert run.input_tokens == run.output_tokens == run.total_tokens == 0
    engine.dispose()


def test_snapshot_is_frozen_after_execution_starts(tmp_path: Path) -> None:
    engine, factory = _factory(tmp_path)
    _seed_case(factory)
    with factory() as db:
        view = create_host_agent_session(db, _request(), now=BASE_TIME)
        view = replace_host_agent_session_snapshot(
            db,
            view.id,
            HostAgentSessionSnapshotUpdate(
                expected_version=view.version,
                case_snapshot_hash="d" * 64,
                parse_snapshot_hash="e" * 64,
                method_snapshot_hash="f" * 64,
                snapshot={"case_id": "CASE-host", "generation": 2},
            ),
            now=BASE_TIME + timedelta(seconds=1),
        )
        assert view.case_snapshot_hash == "d" * 64
        view = transition_host_agent_session(
            db,
            view.id,
            HostAgentSessionStatusTransition(
                expected_version=view.version,
                status="METHODS_READ",
            ),
            now=BASE_TIME + timedelta(seconds=2),
        )
        with pytest.raises(HostAgentSessionTransitionError):
            replace_host_agent_session_snapshot(
                db,
                view.id,
                HostAgentSessionSnapshotUpdate(
                    expected_version=view.version,
                    case_snapshot_hash="a" * 64,
                    parse_snapshot_hash="b" * 64,
                    method_snapshot_hash="c" * 64,
                ),
                now=BASE_TIME + timedelta(seconds=3),
            )
    engine.dispose()


def test_lease_cancel_and_expiration_update_linked_agent_runs(tmp_path: Path) -> None:
    engine, factory = _factory(tmp_path)
    _seed_case(factory)
    _seed_case(factory, "CASE-expire")
    with factory() as db:
        view = create_host_agent_session(db, _request(), now=BASE_TIME)
        view = acquire_host_agent_session_lease(
            db,
            view.id,
            HostAgentSessionLeaseRequest(
                expected_version=view.version,
                owner="codex-one",
                lease_seconds=60,
            ),
            now=BASE_TIME,
        )
        with pytest.raises(HostAgentSessionLeaseConflictError):
            acquire_host_agent_session_lease(
                db,
                view.id,
                HostAgentSessionLeaseRequest(
                    expected_version=view.version,
                    owner="claude-two",
                    lease_seconds=60,
                ),
                now=BASE_TIME + timedelta(seconds=10),
            )
        view = renew_host_agent_session_lease(
            db,
            view.id,
            HostAgentSessionLeaseRequest(
                expected_version=view.version,
                owner="codex-one",
                lease_seconds=120,
            ),
            now=BASE_TIME + timedelta(seconds=20),
        )
        with pytest.raises(HostAgentSessionLeaseConflictError):
            release_host_agent_session_lease(
                db,
                view.id,
                HostAgentSessionLeaseRelease(
                    expected_version=view.version,
                    owner="claude-two",
                ),
                now=BASE_TIME + timedelta(seconds=21),
            )
        view = release_host_agent_session_lease(
            db,
            view.id,
            HostAgentSessionLeaseRelease(
                expected_version=view.version,
                owner="codex-one",
            ),
            now=BASE_TIME + timedelta(seconds=22),
        )
        view = cancel_host_agent_session(
            db,
            view.id,
            HostAgentSessionCancel(
                expected_version=view.version,
                reason="operator stopped the CLI",
            ),
            now=BASE_TIME + timedelta(seconds=23),
        )
        assert view.status == "CANCELLED"
        assert view.completed_at is not None
        run = db.get(AgentRun, view.agent_run_id)
        assert run.status == "CANCELLED"
        assert run.stop_reason == "HOST_AGENT_CANCELLED"

        expiring = create_host_agent_session(
            db,
            _request("CASE-expire", ttl_seconds=300),
            now=BASE_TIME,
        )
        with pytest.raises(HostAgentSessionExpiredError):
            merge_host_agent_session_coverage(
                db,
                expiring.id,
                HostAgentSessionCoverageUpdate(
                    expected_version=expiring.version,
                    coverage_patch={"late": True},
                ),
                now=BASE_TIME + timedelta(seconds=301),
            )
        expired = expire_due_host_agent_sessions(
            db, now=BASE_TIME + timedelta(seconds=301)
        )
        assert [item.id for item in expired] == [expiring.id]
        expired_run = db.get(AgentRun, expiring.agent_run_id)
        assert expired_run.status == "EXPIRED"
        assert expired_run.stop_reason == "HOST_AGENT_SESSION_EXPIRED"
    engine.dispose()
