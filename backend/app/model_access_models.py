"""Model ownership is separate from launcher-managed runtime configuration."""
from sqlalchemy import CheckConstraint, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ModelProfileAccess(Base):
    __tablename__ = "model_profile_access"
    __table_args__ = (
        CheckConstraint("visibility IN ('SHARED', 'PRIVATE')", name="ck_model_access_visibility"),
        CheckConstraint("visibility = 'SHARED' OR owner_id IS NOT NULL", name="ck_private_model_owner"),
    )

    profile_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("model_profiles.id", ondelete="CASCADE"), primary_key=True,
    )
    owner_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    visibility: Mapped[str] = mapped_column(String(16), default="SHARED", nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    __mapper_args__ = {"version_id_col": version}
