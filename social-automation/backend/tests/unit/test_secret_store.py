"""Coverage for app/services/secret_store.py."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.secret_store as SS
from app.services.secret_store import SecretStore


def _d1(enabled=True, healthy=True):
    return SimpleNamespace(
        enabled=enabled,
        health=AsyncMock(return_value=healthy),
        query_one=AsyncMock(return_value=None),
        query_all=AsyncMock(return_value=[]),
        execute=AsyncMock(return_value=None),
        table_exists=AsyncMock(return_value=True),
        _serialize_param=lambda v: v,
    )


class _Session:
    def __init__(self, results=None):
        self._q = list(results or [])
        self.added = []
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def execute(self, stmt, *a):
        return self._q.pop(0) if self._q else SimpleNamespace(
            scalar_one_or_none=lambda: None, all=lambda: [], rowcount=0)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


def _session_maker(session):
    return lambda: session


class TestD1Layer:
    @pytest.mark.asyncio
    async def test_get_disabled_unhealthy(self, monkeypatch):
        s = SecretStore()
        monkeypatch.setattr(SS, "d1_client", _d1(enabled=False))
        assert await s._get_from_d1("K") is None
        monkeypatch.setattr(SS, "d1_client", _d1(healthy=False))
        assert await s._get_from_d1("K") is None

    @pytest.mark.asyncio
    async def test_get_found_and_error(self, monkeypatch):
        d1 = _d1()
        d1.query_one = AsyncMock(return_value={"value": "v1"})
        monkeypatch.setattr(SS, "d1_client", d1)
        assert await SecretStore()._get_from_d1("K") == "v1"
        d1.query_one = AsyncMock(return_value=None)
        assert await SecretStore()._get_from_d1("K") is None
        d1.query_one = AsyncMock(side_effect=RuntimeError())
        assert await SecretStore()._get_from_d1("K") is None

    @pytest.mark.asyncio
    async def test_set_insert_and_update(self, monkeypatch):
        d1 = _d1()
        monkeypatch.setattr(SS, "d1_client", d1)
        s = SecretStore()
        # insert path (no existing)
        assert await s._set_in_d1("K", "v") is True
        assert "INSERT" in d1.execute.await_args_list[-1][0][0]
        # update path (existing row)
        d1.query_one = AsyncMock(return_value={"key": "K"})
        assert await s._set_in_d1("K", "v2") is True
        assert "UPDATE" in d1.execute.await_args_list[-1][0][0]

    @pytest.mark.asyncio
    async def test_set_disabled_and_error(self, monkeypatch):
        s = SecretStore()
        monkeypatch.setattr(SS, "d1_client", _d1(enabled=False))
        assert await s._set_in_d1("K", "v") is False
        d1 = _d1()
        d1.query_one = AsyncMock(side_effect=RuntimeError())
        monkeypatch.setattr(SS, "d1_client", d1)
        assert await s._set_in_d1("K", "v") is False

    @pytest.mark.asyncio
    async def test_ensure_table(self, monkeypatch):
        d1 = _d1()
        d1.table_exists = AsyncMock(return_value=False)
        monkeypatch.setattr(SS, "d1_client", d1)
        await SecretStore()._ensure_d1_table()
        assert "CREATE TABLE" in d1.execute.await_args[0][0]
        # already exists → no create
        d1.execute.reset_mock()
        d1.table_exists = AsyncMock(return_value=True)
        await SecretStore()._ensure_d1_table()
        d1.execute.assert_not_awaited()
        # error swallowed
        d1.table_exists = AsyncMock(side_effect=RuntimeError())
        await SecretStore()._ensure_d1_table()

    @pytest.mark.asyncio
    async def test_delete_d1(self, monkeypatch):
        s = SecretStore()
        monkeypatch.setattr(SS, "d1_client", _d1(enabled=False))
        assert await s._delete_from_d1("K") is False
        d1 = _d1()
        monkeypatch.setattr(SS, "d1_client", d1)
        assert await s._delete_from_d1("K") is True
        assert "DELETE" in d1.execute.await_args[0][0]
        d1.execute = AsyncMock(side_effect=RuntimeError())
        assert await s._delete_from_d1("K") is False


class TestPostgresLayer:
    @pytest.mark.asyncio
    async def test_get(self, monkeypatch):
        s = SecretStore()
        row = SimpleNamespace(value="pgv")
        res = SimpleNamespace(scalar_one_or_none=lambda: row)
        monkeypatch.setattr(SS, "async_session_maker",
                            _session_maker(_Session([res])))
        assert await s._get_from_postgres("K") == "pgv"
        # no row
        res2 = SimpleNamespace(scalar_one_or_none=lambda: None)
        monkeypatch.setattr(SS, "async_session_maker",
                            _session_maker(_Session([res2])))
        assert await s._get_from_postgres("K") is None
        # error
        bad = SimpleNamespace()
        bad.execute = AsyncMock(side_effect=RuntimeError())
        monkeypatch.setattr(SS, "async_session_maker", _session_maker(bad))
        assert await s._get_from_postgres("K") is None

    @pytest.mark.asyncio
    async def test_set_update_existing(self, monkeypatch):
        row = SimpleNamespace(value="old", description="d", updated_at=None)
        res = SimpleNamespace(scalar_one_or_none=lambda: row)
        sess = _Session([res])
        monkeypatch.setattr(SS, "async_session_maker", _session_maker(sess))
        s = SecretStore()
        assert await s._set_in_postgres("K", "new", "desc2") is True
        assert row.value == "new"
        assert row.description == "desc2"
        assert row.updated_at is not None
        assert sess.commits == 1

    @pytest.mark.asyncio
    async def test_set_insert_and_error(self, monkeypatch):
        res = SimpleNamespace(scalar_one_or_none=lambda: None)
        sess = _Session([res])
        monkeypatch.setattr(SS, "async_session_maker", _session_maker(sess))
        s = SecretStore()
        assert await s._set_in_postgres("K", "v") is True
        assert len(sess.added) == 1
        assert sess.added[0].key == "K"
        # error → False
        bad = SimpleNamespace()
        bad.execute = AsyncMock(side_effect=RuntimeError())
        monkeypatch.setattr(SS, "async_session_maker", _session_maker(bad))
        assert await s._set_in_postgres("K", "v") is False

    @pytest.mark.asyncio
    async def test_delete(self, monkeypatch):
        s = SecretStore()
        res = SimpleNamespace(rowcount=1)
        monkeypatch.setattr(SS, "async_session_maker",
                            _session_maker(_Session([res])))
        assert await s._delete_from_postgres("K") is True
        res0 = SimpleNamespace(rowcount=0)
        monkeypatch.setattr(SS, "async_session_maker",
                            _session_maker(_Session([res0])))
        assert await s._delete_from_postgres("K") is False
        bad = SimpleNamespace()
        bad.execute = AsyncMock(side_effect=RuntimeError())
        monkeypatch.setattr(SS, "async_session_maker", _session_maker(bad))
        assert await s._delete_from_postgres("K") is False


class TestEnvLayer:
    def test_env_and_file(self, monkeypatch, tmp_path):
        s = SecretStore()
        monkeypatch.setenv("MY_KEY", "envval")
        assert s._get_from_env("MY_KEY") == "envval"
        monkeypatch.delenv("MY_KEY")
        envf = tmp_path / ".env"
        envf.write_text("MY_KEY=fileval\nOTHER=x\n")
        monkeypatch.setattr(SS, "_ENV_FILE", envf)
        assert s._get_from_env("MY_KEY") == "fileval"
        monkeypatch.setattr(SS, "_ENV_FILE", tmp_path / "none.env")
        assert s._get_from_env("MY_KEY") is None


class TestGetSetDelete:
    @pytest.mark.asyncio
    async def test_get_chain(self, monkeypatch):
        s = SecretStore()
        monkeypatch.setattr(SS, "d1_client", _d1(enabled=False))
        # d1 miss → pg hit
        monkeypatch.setattr(s, "_get_from_postgres",
                            AsyncMock(return_value="pg"))
        assert await s.get("K") == "pg"
        # pg miss → env hit
        monkeypatch.setattr(s, "_get_from_postgres",
                            AsyncMock(return_value=None))
        monkeypatch.setattr(s, "_get_from_env", lambda k: "env")
        assert await s.get("K") == "env"
        # all miss → default
        monkeypatch.setattr(s, "_get_from_env", lambda k: None)
        assert await s.get("K", "def") == "def"
        # d1 hit wins
        monkeypatch.setattr(s, "_get_from_d1", AsyncMock(return_value="d1v"))
        assert await s.get("K") == "d1v"

    @pytest.mark.asyncio
    async def test_set(self, monkeypatch):
        s = SecretStore()
        monkeypatch.setattr(s, "_set_in_d1", AsyncMock(return_value=True))
        monkeypatch.setattr(s, "_set_in_postgres", AsyncMock(return_value=True))
        monkeypatch.setattr(s, "_sync_env_file", AsyncMock(return_value=False))
        out = await s.set("K", "v", description="d")
        assert out == {"d1": True, "postgres": True, "env": False}
        # sync_env=False skips env write
        monkeypatch.setattr(s, "_sync_env_file", AsyncMock(return_value=True))
        out = await s.set("K", "v", sync_env=False)
        assert out["env"] is False

    @pytest.mark.asyncio
    async def test_delete(self, monkeypatch):
        s = SecretStore()
        monkeypatch.setattr(s, "_delete_from_d1", AsyncMock(return_value=True))
        monkeypatch.setattr(s, "_delete_from_postgres",
                            AsyncMock(return_value=True))
        monkeypatch.setattr(s, "_delete_from_env_file", lambda k: True)
        out = await s.delete("K")
        assert out == {"d1": True, "postgres": True, "env": True}


class TestEnvFileSync:
    @pytest.mark.asyncio
    async def test_no_file_and_readonly(self, monkeypatch, tmp_path):
        s = SecretStore()
        monkeypatch.setattr(SS, "_ENV_FILE", tmp_path / "nope.env")
        assert await s._sync_env_file("K", "v") is False
        envf = tmp_path / ".env"
        envf.write_text("A=1\n")
        monkeypatch.setattr(SS, "_ENV_FILE", envf)
        monkeypatch.setattr(SS.os, "access", lambda p, m: False)
        assert await s._sync_env_file("K", "v") is False

    @pytest.mark.asyncio
    async def test_update_and_append(self, monkeypatch, tmp_path):
        envf = tmp_path / ".env"
        envf.write_text("K=old\nA=1\n")
        monkeypatch.setattr(SS, "_ENV_FILE", envf)
        monkeypatch.setattr(SS.os, "access", lambda p, m: True)
        s = SecretStore()
        assert await s._sync_env_file("K", "new") is True
        assert 'K="new"' in envf.read_text()
        # append new key
        assert await s._sync_env_file("Z", "z") is True
        assert 'Z="z"' in envf.read_text()

    @pytest.mark.asyncio
    async def test_sync_error(self, monkeypatch, tmp_path):
        envf = tmp_path / ".env"
        envf.write_text("A=1\n")
        monkeypatch.setattr(SS, "_ENV_FILE", envf)
        monkeypatch.setattr(SS.os, "access", lambda p, m: True)
        monkeypatch.setattr(type(envf), "read_text",
                            lambda self: (_ for _ in ()).throw(OSError()))
        assert await SecretStore()._sync_env_file("K", "v") is False

    def test_delete_env_file(self, monkeypatch, tmp_path):
        s = SecretStore()
        monkeypatch.setattr(SS, "_ENV_FILE", tmp_path / "nope")
        assert s._delete_from_env_file("K") is False
        envf = tmp_path / ".env"
        envf.write_text("K=v\nA=1\n")
        monkeypatch.setattr(SS, "_ENV_FILE", envf)
        monkeypatch.setattr(SS.os, "access", lambda p, m: False)
        assert s._delete_from_env_file("K") is False
        monkeypatch.setattr(SS.os, "access", lambda p, m: True)
        assert s._delete_from_env_file("K") is True
        assert "K=" not in envf.read_text()
        # key not present → False
        assert s._delete_from_env_file("MISSING") is False
        # read error → False
        monkeypatch.setattr(type(envf), "read_text",
                            lambda self: (_ for _ in ()).throw(OSError()))
        assert s._delete_from_env_file("K") is False


class TestListKeys:
    @pytest.mark.asyncio
    async def test_aggregates(self, monkeypatch, tmp_path):
        s = SecretStore()
        d1 = _d1()
        d1.query_all = AsyncMock(return_value=[
            {"key": "D1_KEY"}, {"key": "SHARED"}])
        monkeypatch.setattr(SS, "d1_client", d1)
        row = SimpleNamespace(key="PG_KEY")
        res = SimpleNamespace(all=lambda: [row])
        monkeypatch.setattr(SS, "async_session_maker",
                            _session_maker(_Session([res])))
        envf = tmp_path / ".env"
        envf.write_text("ENV_SECRET=x\nPLAIN_VAR=y\nIG_TOKEN=z\n")
        monkeypatch.setattr(SS, "_ENV_FILE", envf)
        out = await s.list_keys()
        keys = {e["key"] for e in out}
        assert {"D1_KEY", "SHARED", "PG_KEY", "ENV_SECRET", "IG_TOKEN"} <= keys
        assert "PLAIN_VAR" not in keys
        assert all(e["value"] == "***" for e in out)

    @pytest.mark.asyncio
    async def test_errors_swallowed(self, monkeypatch, tmp_path):
        s = SecretStore()
        d1 = _d1()
        d1.query_all = AsyncMock(side_effect=RuntimeError())
        monkeypatch.setattr(SS, "d1_client", d1)
        bad = SimpleNamespace()
        bad.execute = AsyncMock(side_effect=RuntimeError())
        monkeypatch.setattr(SS, "async_session_maker", _session_maker(bad))
        monkeypatch.setattr(SS, "_ENV_FILE", tmp_path / "nope")
        assert await s.list_keys() == []

    @pytest.mark.asyncio
    async def test_profile_credentials(self, monkeypatch):
        s = SecretStore()
        vals = {"INSTAGRAM_USERNAME": "u", "INSTAGRAM_PASSWORD": "p"}
        monkeypatch.setattr(s, "get", AsyncMock(
            side_effect=lambda k, d=None: vals.get(k)))
        out = await s.get_profile_credentials()
        assert out == vals
