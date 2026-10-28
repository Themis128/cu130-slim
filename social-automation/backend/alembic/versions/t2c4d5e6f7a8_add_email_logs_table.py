"""Add email_logs table for transactional email delivery audit.

Revision ID: t2c4d5e6f7a8
Revises: s1b3c4d5e6f7
Create Date: 2026-09-07 22:51:00.000000

Note: This table was created out-of-band before the migration existed.
The upgrade() is idempotent — it checks information_schema before creating.
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers, used by Alembic.
revision = "t2c4d5e6f7a8"
down_revision = "s1b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()
    if "email_logs" not in existing_tables:
        op.create_table(
            "email_logs",
            sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
            sa.Column("user_id", UUID(as_uuid=True), nullable=True, index=True),
            sa.Column("team_id", UUID(as_uuid=True), nullable=True, index=True),
            sa.Column("recipient", sa.String(length=255), nullable=False, index=True),
            sa.Column("subject", sa.String(length=500), nullable=False),
            sa.Column("template", sa.String(length=50), nullable=False, index=True),
            sa.Column("status", sa.String(length=20), server_default="sent", nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
        )


def downgrade() -> None:
    op.drop_table("email_logs")
