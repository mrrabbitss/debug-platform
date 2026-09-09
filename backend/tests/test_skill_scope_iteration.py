"""New category persistence, approved Skill boundaries and complete model reading."""
from types import SimpleNamespace
import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.db import Base
from app.core.utils import json_dumps
from app.models import Case, KnowledgeDocument
from app.workbench_models import WorkbenchRecord
from app.services.diagnostic_agent_budget import DiagnosticAgentBudget
from app.services.diagnostic_methods import load_applicable_diagnostic_methods
from app.services.diagnostic_skill_reading import read_skills
from app.services.agent_runtime.context import ContextWindowPolicy
from app.services.problem_categories import categories_with_status, change_category
from app.services.workbench import categories, validate_case_options, use_configuration


@pytest.fixture
def store(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'skills.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        db.add(Case(id="CASE-scope", title="Synthetic", device_type="GW", problem_category="network",
                    model_egress_approved=True))
        db.commit()
    yield factory
    engine.dispose()


def document(db, key, *, category="network", kind="SKILL", active=True, content="Synthetic published method"):
    row = KnowledgeDocument(id=key, title=key, source_type="analysis_skill", content=content, active=active,
        review_status="ACTIVE" if active else "DRAFT", confidentiality="INTERNAL",
        metadata_json=json_dumps({"problem_categories": [category], "content_kind": kind,
                                 "knowledge_role": "diagnosis"}))
    db.add(row)
    db.flush()
    return row


def methods(store):
    with store() as db:
        return load_applicable_diagnostic_methods(db, db.get(Case, "CASE-scope"))


def test_only_published_skills_are_mandatory_and_empty_category_does_not_cross_fallback(store):
    with store() as db:
        document(db, "ordinary-SKILL.md", kind="KNOWLEDGE")
        document(db, "connection-skill", category="connection")
        document(db, "draft-skill", active=False)
        db.commit()
    assert methods(store) == []
    with store() as db:
        status = next(item for item in categories_with_status(db) if item["id"] == "network")["skill_status"]
        assert status["mode"] == "EVIDENCE_ONLY" and "未使用任何 Skill" in status["warning"]
        document(db, "general-skill", category="general")
        db.commit()
    assert [item.id for item in methods(store)] == ["general-skill"]
    with store() as db:
        assert next(item for item in categories_with_status(db) if item["id"] == "network")["skill_status"]["mode"] == "GENERAL_ONLY"


def test_unknown_may_read_across_categories_and_new_category_is_persistent(store):
    with store() as db:
        document(db, "network-skill")
        document(db, "connection-skill", category="connection")
        db.get(Case, "CASE-scope").problem_category = "unknown"
        added = change_category(db, "expert", name="Synthetic power issues")
        db.commit()
    assert {item.id for item in methods(store)} == {"network-skill", "connection-skill"}
    with store() as db:
        validate_case_options(db, {"problem_category": added["id"]})
        assert added in categories(db)
        changed = change_category(db, "expert", category_id=added["id"], version=added["version"], name="Synthetic renamed")
        db.commit()
    with store() as db:
        with pytest.raises(ValueError, match="已变化"):
            change_category(db, "expert", category_id=added["id"], version=added["version"], name="Stale")
        db.rollback()
        change_category(db, "expert", category_id=added["id"], version=changed["version"], deactivate=True)
        db.commit()
    with store() as db:
        assert added["id"] not in {item["id"] for item in categories(db)}
        assert db.get(Case, "CASE-scope").problem_category == "unknown"


def test_builtin_category_versions_and_nonempty_delete_are_fenced(store):
    with store() as db:
        updated = change_category(db, "expert", category_id="network", version=1, name="Synthetic network")
        document(db, "published")
        db.commit()
    assert updated["version"] == 2
    with store() as db:
        with pytest.raises(ValueError, match="仍有生效"):
            change_category(db, "expert", category_id="network", version=2, deactivate=True)
        db.rollback()
        with pytest.raises(ValueError, match="不能停用"):
            change_category(db, "expert", category_id="unknown", version=1, deactivate=True)


class Chat:
    profile = None
    def __init__(self, fail_at=None):
        self.calls, self.fail_at = [], fail_at
    async def generate_json(self, system, user, **kwargs):
        payload = json.loads(user)
        self.calls.append(payload)
        if len(self.calls) == self.fail_at:
            raise RuntimeError("Synthetic provider failure")
        return {"notes": "Synthetic reading through character " + str(payload["end"])}


def read(store, model, *, run="AR-read", budget=None):
    with store() as db:
        case = db.get(Case, "CASE-scope")
    return read_skills(SimpleNamespace(raise_if_cancelled=lambda: None, update=lambda *args: None),
        provider=model, case=case, agent_run_id=run, methods=methods(store), session_factory=store,
        context_policy=ContextWindowPolicy(context_window_tokens=8192, reserved_output_tokens=1024,
                                           safety_margin_tokens=512), budget=budget or DiagnosticAgentBudget())


def test_every_character_is_sent_and_receipts_survive_new_session(store):
    text = "中文日志原则\n" * 1850 + "FILE_END"
    with store() as db:
        document(db, "full-file", content=text)
        db.commit()
    model = Chat()
    result = read(store, model)
    assert "".join(item["text"] for item in model.calls) == text
    assert model.calls[0]["start"] == 0 and model.calls[-1]["end"] == len(text)
    assert result["model_reading"]["complete"] is True
    assert result["documents"][0]["content_is_reading_notes"] is True
    later = Chat()
    cached = read(store, later)
    assert later.calls == []
    assert cached["model_reading"]["segments"] == result["model_reading"]["segments"]
    with use_configuration({"model_profile_fingerprint": "different-model"}):
        read(store, later)
    assert later.calls


def test_failed_read_keeps_only_successful_segments_and_retry_cannot_claim_unread_text(store):
    with store() as db:
        document(db, "full-file", content="Synthetic log principles\n" * 800)
        db.commit()
    model = Chat(fail_at=2)
    with pytest.raises(ValueError, match="请求失败"):
        read(store, model)
    with store() as db:
        rows = list(db.scalars(select(WorkbenchRecord).where(WorkbenchRecord.kind == "diagnostic_skill_reading")))
        assert len(rows) == 1
    recovered = Chat()
    result = read(store, recovered)
    assert recovered.calls[0]["start"] == model.calls[0]["end"]
    assert result["model_reading"]["complete"]


def test_reading_budget_and_revoked_consent_stop_before_egress(store):
    with store() as db:
        document(db, "full-file", content="Synthetic rules" * 800)
        db.commit()
    model = Chat()
    with pytest.raises(ValueError, match="模型预算"):
        read(store, model, budget=DiagnosticAgentBudget(max_total_tokens=1))
    with store() as db:
        db.get(Case, "CASE-scope").model_egress_approved = False
        db.commit()
    with pytest.raises(ValueError, match="授权已关闭"):
        read(store, model)
    assert model.calls == []
