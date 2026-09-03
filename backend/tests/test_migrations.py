from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select, text

from app.core.config import BACKEND_ROOT
from app.core.db import Base
from app.core.migrations import run_database_migrations
from app.models import Artifact, Case, LogEvent


def _upgrade_database(database_url: str, revision: str) -> None:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.attributes["database_url"] = database_url
    command.upgrade(config, revision)


def test_migrations_create_fresh_database_and_are_idempotent(tmp_path: Path) -> None:
    database_path = tmp_path / "fresh.db"
    database_url = f"sqlite:///{database_path.as_posix()}"

    run_database_migrations(database_url)
    run_database_migrations(database_url)

    engine = create_engine(database_url)
    table_names = set(inspect(engine).get_table_names())
    assert {
        "cases", "artifacts", "log_events", "model_profiles", "audit_events",
        "user_accounts", "access_tokens", "case_members", "alembic_version",
        "knowledge_derivations", "code_relations", "commit_records",
        "commit_file_changes", "agent_memories",
        "knowledge_revisions", "knowledge_graph_states", "knowledge_entities",
        "knowledge_entity_mentions", "knowledge_relations",
        "diagnosis_feedback", "retrieval_evaluation_datasets",
        "retrieval_evaluation_cases", "retrieval_evaluation_runs",
        "knowledge_curation_sessions", "knowledge_curation_source_files",
        "knowledge_curation_revisions", "knowledge_curation_messages",
        "agent_runs", "agent_trace_events", "host_agent_sessions",
        "log_triage_runs", "log_evidence_matches", "log_evidence_occurrences",
        "analysis_revisions",
    }.issubset(table_names)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0017"
    analysis_column_info = {item["name"]: item for item in inspect(engine).get_columns("analysis_runs")}
    event_indexes = {item["name"] for item in inspect(engine).get_indexes("log_events")}
    model_indexes = {item["name"] for item in inspect(engine).get_indexes("model_profiles")}
    assert "ix_log_events_case_time" in event_indexes
    assert "uq_model_profiles_active_task" in model_indexes
    assert {"model_profile_id", "model_config_json", "agent_run_id"}.issubset(
        analysis_column_info
    )
    assert analysis_column_info["model"]["type"].length == 512
    artifact_columns = {item["name"] for item in inspect(engine).get_columns("artifacts")}
    job_columns = {item["name"] for item in inspect(engine).get_columns("jobs")}
    host_session_columns = {
        item["name"]
        for item in inspect(engine).get_columns("host_agent_sessions")
    }
    assert {
        "agent_run_id", "version", "coverage_json", "planning_rounds_json",
        "tool_receipts_json",
        "allowed_evidence_ids_json", "evidence_cache_json", "lease_owner",
        "lease_expires_at", "expires_at",
    }.issubset(host_session_columns)
    assert {
        "idempotency_key", "attempt", "max_attempts", "available_at",
        "lease_owner", "lease_expires_at", "heartbeat_at", "deadline_at",
        "timeout_seconds", "resource_limits_json", "dead_letter_at",
        "dead_letter_reason",
    }.issubset(job_columns)
    case_columns = {item["name"] for item in inspect(engine).get_columns("cases")}
    event_columns = {item["name"] for item in inspect(engine).get_columns("log_events")}
    assert {
        "active_parse_run_id", "source_device_type", "source_device_role",
    }.issubset(artifact_columns)
    assert "owner_id" in case_columns
    assert "model_egress_approved" in case_columns
    assert "parse_run_id" in event_columns
    repository_columns = {item["name"] for item in inspect(engine).get_columns("repositories")}
    assert {
        "graph_status", "commit_graph_status", "index_metadata_json", "indexed_at",
        "active_graph_generation_id",
    }.issubset(repository_columns)
    model_profile_columns = {
        item["name"] for item in inspect(engine).get_columns("model_profiles")
    }
    assert {
        "active_embedding_generation_id",
        "proxy_url_ciphertext",
        "proxy_url_hint",
    }.issubset(model_profile_columns)
    assert {"generation_id", "logical_id"}.issubset({
        item["name"] for item in inspect(engine).get_columns("code_symbols")
    })
    assert {"generation_id", "logical_id"}.issubset({
        item["name"] for item in inspect(engine).get_columns("code_relations")
    })
    assert "generation_id" in {
        item["name"]
        for item in inspect(engine).get_columns("knowledge_embeddings")
    }
    assert {
        "review_status",
        "version",
        "lock_version",
        "reviewed_by",
        "reviewed_at",
        "review_comment",
        "published_at",
    }.issubset({
        item["name"]
        for item in inspect(engine).get_columns("knowledge_documents")
    })
    assert "uq_report_case_analysis_format_version" in {
        item["name"]
        for item in inspect(engine).get_unique_constraints("reports")
    }
    assert {
        "extracted_text_path",
        "extracted_text_sha256",
        "extraction_method",
        "extraction_truncated",
        "page_count",
    }.issubset({
        item["name"]
        for item in inspect(engine).get_columns("knowledge_curation_source_files")
    })
    engine.dispose()


def test_migrations_adopt_legacy_create_all_database_without_data_loss(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = create_engine(database_url)
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        connection.execute(Case.__table__.insert().values(
            id="CASE-legacy",
            title="legacy case",
            device_type="GW",
            description="",
            status="DRAFT",
            severity="UNKNOWN",
        ))
        connection.execute(Artifact.__table__.insert().values(
            id="ART-legacy",
            case_id="CASE-legacy",
            kind="debug_log",
            original_name="legacy.log",
            stored_path="artifacts/ART-legacy/legacy.log",
            sha256="a" * 64,
            size_bytes=10,
            status="PARSED",
            metadata_json="{}",
        ))
        connection.execute(LogEvent.__table__.insert().values(
            id="EVT-legacy",
            case_id="CASE-legacy",
            artifact_id="ART-legacy",
            source_file="legacy.log",
            line_start=1,
            line_end=1,
            level="ERROR",
            module="SYSTEM",
            component="legacy",
            event_code="LEGACY",
            message="legacy event",
            raw_text="legacy event",
            entities_json="{}",
            parser_id="legacy",
            parser_version="1",
            confidence=1.0,
        ))
    engine.dispose()

    run_database_migrations(database_url)

    engine = create_engine(database_url)
    with engine.connect() as connection:
        title = connection.scalar(select(Case.title).where(Case.id == "CASE-legacy"))
        version = connection.scalar(text("SELECT version_num FROM alembic_version"))
        active_run_id = connection.scalar(text(
            "SELECT active_parse_run_id FROM artifacts WHERE id = 'ART-legacy'"
        ))
        event_run_id = connection.scalar(text(
            "SELECT parse_run_id FROM log_events WHERE id = 'EVT-legacy'"
        ))
    assert title == "legacy case"
    assert version == "0017"
    assert active_run_id == "ART-legacy"
    assert event_run_id == "ART-legacy"
    engine.dispose()


def test_0008_upgrades_existing_graph_vectors_and_report_versions(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "upgrade-from-0007.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    _upgrade_database(database_url, "0007")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO cases "
            "(id, title, device_type, description, status, severity, "
            "created_at, updated_at) VALUES "
            "('CASE-old', 'Old case', 'GW', '', 'DRAFT', 'UNKNOWN', "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ))
        connection.execute(text(
            "INSERT INTO artifacts "
            "(id, case_id, kind, original_name, stored_path, sha256, "
            "size_bytes, status, metadata_json, created_at) VALUES "
            "('ART-old', 'CASE-old', 'source_repository', 'old.zip', "
            "'artifacts/ART-old/old.zip', :digest, 1, 'EXTRACTED', '{}', "
            "CURRENT_TIMESTAMP)"
        ), {"digest": "a" * 64})
        connection.execute(text(
            "INSERT INTO repositories "
            "(id, case_id, artifact_id, name, root_path, status, "
            "graph_status, commit_graph_status, index_metadata_json, "
            "created_at) VALUES "
            "('REPO-old', 'CASE-old', 'ART-old', 'old', "
            "'repositories/REPO-old', 'INDEXED', 'INDEXED', "
            "'UNAVAILABLE', '{}', CURRENT_TIMESTAMP)"
        ))
        connection.execute(text(
            "INSERT INTO code_symbols "
            "(id, repository_id, kind, name, file_path, line_start, "
            "line_end, code, calls_json, metadata_json) VALUES "
            "('SYM-old', 'REPO-old', 'function', 'old', 'old.py', "
            "1, 1, 'def old(): pass', '[]', '{}')"
        ))
        connection.execute(text(
            "INSERT INTO code_relations "
            "(id, repository_id, source_symbol_id, target_name, "
            "relation_type, confidence, evidence_json, created_at) VALUES "
            "('REL-old', 'REPO-old', 'SYM-old', 'target', 'CALLS', "
            "0.5, '{}', CURRENT_TIMESTAMP)"
        ))
        connection.execute(text(
            "INSERT INTO knowledge_documents "
            "(id, title, source_type, trust_level, confidentiality, "
            "content, metadata_json, active, created_at, updated_at) VALUES "
            "('DOC-old', 'Old document', 'document', 'MEDIUM', "
            "'INTERNAL', 'legacy content', '{}', 1, CURRENT_TIMESTAMP, "
            "CURRENT_TIMESTAMP)"
        ))
        connection.execute(text(
            "INSERT INTO knowledge_chunks "
            "(id, document_id, chunk_index, content, token_estimate, "
            "metadata_json) VALUES "
            "('CHK-old', 'DOC-old', 0, 'legacy content', 2, '{}')"
        ))
        connection.execute(text(
            "INSERT INTO model_profiles "
            "(id, name, task_type, mode, provider, model_name, config_json, "
            "enabled, is_active, created_at, updated_at) VALUES "
            "('MODEL-old', 'Old embedding', 'embedding', 'builtin', "
            "'hashing', 'hashing-char-384', '{}', 1, 1, "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ))
        connection.execute(text(
            "INSERT INTO knowledge_embeddings "
            "(id, chunk_id, profile_id, dimension, vector_json, created_at) "
            "VALUES ('VEC-old', 'CHK-old', 'MODEL-old', 2, '[1, 0]', "
            "CURRENT_TIMESTAMP)"
        ))
        connection.execute(text(
            "INSERT INTO analysis_runs "
            "(id, case_id, status, provider, model, prompt_version, "
            "result_json, evidence_json, model_config_json, created_at) "
            "VALUES ('ANL-old', 'CASE-old', 'COMPLETED', 'mock', 'mock', "
            "'1', '{}', '[]', '{}', CURRENT_TIMESTAMP)"
        ))
        for report_id, version, path in (
            ("RPT-one", 1, "reports/one.html"),
            ("RPT-gap", 3, "reports/gap.html"),
            ("RPT-duplicate", 3, "reports/duplicate.html"),
        ):
            connection.execute(text(
                "INSERT INTO reports "
                "(id, case_id, analysis_run_id, format, version, "
                "stored_path, sha256, created_at) VALUES "
                "(:id, 'CASE-old', 'ANL-old', 'html', :version, :path, "
                ":digest, CURRENT_TIMESTAMP)"
            ), {
                "id": report_id,
                "version": version,
                "path": path,
                "digest": "b" * 64,
            })
    engine.dispose()

    _upgrade_database(database_url, "head")

    engine = create_engine(database_url)
    with engine.connect() as connection:
        assert connection.scalar(text(
            "SELECT active_graph_generation_id FROM repositories "
            "WHERE id = 'REPO-old'"
        )) == "legacy"
        assert connection.execute(text(
            "SELECT generation_id, logical_id FROM code_symbols "
            "WHERE id = 'SYM-old'"
        )).one() == ("legacy", "SYM-old")
        assert connection.execute(text(
            "SELECT generation_id, logical_id FROM code_relations "
            "WHERE id = 'REL-old'"
        )).one() == ("legacy", "REL-old")
        assert connection.scalar(text(
            "SELECT active_embedding_generation_id FROM model_profiles "
            "WHERE id = 'MODEL-old'"
        )) == "legacy"
        assert connection.scalar(text(
            "SELECT generation_id FROM knowledge_embeddings "
            "WHERE id = 'VEC-old'"
        )) == "legacy"
        reports = dict(connection.execute(text(
            "SELECT id, version FROM reports"
        )).all())
        assert reports["RPT-one"] == 1
        assert sorted((
            reports["RPT-gap"],
            reports["RPT-duplicate"],
        )) == [3, 4]
        assert connection.scalar(text(
            "SELECT stored_path FROM reports WHERE id = 'RPT-gap'"
        )) == "reports/gap.html"
        assert connection.scalar(text(
            "SELECT review_status FROM knowledge_documents "
            "WHERE id = 'DOC-old'"
        )) == "ACTIVE"
        assert connection.scalar(text(
            "SELECT COUNT(*) FROM knowledge_revisions "
            "WHERE document_id = 'DOC-old'"
        )) == 1
    engine.dispose()
