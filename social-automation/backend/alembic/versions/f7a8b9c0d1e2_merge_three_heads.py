"""Merge all divergent alembic heads into one.

Concurrent PRs + migrations that existed only in a build image (never
committed) left the chain with five leaf heads; fresh containers
crash-looped on ``alembic upgrade head`` ("Multiple head revisions").

Merges: b2c3d4e5f6a7, d3e5f7a9b1c4, e7f8a9b1c2d3 (master heads) plus
e4f6a8b0c1d3, f7g8h9i0j1k2 (image-only migrations rescued alongside).
No schema change — lineage-only merge.
"""
from __future__ import annotations

revision = "f7a8b9c0d1e2"
down_revision = (
    "b2c3d4e5f6a7",
    "d3e5f7a9b1c4",
    "e7f8a9b1c2d3",
    "e4f6a8b0c1d3",
    "f7g8h9i0j1k2",
)
branch_labels = None
depends_on = None


def upgrade() -> None:  # merge-only
    pass


def downgrade() -> None:
    pass
