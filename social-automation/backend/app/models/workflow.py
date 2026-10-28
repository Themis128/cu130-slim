import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class PromptTemplate(Base):
    __tablename__ = "prompt_templates"
    __table_args__ = (
        Index("ix_prompt_templates_team", "team_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_template: Mapped[str] = mapped_column(Text, nullable=False)  # with {{variables}}
    n8n_workflow_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    category: Mapped[str | None] = mapped_column(String(50), nullable=True)  # portfolio, announcement, thread, carousel, video
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=[], nullable=False)
    is_public: Mapped[bool] = mapped_column(default=False, nullable=False)
    usage_count: Mapped[int] = mapped_column(default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False)

    team: Mapped["Team"] = relationship("Team", back_populates="prompt_templates")
    creator: Mapped["User"] = relationship("User", back_populates="prompt_templates")
    generated_workflows: Mapped[list["GeneratedWorkflow"]] = relationship("GeneratedWorkflow", back_populates="template")


class GeneratedWorkflow(Base):
    __tablename__ = "generated_workflows"
    __table_args__ = (
        Index("ix_generated_workflows_team", "team_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    n8n_workflow_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    n8n_workflow_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)  # draft, deployed, archived
    template_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("prompt_templates.id", ondelete="SET NULL"), nullable=True)
    variables_used: Mapped[dict] = mapped_column(JSONB, default={}, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False)

    team: Mapped["Team"] = relationship("Team", back_populates="generated_workflows")
    creator: Mapped["User"] = relationship("User", back_populates="generated_workflows")
    template: Mapped["PromptTemplate | None"] = relationship("PromptTemplate", back_populates="generated_workflows")


class ContentPromptTemplate(Base):
    """Saved AI content-generation prompt templates scoped by pillar/tone/platform."""

    __tablename__ = "content_prompt_templates"
    __table_args__ = (
        Index("ix_content_prompt_templates_team", "team_id"),
        Index("ix_content_prompt_templates_pillar", "pillar_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True)
    pillar_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("pillars.id", ondelete="SET NULL"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    platform: Mapped[str | None] = mapped_column(String(30), nullable=True)  # linkedin, twitter, instagram, etc.
    tone: Mapped[str | None] = mapped_column(String(50), nullable=True)  # professional, casual, witty, etc.
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    user_prompt_template: Mapped[str] = mapped_column(Text, nullable=False)  # with {{topic}}, {{brand}}, etc.
    variables: Mapped[list[str]] = mapped_column(ARRAY(String), default=[], nullable=False)
    is_default: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False)

    team: Mapped["Team"] = relationship("Team")
