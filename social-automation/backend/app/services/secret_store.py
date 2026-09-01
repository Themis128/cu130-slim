"""Cloudflare-first secret store with local PostgreSQL and .env failover.

Primary: Cloudflare D1 ``social_secrets`` table.
Failover 1: Local PostgreSQL ``social_secrets`` table (SQLAlchemy model).
Failover 2: Local ``.env`` file / process environment.

Service credentials such as Instagram username/password, Twitter API tokens,
TikTok private API key, and Facebook/LinkedIn browser credentials are written
here so the SocialAuto app can log into all services.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from sqlalchemy import select

from app.db.session import async_session_maker
from app.models.social_secret import SocialSecret
from app.services.d1_client import d1_client

logger = logging.getLogger(__name__)

_ENV_FILE = Path("/app/.env")


class SecretStore:
    """Read and write secrets with Cloudflare D1 primary and local failover."""

    # Secrets the store will fall back to .env for if not found in D1/Postgres.
    # These keys are profile / login related.
    PROFILE_SECRET_KEYS: frozenset[str] = frozenset({
        "INSTAGRAM_USERNAME",
        "INSTAGRAM_PASSWORD",
        "TWITTER_API_KEY",
        "TWITTER_API_SECRET",
        "TWITTER_ACCESS_TOKEN",
        "TWITTER_ACCESS_TOKEN_SECRET",
        "TIKTOK_PRIVATE_API_KEY",
        "FACEBOOK_USERNAME",
        "FACEBOOK_PASSWORD",
        "LINKEDIN_USERNAME",
        "LINKEDIN_PASSWORD",
    })

    def __init__(self) -> None:
        self._d1_table = "social_secrets"

    async def _get_from_d1(self, key: str) -> str | None:
        if not d1_client.enabled or not await d1_client.health():
            return None
        try:
            row = await d1_client.query_one(
                f"SELECT value FROM {self._d1_table} WHERE key = ?",
                [key],
            )
            return row["value"] if row else None
        except Exception as exc:
            logger.warning("D1 secret read failed for %s: %s", key, exc)
            return None

    async def _set_in_d1(self, key: str, value: str) -> bool:
        if not d1_client.enabled or not await d1_client.health():
            return False
        try:
            # Ensure table exists.
            await self._ensure_d1_table()
            existing = await d1_client.query_one(
                f"SELECT key FROM {self._d1_table} WHERE key = ?",
                [key],
            )
            if existing:
                await d1_client.execute(
                    f"UPDATE {self._d1_table} SET value = ?, updated_at = ? WHERE key = ?",
                    [value, d1_client._serialize_param(_now_iso()), key],
                )
            else:
                await d1_client.execute(
                    f"INSERT INTO {self._d1_table} (key, value, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    [key, value, d1_client._serialize_param(_now_iso()), d1_client._serialize_param(_now_iso())],
                )
            return True
        except Exception as exc:
            logger.warning("D1 secret write failed for %s: %s", key, exc)
            return False

    async def _ensure_d1_table(self) -> None:
        try:
            if not await d1_client.table_exists(self._d1_table):
                await d1_client.execute(
                    f"""
                    CREATE TABLE {self._d1_table} (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        key TEXT NOT NULL UNIQUE,
                        value TEXT NOT NULL,
                        description TEXT,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
        except Exception as exc:
            logger.warning("D1 ensure table failed: %s", exc)

    async def _get_from_postgres(self, key: str) -> str | None:
        try:
            async with async_session_maker() as session:
                result = await session.execute(
                    select(SocialSecret).where(SocialSecret.key == key)
                )
                row = result.scalar_one_or_none()
                return row.value if row else None
        except Exception as exc:
            logger.warning("Postgres secret read failed for %s: %s", key, exc)
            return None

    async def _set_in_postgres(self, key: str, value: str, description: str | None = None) -> bool:
        try:
            async with async_session_maker() as session:
                result = await session.execute(
                    select(SocialSecret).where(SocialSecret.key == key)
                )
                row = result.scalar_one_or_none()
                now = _utc_now()
                if row:
                    row.value = value
                    row.description = description or row.description
                    row.updated_at = now
                else:
                    session.add(
                        SocialSecret(
                            key=key,
                            value=value,
                            description=description or "",
                            created_at=now,
                            updated_at=now,
                        )
                    )
                await session.commit()
                return True
        except Exception as exc:
            logger.warning("Postgres secret write failed for %s: %s", key, exc)
            return False

    def _get_from_env(self, key: str) -> str | None:
        """Read from process env or .env file."""
        value = os.environ.get(key)
        if value:
            return value
        if _ENV_FILE.exists():
            env_values = dotenv_values(_ENV_FILE)
            return env_values.get(key)
        return None

    async def get(self, key: str, default: Any = None) -> str | Any:
        """Get a secret value.

        Order: D1 → Postgres → .env / env.
        """
        value = await self._get_from_d1(key)
        if value:
            return value

        value = await self._get_from_postgres(key)
        if value:
            return value

        value = self._get_from_env(key)
        if value:
            return value

        return default

    async def set(
        self,
        key: str,
        value: str,
        description: str | None = None,
        sync_env: bool = True,
    ) -> dict[str, bool]:
        """Set a secret value.

        Writes to D1 (if available) and Postgres. Also attempts to keep the
        local .env file in sync if the file is writable.
        """
        d1_ok = await self._set_in_d1(key, value)
        pg_ok = await self._set_in_postgres(key, value, description)

        env_ok = False
        if sync_env:
            env_ok = await self._sync_env_file(key, value)

        return {"d1": d1_ok, "postgres": pg_ok, "env": env_ok}

    async def _sync_env_file(self, key: str, value: str) -> bool:
        """Update .env file in place if writable (best-effort)."""
        if not _ENV_FILE.exists():
            return False
        try:
            if not os.access(_ENV_FILE, os.W_OK):
                # .env is mounted ro for social-api in Docker.
                return False
            content = _ENV_FILE.read_text()
            lines = content.splitlines()
            updated = False
            new_lines: list[str] = []
            for line in lines:
                if line.startswith(f"{key}="):
                    new_lines.append(f'{key}="{value}"')
                    updated = True
                else:
                    new_lines.append(line)
            if not updated:
                new_lines.append(f'{key}="{value}"')
            _ENV_FILE.write_text("\n".join(new_lines) + "\n")
            return True
        except Exception as exc:
            logger.warning("Could not sync %s to .env: %s", key, exc)
            return False

    async def list_keys(self) -> list[dict[str, Any]]:
        """List all secret keys (values are masked)."""
        keys: set[str] = set()

        # D1
        if d1_client.enabled and await d1_client.health():
            try:
                rows = await d1_client.query_all(f"SELECT key, updated_at FROM {self._d1_table}")
                for row in rows:
                    keys.add(row["key"])
            except Exception as exc:
                logger.warning("D1 list keys failed: %s", exc)

        # Postgres
        try:
            async with async_session_maker() as session:
                result = await session.execute(select(SocialSecret.key, SocialSecret.updated_at))
                for row in result.all():
                    keys.add(row[0])
        except Exception as exc:
            logger.warning("Postgres list keys failed: %s", exc)

        # .env
        if _ENV_FILE.exists():
            env_values = dotenv_values(_ENV_FILE)
            for k in env_values:
                if k in self.PROFILE_SECRET_KEYS or k.endswith("_KEY") or k.endswith("_SECRET") or k.endswith("_TOKEN") or "PASSWORD" in k:
                    keys.add(k)

        return [{"key": k, "value": "***", "source": "ssm"} for k in sorted(keys)]

    async def get_profile_credentials(self) -> dict[str, str]:
        """Return all profile-related credentials as a dict."""
        credentials: dict[str, str] = {}
        for key in self.PROFILE_SECRET_KEYS:
            value = await self.get(key)
            if value:
                credentials[key] = value
        return credentials


def _utc_now():
    from datetime import UTC, datetime

    return datetime.now(UTC)


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


# Singleton
secret_store = SecretStore()
