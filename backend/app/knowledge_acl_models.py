from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class KnowledgeAccess(Base):
    __tablename__ = "knowledge_access"
    document_id: Mapped[str] = mapped_column(ForeignKey("knowledge_documents.id", ondelete="CASCADE"), primary_key=True)
    owner_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    publisher_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
