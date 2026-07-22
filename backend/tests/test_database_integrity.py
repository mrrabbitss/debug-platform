from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.core.db import Base, configure_sqlite_engine
from app.models import Artifact, Case, ConversationMessage


def test_sqlite_foreign_keys_and_cascade_are_enabled(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'integrity.db'}")
    configure_sqlite_engine(engine)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as db:
        db.add(Case(id="CASE-cascade", title="cascade", description=""))
        db.add(Artifact(
            id="ART-cascade",
            case_id="CASE-cascade",
            original_name="log.txt",
            stored_path="artifacts/ART-cascade/log.txt",
            sha256="a" * 64,
            size_bytes=1,
        ))
        db.add(ConversationMessage(
            id="MSG-cascade",
            case_id="CASE-cascade",
            role="user",
            content="test",
        ))
        db.commit()

        db.execute(Case.__table__.delete().where(Case.id == "CASE-cascade"))
        db.commit()

        assert db.scalar(select(func.count(Artifact.id))) == 0
        assert db.scalar(select(func.count(ConversationMessage.id))) == 0

    engine.dispose()
