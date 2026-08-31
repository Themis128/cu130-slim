"""Cloudflare D1 database client.

Provides a Python interface to Cloudflare D1 via the REST API.
Used as the primary database for social-api, with local PostgreSQL as failover.

Free tier: 5M reads/day, 100K writes/day, 5GB storage.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class D1Client:
    """Async client for Cloudflare D1 REST API."""

    def __init__(self) -> None:
        self.account_id = (settings.CLOUDFLARE_ACCOUNT_ID or "").strip()
        self.api_token = (settings.CLOUDFLARE_API_TOKEN or "").strip()
        self.db_id = (getattr(settings, "D1_SOCIAL_AUTOMATION_ID", "") or "").strip()
        self._base_url: str | None = None
        self._enabled: bool | None = None

    @property
    def enabled(self) -> bool:
        if self._enabled is None:
            self._enabled = all([self.account_id, self.api_token, self.db_id])
        return self._enabled

    @property
    def base_url(self) -> str:
        if self._base_url is None:
            self._base_url = (
                f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}"
                f"/d1/database/{self.db_id}/query"
            )
        return self._base_url

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }

    async def execute(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        """Execute a SQL statement and return rows.

        Args:
            sql: SQL statement (SQLite syntax). Use ? for parameters.
            params: List of parameter values.

        Returns:
            List of result row dicts. For INSERT/UPDATE/DELETE, returns metadata.
        """
        if not self.enabled:
            raise RuntimeError("D1 client is not configured (missing account_id, token, or db_id)")

        body: dict[str, Any] = {"sql": sql}
        if params:
            body["params"] = [self._serialize_param(p) for p in params]

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(self.base_url, json=body, headers=self._headers())
            resp.raise_for_status()
            data = resp.json()

        if not data.get("success"):
            errors = data.get("errors", [])
            msg = errors[0].get("message", "Unknown D1 error") if errors else "Unknown D1 error"
            logger.error("D1 query failed: %s | SQL: %s", msg, sql[:200])
            raise RuntimeError(f"D1 error: {msg}")

        results = data.get("result", [])
        if not results:
            return []

        # D1 returns [{"results": [...], "success": true, "meta": {...}}]
        return results[0].get("results", [])

    async def execute_many(self, sql: str, params_list: list[list[Any]]) -> int:
        """Execute a SQL statement with multiple parameter sets.

        Returns the number of affected rows (approximate).
        """
        if not self.enabled:
            raise RuntimeError("D1 client is not configured")

        count = 0
        for params in params_list:
            try:
                await self.execute(sql, params)
                count += 1
            except Exception as exc:
                logger.error("D1 execute_many row failed: %s", exc)
        return count

    async def query_one(self, sql: str, params: list[Any] | None = None) -> dict[str, Any] | None:
        """Execute and return a single row, or None."""
        rows = await self.execute(sql, params)
        return rows[0] if rows else None

    async def query_all(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        """Execute and return all rows."""
        return await self.execute(sql, params)

    async def insert(self, table: str, data: dict[str, Any]) -> dict[str, Any] | None:
        """Insert a row into a table.

        Args:
            table: Table name.
            data: Column-value dict.

        Returns:
            The inserted row, or None on failure.
        """
        columns = list(data.keys())
        placeholders = ", ".join(["?"] * len(columns))
        col_str = ", ".join(columns)
        sql = f"INSERT INTO {table} ({col_str}) VALUES ({placeholders})"
        await self.execute(sql, list(data.values()))
        return data

    async def update(
        self, table: str, data: dict[str, Any], where: str, where_params: list[Any] | None = None
    ) -> int:
        """Update rows in a table.

        Args:
            table: Table name.
            data: Column-value dict to set.
            where: WHERE clause (without "WHERE" keyword).
            where_params: Parameters for the WHERE clause.

        Returns:
            Number of rows affected (approximate).
        """
        set_clause = ", ".join(f"{k} = ?" for k in data)
        sql = f"UPDATE {table} SET {set_clause} WHERE {where}"
        params = list(data.values()) + (where_params or [])
        await self.execute(sql, params)
        return 1  # D1 doesn't reliably return rowcount

    async def delete(
        self, table: str, where: str, where_params: list[Any] | None = None
    ) -> int:
        """Delete rows from a table.

        Returns:
            Number of rows deleted (approximate).
        """
        sql = f"DELETE FROM {table} WHERE {where}"
        await self.execute(sql, where_params or [])
        return 1

    async def count(self, table: str, where: str | None = None, where_params: list[Any] | None = None) -> int:
        """Count rows in a table."""
        sql = f"SELECT count(*) as cnt FROM {table}"
        if where:
            sql += f" WHERE {where}"
        row = await self.query_one(sql, where_params)
        return int(row["cnt"]) if row else 0

    async def table_exists(self, table: str) -> bool:
        """Check if a table exists in the D1 database."""
        row = await self.query_one(
            "SELECT name FROM sqlite_master WHERE type = ? AND name = ?",
            ["table", table],
        )
        return row is not None

    async def list_tables(self) -> list[str]:
        """List all tables in the D1 database."""
        rows = await self.execute(
            "SELECT name FROM sqlite_master WHERE type = ? ORDER BY name",
            ["table"],
        )
        return [r["name"] for r in rows if not r["name"].startswith("_cf")]

    async def health(self) -> bool:
        """Check if D1 is reachable and configured."""
        if not self.enabled:
            return False
        try:
            await self.execute("SELECT 1 as ok")
            return True
        except Exception:
            return False

    @staticmethod
    def _serialize_param(value: Any) -> Any:
        """Serialize a Python value for D1 JSON transport."""
        if isinstance(value, (dict, list)):
            return json.dumps(value)
        if isinstance(value, bool):
            return 1 if value else 0
        if value is None:
            return None
        return value


# Singleton instance
d1_client = D1Client()
