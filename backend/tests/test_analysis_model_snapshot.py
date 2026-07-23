from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.db import Base
from app.core.utils import json_loads
from app.models import AnalysisRun, Case
from app.services import diagnosis


class _JobContext:
    def update(self, progress: int, message: str) -> None:
        pass

    def complete_in_transaction(self, db, result, message: str = "Completed") -> None:
        pass


def test_analysis_records_safe_model_configuration_snapshot(tmp_path: Path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'model-snapshot.db'}")
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    with session_factory() as db:
        db.add(Case(id="CASE-snapshot", title="snapshot", description=""))
        db.commit()

    monkeypatch.setattr(diagnosis, "SessionLocal", session_factory)
    monkeypatch.setattr(diagnosis.retriever, "search", lambda *args, **kwargs: [])
    monkeypatch.setattr(diagnosis, "_find_related_symbols", lambda case_id, events: [])
    monkeypatch.setattr(diagnosis, "get_active_chat_model_info", lambda: {
        "profile_id": "MODEL-qwen",
        "profile_name": "Qwen production",
        "provider": "openai_compatible",
        "model": "qwen-plus",
        "mode": "api",
        "base_url": "https://model.example.com/v1",
        "config": {"temperature": 0.1, "timeout_seconds": 60},
        "is_mock": False,
    })

    async def keep_deterministic_result(case, result, evidence):
        return result

    monkeypatch.setattr(diagnosis, "_augment_with_llm", keep_deterministic_result)
    diagnosis._analyze_case_impl(_JobContext(), "CASE-snapshot")

    with session_factory() as db:
        run = db.scalar(select(AnalysisRun).where(AnalysisRun.case_id == "CASE-snapshot"))
        assert run is not None
        assert run.status == "COMPLETED"
        assert run.model_profile_id == "MODEL-qwen"
        assert run.prompt_version == "v2-evidence-validated"
        snapshot = json_loads(run.model_config_json, {})
        assert snapshot["profile_name"] == "Qwen production"
        assert snapshot["base_url"] == "https://model.example.com/v1"
        assert snapshot["config"]["timeout_seconds"] == 60
        assert "api_key" not in run.model_config_json.lower()

    engine.dispose()
