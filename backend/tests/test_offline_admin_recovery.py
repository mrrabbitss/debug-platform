"""Offline administrator recovery issues only a temporary token for an existing administrator."""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import sessionmaker

from app.core.db import Base
from app.core.utils import utcnow
from app.models import AccessToken, AuditEvent, UserAccount
from app.services.access_control import authenticate_access_token, token_digest


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def server_admin_module():
    portable = str(PROJECT_ROOT / "deploy/windows-portable")
    if portable not in sys.path:
        sys.path.insert(0, portable)
    path = PROJECT_ROOT / "deploy/windows-server/server_admin.py"
    spec = importlib.util.spec_from_file_location("offline_admin_recovery_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def recovery_database(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'recovery.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add_all([
            UserAccount(id="USR-admin", username="admin", display_name="Administrator", role="ADMIN"),
            UserAccount(id="USR-engineer", username="e12345678", display_name="Engineer", role="ENGINEER"),
        ])
        db.commit()
    return factory


def _config(tmp_path: Path):
    root = tmp_path / "server-data"
    (root / "config").mkdir(parents=True)
    return SimpleNamespace(root=root)


def test_recovery_issues_file_backed_token_for_only_active_admin(
    server_admin_module, recovery_database, tmp_path: Path,
):
    config = _config(tmp_path)
    result = server_admin_module.recover_admin_access(
        config, username=None, expires_days=7, session_factory=recovery_database,
    )

    credential = config.root / "config/admin-recovery-token.txt"
    raw = credential.read_text(encoding="utf-8").strip()
    assert raw.startswith("gwdp_")
    assert raw not in str(result)
    assert result["administrator"] == "admin"
    assert result["expires_days"] == 7
    with recovery_database() as db:
        users = db.scalars(select(UserAccount).order_by(UserAccount.username)).all()
        assert [(user.username, user.role, user.active) for user in users] == [
            ("admin", "ADMIN", True), ("e12345678", "ENGINEER", True),
        ]
        token = db.scalar(select(AccessToken).where(AccessToken.name == "offline-admin-recovery"))
        assert token is not None and token.user_id == "USR-admin" and token.expires_at is not None
        audit = db.scalar(select(AuditEvent).where(AuditEvent.action == "auth.admin_recovery_token_issued"))
        assert audit is not None and raw not in audit.details_json

    with pytest.raises(ValueError, match="already exists"):
        server_admin_module.recover_admin_access(
            config, username=None, expires_days=7, session_factory=recovery_database,
        )
    with recovery_database() as db:
        assert len(db.scalars(select(AccessToken).where(AccessToken.name == "offline-admin-recovery")).all()) == 1


def test_recovery_requires_exact_administrator_when_more_than_one_is_active(
    server_admin_module, recovery_database, tmp_path: Path,
):
    config = _config(tmp_path)
    with recovery_database() as db:
        db.add(UserAccount(id="USR-second", username="second-admin", display_name="Second", role="ADMIN"))
        db.commit()

    with pytest.raises(ValueError, match="exactly one active administrator"):
        server_admin_module.recover_admin_access(
            config, username=None, expires_days=7, session_factory=recovery_database,
        )
    assert not (config.root / "config/admin-recovery-token.txt").exists()
    result = server_admin_module.recover_admin_access(
        config, username="second-admin", expires_days=7, session_factory=recovery_database,
    )
    assert result["administrator"] == "second-admin"


def test_recovery_does_not_commit_a_token_when_credential_file_cannot_be_created(
    server_admin_module, recovery_database, tmp_path: Path,
):
    config = SimpleNamespace(root=tmp_path / "missing-server-data")
    with pytest.raises(FileNotFoundError):
        server_admin_module.recover_admin_access(
            config, username=None, expires_days=7, session_factory=recovery_database,
        )
    with recovery_database() as db:
        assert db.scalars(select(AccessToken)).all() == []


def test_new_credential_write_failure_removes_only_its_partial_file(server_admin_module, monkeypatch, tmp_path: Path):
    path = tmp_path / "credential.txt"
    monkeypatch.setattr(server_admin_module.os, "fsync", lambda _fd: (_ for _ in ()).throw(OSError("disk failure")))

    with pytest.raises(OSError, match="disk failure"):
        server_admin_module._write_new_credential(path, "gwdp_" + "x" * 43)
    assert not path.exists()


def test_new_bootstrap_resumes_a_durable_unregistered_credential(
    server_admin_module, recovery_database, monkeypatch, tmp_path: Path,
):
    config = _config(tmp_path)
    raw = "gwdp_" + "b" * 43
    bootstrap = config.root / "config/bootstrap-token.txt"
    bootstrap.write_text(raw + "\n", encoding="utf-8")
    with recovery_database() as db:
        db.execute(delete(UserAccount))
        db.commit()
    import app.core.db as core_db
    import app.core.migrations as migrations

    monkeypatch.setattr(core_db, "SessionLocal", recovery_database)
    monkeypatch.setattr(migrations, "run_database_migrations", lambda: None)
    result = server_admin_module.initialize_admin(config)

    assert result["administrator"] == "admin"
    assert bootstrap.read_text(encoding="utf-8").strip() == raw
    with recovery_database() as db:
        identity = authenticate_access_token(db, raw)
        assert identity is not None and identity["role"] == "ADMIN" and identity["username"] == "admin"


def test_new_recovery_token_authenticates_as_administrator(
    server_admin_module, recovery_database, tmp_path: Path,
):
    config = _config(tmp_path)
    server_admin_module.recover_admin_access(config, username=None, expires_days=7, session_factory=recovery_database)
    raw = (config.root / "config/admin-recovery-token.txt").read_text(encoding="utf-8").strip()
    with recovery_database() as db:
        identity = authenticate_access_token(db, raw)
    assert identity == {
        "id": "USR-admin", "username": "admin", "display_name": "Administrator",
        "role": "ADMIN", "type": "user_token", "token_id": identity["token_id"],
    }


@pytest.mark.parametrize("state", ["unregistered", "expired", "revoked"])
def test_new_recovery_replaces_only_an_unusable_own_credential(
    server_admin_module, recovery_database, tmp_path: Path, state: str,
):
    config = _config(tmp_path)
    old_raw = "gwdp_" + ("c" if state == "unregistered" else "d") * 43
    credential = config.root / "config/admin-recovery-token.txt"
    credential.write_text(old_raw + "\n", encoding="utf-8")
    if state != "unregistered":
        with recovery_database() as db:
            db.add(AccessToken(
                id=f"TOK-{state}", user_id="USR-admin", name="offline-admin-recovery",
                token_hash=token_digest(old_raw), token_hint="gwdp_...old", expires_at=(
                    utcnow() - timedelta(days=1) if state == "expired" else utcnow() + timedelta(days=1)
                ), revoked_at=utcnow() if state == "revoked" else None,
            ))
            db.commit()

    result = server_admin_module.recover_admin_access(
        config, username=None, expires_days=7, session_factory=recovery_database,
    )
    assert result["administrator"] == "admin"
    assert credential.read_text(encoding="utf-8").strip() != old_raw
    with recovery_database() as db:
        audit = db.scalars(select(AuditEvent).where(
            AuditEvent.action == "auth.admin_recovery_token_issued",
        ).order_by(AuditEvent.created_at.desc())).first()
    assert audit is not None and json.loads(audit.details_json)["replaced_credential_state"] == state


@pytest.mark.parametrize(("other_account", "message"), [
    (False, "still usable"),
    (True, "another account"),
])
def test_new_recovery_never_replaces_a_usable_or_other_account_credential(
    server_admin_module, recovery_database, tmp_path: Path, other_account: bool, message: str,
):
    config = _config(tmp_path)
    raw = "gwdp_" + "e" * 43
    credential = config.root / "config/admin-recovery-token.txt"
    credential.write_text(raw + "\n", encoding="utf-8")
    with recovery_database() as db:
        db.add(AccessToken(
            id="TOK-existing", user_id="USR-engineer" if other_account else "USR-admin",
            name="offline-admin-recovery", token_hash=token_digest(raw), token_hint="gwdp_...old",
            expires_at=utcnow() + timedelta(days=1),
        ))
        db.commit()

    with pytest.raises(ValueError, match=message):
        server_admin_module.recover_admin_access(
            config, username=None, expires_days=7, session_factory=recovery_database,
        )
    assert credential.read_text(encoding="utf-8").strip() == raw


def test_new_recovery_denies_a_disabled_administrator(server_admin_module, recovery_database, tmp_path: Path):
    config = _config(tmp_path)
    with recovery_database() as db:
        db.get(UserAccount, "USR-admin").active = False
        db.commit()

    with pytest.raises(ValueError, match="not an active administrator"):
        server_admin_module.recover_admin_access(
            config, username="admin", expires_days=7, session_factory=recovery_database,
        )
    assert not (config.root / "config/admin-recovery-token.txt").exists()


def test_new_stopped_server_lock_rejects_an_open_server_port(server_admin_module, monkeypatch, tmp_path: Path):
    config = SimpleNamespace(root=tmp_path, origin="https://127.0.0.1:18443", backend_port=18080)
    monkeypatch.setattr(server_admin_module, "_server_port_is_open", lambda _port: True)

    with pytest.raises(RuntimeError, match="appears to be running"):
        with server_admin_module._stopped_server_lock(config):
            pytest.fail("Recovery acquired the stopped-server lock while a configured port was open")
