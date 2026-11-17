"""encrypt social_secrets values at rest (Postgres failover copy)

The Postgres ``social_secrets`` table — the local failover for the D1
secret store — held plaintext values while OAuth tokens elsewhere are
Fernet-encrypted. This data migration rewrites each row through the same
``enc:v1:`` envelope that ``secret_store`` now writes, and decrypts them
back on downgrade. Rows are skipped when ENCRYPTION_KEY is not configured
so environments without the key are unaffected.

Revision ID: x9y0z1a2b3c4
Revises: nlw001q2e3u4
Create Date: 2026-11-17 05:00:00.000000

"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "x9y0z1a2b3c4"
down_revision = "nlw001q2e3u4"
branch_labels = None
depends_on = None


def _rewrite(encrypt: bool) -> None:
    try:
        from app.core.security import (
            SECRET_ENC_PREFIX,
            decrypt_secret,
            encrypt_secret,
            get_fernet,
        )

        get_fernet()
    except Exception:
        return  # ENCRYPTION_KEY not configured — nothing we can safely do

    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, value FROM social_secrets")).fetchall()
    stmt = sa.text("UPDATE social_secrets SET value = :v WHERE id = :i")
    for rid, value in rows:
        if not value:
            continue
        try:
            if encrypt and not value.startswith(SECRET_ENC_PREFIX):
                conn.execute(stmt, {"v": encrypt_secret(value), "i": rid})
            elif not encrypt and value.startswith(SECRET_ENC_PREFIX):
                conn.execute(stmt, {"v": decrypt_secret(value), "i": rid})
        except Exception:
            continue


def upgrade() -> None:
    _rewrite(encrypt=True)


def downgrade() -> None:
    _rewrite(encrypt=False)
