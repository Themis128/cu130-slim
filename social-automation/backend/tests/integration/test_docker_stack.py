"""Docker container integration tests.

Verifies that all containers in the cu130-slim stack are running, healthy,
and can communicate with each other. These tests require the full Docker
Compose stack to be up (``docker compose up -d``).

Run with:
    pytest tests/integration/test_docker_stack.py -v -m integration
"""

from __future__ import annotations

import json
import os
import subprocess

import httpx
import pytest
import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.integration

COMPOSE_DIR = os.environ.get("COMPOSE_DIR", "/home/tbaltzakis/cu130-slim")
API_URL = os.environ.get("API_URL", "http://localhost:8083")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:8082")
N8N_URL = os.environ.get("N8N_URL", "http://localhost:5678")


def _docker_compose_ps() -> list[dict]:
    result = subprocess.run(
        ["docker", "compose", "ps", "--format", "json"],
        capture_output=True,
        text=True,
        cwd=COMPOSE_DIR,
        timeout=30,
    )
    containers = []
    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if line:
            containers.append(json.loads(line))
    return containers


def _get_container_state(name: str) -> str | None:
    containers = _docker_compose_ps()
    for c in containers:
        service = c.get("Service", c.get("name", ""))
        if name in service:
            return c.get("State", c.get("status", ""))
    return None


class TestContainersRunning:
    REQUIRED_CONTAINERS = [
        "social-api",
        "social-worker",
        "social-frontend",
        "social-postgres",
        "redis",
        "n8n",
        "chroma",
        "minio",
        "ollama",
        "languagetool",
    ]

    @pytest.mark.parametrize("container_name", REQUIRED_CONTAINERS)
    def test_container_running(self, container_name: str):
        state = _get_container_state(container_name)
        assert state is not None, f"Container '{container_name}' not found"
        assert state == "running", f"Container '{container_name}' is '{state}', expected 'running'"


class TestSocialAPI:
    @pytest.mark.asyncio
    async def test_health_endpoint(self):
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{API_URL}/health", timeout=10)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "service" in data
        assert "version" in data

    @pytest.mark.asyncio
    async def test_openapi_schema_accessible(self):
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{API_URL}/openapi.json", timeout=10)
        assert resp.status_code == 200
        schema = resp.json()
        assert "paths" in schema
        assert len(schema["paths"]) > 10

    @pytest.mark.asyncio
    async def test_cf_db_health(self):
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{API_URL}/api/v1/cf-db/health", timeout=15)
        assert resp.status_code == 200
        data = resp.json()
        assert "d1" in data
        assert "kv" in data
        assert "vectorize" in data
        assert "router" in data


class TestSocialWorker:
    def test_celery_ping(self):
        result = subprocess.run(
            [
                "docker", "compose", "exec", "-T", "social-worker",
                "celery", "-A", "app.worker.celery_app", "inspect", "ping",
            ],
            capture_output=True,
            text=True,
            cwd=COMPOSE_DIR,
            timeout=30,
        )
        assert result.returncode == 0, f"Celery ping failed: {result.stderr}"
        assert "pong" in result.stdout.lower(), f"Expected 'pong': {result.stdout}"


class TestPostgres:
    @pytest.mark.asyncio
    async def test_database_connection(self):
        db_url = os.environ.get(
            "DATABASE_URL",
            "postgresql+asyncpg://postgres:postgres_password@localhost:5433/social",
        )
        engine = create_async_engine(db_url)
        try:
            async with engine.connect() as conn:
                result = await conn.execute(text("SELECT 1 as test"))
                row = result.fetchone()
                assert row is not None
                assert row[0] == 1
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_tables_exist(self):
        db_url = os.environ.get(
            "DATABASE_URL",
            "postgresql+asyncpg://postgres:postgres_password@localhost:5433/social",
        )
        engine = create_async_engine(db_url)
        try:
            async with engine.connect() as conn:
                result = await conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public' ORDER BY table_name"
                    )
                )
                tables = [row[0] for row in result.fetchall()]
                required = ["users", "teams", "team_members", "social_accounts", "posts"]
                for table in required:
                    assert table in tables, f"Table '{table}' not found. Found: {tables}"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_alembic_version_head(self):
        db_url = os.environ.get(
            "DATABASE_URL",
            "postgresql+asyncpg://postgres:postgres_password@localhost:5433/social",
        )
        engine = create_async_engine(db_url)
        try:
            async with engine.connect() as conn:
                result = await conn.execute(text("SELECT version_num FROM alembic_version"))
                version = result.fetchone()
                assert version is not None, "No alembic version found"
                assert len(version[0]) > 0
        finally:
            await engine.dispose()


class TestRedis:
    @pytest.mark.asyncio
    async def test_redis_connection(self):
        redis_url = os.environ.get("REDIS_URL", "redis://:redis_password@localhost:6379/0")
        r = aioredis.from_url(redis_url)
        try:
            pong = await r.ping()
            assert pong is True
        finally:
            await r.aclose()

    @pytest.mark.asyncio
    async def test_redis_set_get(self):
        redis_url = os.environ.get("REDIS_URL", "redis://:redis_password@localhost:6379/0")
        r = aioredis.from_url(redis_url)
        try:
            await r.set("test_key", "test_value", ex=10)
            value = await r.get("test_key")
            assert value == b"test_value"
            await r.delete("test_key")
        finally:
            await r.aclose()


class TestN8N:
    @pytest.mark.asyncio
    async def test_n8n_web_ui_accessible(self):
        async with httpx.AsyncClient() as client:
            resp = await client.get(N8N_URL, timeout=15, follow_redirects=True)
        assert resp.status_code in (200, 302, 307)

    @pytest.mark.asyncio
    async def test_n8n_api_health(self):
        n8n_api_key = os.environ.get("N8N_API_KEY", "")
        if not n8n_api_key:
            pytest.skip("N8N_API_KEY not set")
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{N8N_URL}/api/v1/workflows",
                headers={"X-N8N-API-KEY": n8n_api_key},
                timeout=15,
            )
        assert resp.status_code in (200, 401, 403)


class TestFrontend:
    @pytest.mark.asyncio
    async def test_frontend_accessible(self):
        async with httpx.AsyncClient() as client:
            resp = await client.get(FRONTEND_URL, timeout=15, follow_redirects=True)
        assert resp.status_code == 200
        assert "html" in resp.headers.get("content-type", "").lower()


class TestChroma:
    @pytest.mark.asyncio
    async def test_chroma_heartbeat(self):
        chroma_url = os.environ.get("CHROMA_URL", "http://localhost:8001")
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{chroma_url}/api/v1/heartbeat", timeout=10)
        assert resp.status_code == 200
        data = resp.json()
        assert "nanosecond heartbeat" in data


class TestMinIO:
    @pytest.mark.asyncio
    async def test_minio_api_accessible(self):
        minio_api = os.environ.get("MINIO_API_URL", "http://localhost:9000")
        async with httpx.AsyncClient() as client:
            resp = await client.get(minio_api, timeout=10)
        assert resp.status_code in (400, 403)


class TestLanguageTool:
    @pytest.mark.asyncio
    async def test_languagetool_accessible(self):
        lt_url = os.environ.get("LANGUAGETOOL_URL", "http://localhost:8010")
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{lt_url}/v2/languages", timeout=10)
        assert resp.status_code == 200


class TestComposeConfig:
    def test_compose_config_valid(self):
        result = subprocess.run(
            ["docker", "compose", "config", "--quiet"],
            capture_output=True,
            text=True,
            cwd=COMPOSE_DIR,
            timeout=30,
        )
        assert result.returncode == 0, f"docker compose config failed: {result.stderr}"


class TestInterServiceCommunication:
    @pytest.mark.asyncio
    async def test_api_can_reach_database(self):
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{API_URL}/health", timeout=10)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    @pytest.mark.asyncio
    async def test_api_can_reach_redis(self):
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{API_URL}/api/v1/workflows", timeout=10)
        assert resp.status_code in (200, 401)
