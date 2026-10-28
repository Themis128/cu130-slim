"""add linkedin_ads to leadsource enum"""
from alembic import op

revision = 'e4f6a8b0c1d3'
down_revision = 'd3e5f7a9b1c4'
branch_labels = None
depends_on = None

def upgrade():
    op.execute("ALTER TYPE leadsource ADD VALUE IF NOT EXISTS 'linkedin_ads'")

def downgrade():
    pass  # enum values cannot be removed
