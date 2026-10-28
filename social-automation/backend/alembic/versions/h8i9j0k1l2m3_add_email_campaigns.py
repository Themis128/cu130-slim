"""add email campaign tables

Marketing/survey campaign sends to EspoCRM contacts: campaigns, per-recipient
deliveries (token doubles as the unsubscribe key), and the GDPR suppression
(do-not-contact) list checked before every send.

Revision ID: h8i9j0k1l2m3
Revises: g7f8e9d0c1b2
Create Date: 2026-10-16 12:00:00.000000

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "h8i9j0k1l2m3"
down_revision = "g7f8e9d0c1b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_campaigns",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("marketing", "survey", name="campaignkind"),
            nullable=False,
            server_default="marketing",
        ),
        sa.Column("subject", sa.String(500), nullable=False),
        sa.Column("text_body", sa.Text(), nullable=False),
        sa.Column("html_body", sa.Text(), nullable=True),
        sa.Column("from_name", sa.String(120), nullable=False, server_default="Cloudless"),
        sa.Column("reply_to", sa.String(320), nullable=True),
        sa.Column(
            "status",
            sa.Enum("draft", "sending", "sent", "partial", name="campaignstatus"),
            nullable=False,
            server_default="draft",
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("espocrm_target_list_id", sa.String(40), nullable=True),
        sa.Column("survey_url", sa.String(500), nullable=True),
        sa.Column("meta_data", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_email_campaigns_team_created", "email_campaigns", ["team_id", "created_at"])
    op.create_index("ix_email_campaigns_team_status", "email_campaigns", ["team_id", "status"])
    op.create_index("ix_email_campaigns_team_id", "email_campaigns", ["team_id"])

    op.create_table(
        "email_recipients",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "campaign_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("email_campaigns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "lead_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("leads.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "sent",
                "failed",
                "skipped_suppressed",
                "unsubscribed",
                name="recipientstatus",
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("token", sa.String(64), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("campaign_id", "email", name="uq_email_recipients_campaign_email"),
    )
    op.create_index("ix_email_recipients_campaign", "email_recipients", ["campaign_id"])
    op.create_index("ix_email_recipients_status", "email_recipients", ["campaign_id", "status"])
    op.create_index("ix_email_recipients_team_email", "email_recipients", ["team_id", "email"])
    op.create_index("ix_email_recipients_team_id", "email_recipients", ["team_id"])
    op.create_index("ix_email_recipients_token", "email_recipients", ["token"], unique=True)

    op.create_table(
        "email_suppressions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("reason", sa.String(40), nullable=False, server_default="unsubscribe"),
        sa.Column("source", sa.String(120), nullable=False, server_default="campaign"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_email_suppressions_team_email",
        "email_suppressions",
        ["team_id", "email"],
        unique=True,
    )
    op.create_index("ix_email_suppressions_team_id", "email_suppressions", ["team_id"])

    op.create_table(
        "survey_responses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "campaign_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("email_campaigns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "recipient_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("email_recipients.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "lead_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("leads.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("answers", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("espocrm_noted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("recipient_id", name="uq_survey_responses_recipient"),
    )
    op.create_index("ix_survey_responses_campaign", "survey_responses", ["campaign_id"])
    op.create_index("ix_survey_responses_team", "survey_responses", ["team_id", "submitted_at"])
    op.create_index("ix_survey_responses_team_id", "survey_responses", ["team_id"])


def downgrade() -> None:
    op.drop_table("survey_responses")
    op.drop_table("email_suppressions")
    op.drop_table("email_recipients")
    op.drop_table("email_campaigns")
    op.execute("DROP TYPE IF EXISTS recipientstatus")
    op.execute("DROP TYPE IF EXISTS campaignstatus")
    op.execute("DROP TYPE IF EXISTS campaignkind")
