import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class UserRole(enum.StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    timezone: Mapped[str] = mapped_column(String(50), default="UTC", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False)

    # Relationships
    owned_teams: Mapped[list["Team"]] = relationship("Team", back_populates="owner", foreign_keys="Team.owner_id")
    team_memberships: Mapped[list["TeamMember"]] = relationship("TeamMember", back_populates="user")
    posts: Mapped[list["Post"]] = relationship("Post", back_populates="author")
    media_assets: Mapped[list["MediaAsset"]] = relationship("MediaAsset", back_populates="uploader")
    prompt_templates: Mapped[list["PromptTemplate"]] = relationship("PromptTemplate", back_populates="creator")
    generated_workflows: Mapped[list["GeneratedWorkflow"]] = relationship("GeneratedWorkflow", back_populates="creator")


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)

    owner: Mapped["User"] = relationship("User", back_populates="owned_teams", foreign_keys=[owner_id])
    members: Mapped[list["TeamMember"]] = relationship("TeamMember", back_populates="team", cascade="all, delete-orphan")
    social_accounts: Mapped[list["SocialAccount"]] = relationship("SocialAccount", back_populates="team", cascade="all, delete-orphan")
    posts: Mapped[list["Post"]] = relationship("Post", back_populates="team", cascade="all, delete-orphan")
    media_assets: Mapped[list["MediaAsset"]] = relationship("MediaAsset", back_populates="team", cascade="all, delete-orphan")
    prompt_templates: Mapped[list["PromptTemplate"]] = relationship("PromptTemplate", back_populates="team", cascade="all, delete-orphan")
    generated_workflows: Mapped[list["GeneratedWorkflow"]] = relationship("GeneratedWorkflow", back_populates="team", cascade="all, delete-orphan")


class TeamMember(Base):
    __tablename__ = "team_members"
    __table_args__ = (UniqueConstraint("team_id", "user_id", name="uq_team_member"),)

    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[UserRole] = mapped_column(SQLEnum(UserRole, values_callable=lambda obj: [e.value for e in obj]), default=UserRole.EDITOR, nullable=False)

    team: Mapped["Team"] = relationship("Team", back_populates="members")
    user: Mapped["User"] = relationship("User", back_populates="team_memberships")
