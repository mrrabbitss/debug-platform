from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.db import Base
from app.models import AgentRun, Case, ConversationMessage
from app.services import case_chat
from app.services.agent_trace_runtime import create_live_agent_run
from app.services.llm import MockProvider


class _JobContext:
    job_id = None

    def update(self, progress: int, message: str) -> None:
        pass

    def raise_if_cancelled(self) -> None:
        pass


def test_case_chat_job_persists_recoverable_messages_and_live_trace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'chat.db'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(case_chat, "SessionLocal", factory)
    monkeypatch.setattr(case_chat, "get_llm_provider", lambda: MockProvider())
    monkeypatch.setattr(
        case_chat,
        "get_active_chat_model_info",
        lambda: {"is_mock": True},
    )
    monkeypatch.setattr(case_chat, "extract_memories_from_chat", lambda *args, **kwargs: None)
    monkeypatch.setattr(case_chat, "agentic_search", lambda *args, **kwargs: {
        "results": [{
            "evidence_id": "EVIDENCE-1",
            "source_type": "knowledge",
            "title": "Synthetic evidence",
            "content": "Synthetic evidence content",
            "source_score": 0.8,
            "metadata": {},
        }],
        "plan": {},
        "trace": [],
        "paths": [],
        "summary": {},
    })
    with factory() as db:
        db.add(Case(
            id="CASE-chat",
            title="Synthetic case",
            description="Synthetic issue",
            model_egress_approved=False,
        ))
        message = ConversationMessage(
            id="MSG-user",
            case_id="CASE-chat",
            role="user",
            content="What should be checked next?",
            status="QUEUED",
        )
        db.add(message)
        run = create_live_agent_run(
            db,
            operation="case_chat",
            case_id="CASE-chat",
            resource_type="conversation_message",
            resource_id=message.id,
            input_summary={"question": message.content},
            prompt_version="case-chat-v3-async-evidence",
        )
        message.agent_run_id = run.id
        db.commit()

    result = case_chat.case_chat_job(
        _JobContext(),
        "CASE-chat",
        "MSG-user",
        run.id,
    )

    with factory() as db:
        user_message = db.get(ConversationMessage, "MSG-user")
        assistant = db.scalar(select(ConversationMessage).where(
            ConversationMessage.case_id == "CASE-chat",
            ConversationMessage.role == "assistant",
        ))
        completed_run = db.get(AgentRun, run.id)
        assert user_message.status == "COMPLETED"
        assert assistant is not None
        assert assistant.status == "COMPLETED"
        assert "Mock" in assistant.content
        assert completed_run.status == "COMPLETED"
        assert completed_run.stop_reason == "COMPLETED"
        assert result["message_id"] == assistant.id
        assert result["citations"] == 1
    engine.dispose()
