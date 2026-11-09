"""Unit tests for app/services/mautic_winback.py + its Celery task."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.services import mautic_winback as mw


class FakeRedis:
    """Minimal async Redis covering the surface mautic_winback uses."""

    def __init__(self, store=None):
        self.store = dict(store or {})
        self.sets = []

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value
        self.sets.append((key, value, ex))
        return True

    async def mget(self, keys):
        return [self.store.get(k) for k in keys]

    async def aclose(self):
        return None


def _lead(id_: str = "l1", email: str = "lead@acme.gr", status: str = "New"):
    return {
        "id": id_,
        "emailAddress": email,
        "status": status,
        "source": "Web Site",
        "firstName": "A",
        "lastName": "B",
    }


def _now() -> str:
    return datetime.now(UTC).isoformat()


@pytest.fixture(autouse=True)
def _no_pg(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)


@pytest.fixture
def redis(monkeypatch):
    store = {}
    fake = FakeRedis(store)
    monkeypatch.setattr(mw, "_redis", AsyncMock(return_value=fake))
    return fake


@pytest.fixture
def mautic(monkeypatch):
    get = AsyncMock(return_value={})
    post = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(mw.mautic_client, "mautic_get", get)
    monkeypatch.setattr(mw.mautic_client, "mautic_post", post)
    return get, post


@pytest.fixture
def leads(monkeypatch):
    mock = AsyncMock(return_value=[])
    monkeypatch.setattr(mw.espocrm_client, "list_all", mock)
    return mock


def test_parse_ts():
    assert mw._parse_ts("2026-01-01T00:00:00Z") is not None
    assert mw._parse_ts("2026-01-01T00:00:00") is not None  # naive → UTC
    assert mw._parse_ts("not-a-date") is None
    assert mw._parse_ts("") is None
    assert mw._parse_ts(None) is None


def test_state_key():
    assert mw._state_key("a@b.c") == "wb:seq:a@b.c"


def test_redis_helper(monkeypatch):
    """Real _redis(): env REDIS_URL wins, else settings, else None."""
    import redis.asyncio as aioredis
    from types import SimpleNamespace

    monkeypatch.setattr(aioredis, "from_url", lambda url, decode_responses=True: ("R", url))
    monkeypatch.setattr(mw, "get_settings", lambda: SimpleNamespace(REDIS_URL=""))
    monkeypatch.delenv("REDIS_URL", raising=False)
    assert mw._redis() is None
    monkeypatch.setenv("REDIS_URL", "redis://env:6379/0")
    assert mw._redis() == ("R", "redis://env:6379/0")
    monkeypatch.delenv("REDIS_URL")
    monkeypatch.setattr(mw, "get_settings", lambda: SimpleNamespace(REDIS_URL="redis://settings:6379/1"))
    assert mw._redis() == ("R", "redis://settings:6379/1")


def test_tier_resolution():
    lead = _lead(status="Converted")
    assert mw._tier(lead, "x@y.z", set(), {"l1": "warm"}) == "warm"  # band wins
    assert mw._tier(lead, "hot@sa.io", {"hot@sa.io"}, {}) == "hot"  # socialauto hot
    assert mw._tier(_lead(status="In Process"), "x@y.z", set(), {}) == "hot"
    assert mw._tier(_lead(status="Assigned"), "x@y.z", set(), {}) == "warm"
    assert mw._tier(_lead(status="New"), "x@y.z", set(), {}) == "warm"
    assert mw._tier(_lead(status="Junk"), "x@y.z", set(), {}) == "cold"
    assert mw._tier(_lead(status=""), "x@y.z", set(), {}) == "cold"


@pytest.mark.asyncio
async def test_lifecycle_bands(monkeypatch):
    fake = FakeRedis({
        "espo:leadscore:l1": json.dumps({"band": "COLD"}),
        "espo:leadscore:l2": "{bad json",
        "espo:leadscore:l3": json.dumps({"band": "weird"}),
        "espo:leadscore:l4": json.dumps({"band": "hot"}),
    })
    monkeypatch.setattr(mw, "_redis", AsyncMock(return_value=fake))
    out = await mw._lifecycle_bands(["l1", "l2", "l3", "l4"])
    assert out == {"l1": "cold", "l4": "hot"}
    assert await mw._lifecycle_bands([]) == {}
    monkeypatch.setattr(mw, "_redis", AsyncMock(return_value=None))
    assert await mw._lifecycle_bands(["l1"]) == {}


@pytest.mark.asyncio
async def test_socialauto_hot_emails(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert await mw._socialauto_hot_emails() == set()
    monkeypatch.setenv("DATABASE_URL", "postgresql://bad-host:1/db")
    assert await mw._socialauto_hot_emails() == set()  # connect fails → empty


@pytest.mark.asyncio
async def test_socialauto_hot_emails_pg(monkeypatch):
    """Real query path with a stubbed asyncpg connection."""
    import asyncpg

    class _Conn:
        async def fetch(self, q):
            return [{"email": "A@Acme.gr"}, {"email": None}, {"email": "b@x.io"}]

        async def close(self):
            return None

    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h/d")
    monkeypatch.setattr(asyncpg, "connect", AsyncMock(return_value=_Conn()))
    out = await mw._socialauto_hot_emails()
    assert out == {"a@acme.gr", "b@x.io"}


@pytest.mark.asyncio
async def test_email_ids_cached(redis, mautic):
    get, _ = mautic
    await redis.set("wb:email_ids", json.dumps({"0": 11, "1": 22, "2": 33}))
    ids = await mw._email_ids()
    assert ids == {"0": 11, "1": 22, "2": 33}
    get.assert_not_called()


@pytest.mark.asyncio
async def test_email_ids_fetch_and_cache(redis, mautic):
    get, _ = mautic
    get.return_value = {"emails": [
        {"id": 5, "name": "Win-back 1: the 30-minute audit"},
        {"id": 6, "name": "Win-back 2: what the funnel data actually shows (v2)"},
        {"id": 7, "name": "Win-back 3: should I close your file"},
        {"id": 9, "name": "unrelated"},
    ]}
    ids = await mw._email_ids()
    assert ids == {"0": 5, "1": 6, "2": 7}  # prefix match for slot 1
    assert json.loads(redis.store["wb:email_ids"]) == {"0": 5, "1": 6, "2": 7}


@pytest.mark.asyncio
async def test_email_ids_partial_match_not_cached(redis, mautic):
    get, _ = mautic
    get.return_value = {"emails": [
        {"id": 1, "name": "Win-back 1: the 30-minute audit"},
        {"id": 9, "name": "unrelated"},
    ]}
    ids = await mw._email_ids()
    assert ids == {"0": 1}  # partial result returned but not cached
    assert "wb:email_ids" not in redis.store


@pytest.mark.asyncio
async def test_email_ids_no_redis(monkeypatch, mautic):
    monkeypatch.setattr(mw, "_redis", AsyncMock(return_value=None))
    assert await mw._email_ids() == {}


@pytest.mark.asyncio
async def test_mautic_contact_id_shapes(mautic):
    get, _ = mautic
    get.return_value = {"contacts": {"1": {"id": 7, "email": "Lead@Acme.gr"}}}
    assert await mw._mautic_contact_id("lead@acme.gr") == 7
    get.return_value = {"contacts": [{"id": 8, "email": "other@x.io"}, {"id": 9, "email": "lead@acme.gr"}]}
    assert await mw._mautic_contact_id("lead@acme.gr") == 9
    get.return_value = {"contacts": {}}
    assert await mw._mautic_contact_id("nobody@x.io") is None


@pytest.mark.asyncio
async def test_mautic_last_active(mautic):
    get, _ = mautic
    get.return_value = {"contact": {"lastActive": "2026-11-01T10:00:00Z"}}
    out = await mw._mautic_last_active(3)
    assert out is not None and out.year == 2026
    get.return_value = {"contact": {}}
    assert await mw._mautic_last_active(3) is None


@pytest.mark.asyncio
async def test_sweep_redis_down(leads, mautic, monkeypatch):
    monkeypatch.setattr(mw, "_redis", AsyncMock(side_effect=[None, None, None, None]))
    leads.return_value = [_lead()]
    out = await mw.run_mautic_winback_sweep(dry_run=True)
    assert out["errors"] >= 1


_IDS = json.dumps({"0": 1, "1": 2, "2": 3})


async def _seed_ids(redis):
    await redis.set("wb:email_ids", _IDS)


@pytest.mark.asyncio
async def test_sweep_junk_and_tier_skips(redis, mautic, leads):
    get, _ = mautic
    await _seed_ids(redis)
    leads.return_value = [
        _lead(id_="j1", email="probe@test.dev"),                 # junk
        _lead(id_="j2", email="me@cloudless.gr"),               # own domain junk
        _lead(id_="w1", email="warm@acme.gr", status="Assigned"),  # warm tier
        _lead(id_="c1", email="cold@acme.gr", status="Dead"),
    ]
    get.side_effect = [
        {"contacts": {"9": {"id": 42, "email": "cold@acme.gr"}}},  # contact search
        {"contact": {"lastActive": ""}},                            # lastActive
    ]
    out = await mw.run_mautic_winback_sweep(dry_run=True)
    assert out["scanned"] == 4
    assert out["skipped_tier"] == 1
    assert out["sent"] == 1  # dry-run counts the would-send
    assert mw._state_key("cold@acme.gr") not in redis.store  # dry-run does not persist


@pytest.mark.asyncio
async def test_sweep_terminal_state_skips(redis, mautic, leads):
    get, _ = mautic
    await _seed_ids(redis)
    leads.return_value = [_lead(id_="c1", email="cold@acme.gr", status="Dead")]
    await redis.set(mw._state_key("cold@acme.gr"), json.dumps({"status": "exhausted"}))
    out = await mw.run_mautic_winback_sweep(dry_run=True)
    assert out["skipped_state"] == 1 and out["sent"] == 0
    get.assert_not_called()


@pytest.mark.asyncio
async def test_sweep_engagement_pauses(redis, mautic, leads):
    get, post = mautic
    await _seed_ids(redis)
    leads.return_value = [_lead(id_="c1", email="cold@acme.gr", status="Dead")]

    # step 0 sent just now → next step (+5d) not due → skipped_state
    await redis.set(mw._state_key("cold@acme.gr"), json.dumps({
        "step": 0, "started_at": _now(), "last_sent": _now(),
        "next_at": (datetime.now(UTC) + timedelta(days=5)).isoformat(),
    }))
    out = await mw.run_mautic_winback_sweep(dry_run=True)
    assert out["sent"] == 0

    # lastActive newer than last_sent → engaged terminal state
    await redis.set(mw._state_key("cold@acme.gr"), json.dumps({
        "step": 0,
        "started_at": (datetime.now(UTC) - timedelta(days=6)).isoformat(),
        "last_sent": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
        "next_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
    }))
    get.side_effect = [
        {"contacts": {"9": {"id": 42, "email": "cold@acme.gr"}}},
        {"contact": {"lastActive": _now()}},  # active AFTER last_sent
    ]
    out = await mw.run_mautic_winback_sweep(dry_run=True)
    assert out["skipped_engaged"] == 1
    state = json.loads(redis.store[mw._state_key("cold@acme.gr")])
    assert state["status"] == "engaged"
    post.assert_not_called()


@pytest.mark.asyncio
async def test_sweep_real_send_writes_state(redis, mautic, leads):
    get, post = mautic
    await _seed_ids(redis)
    leads.return_value = [_lead(id_="c1", email="cold@acme.gr", status="Dead")]
    get.side_effect = [
        {"contacts": {"9": {"id": 42, "email": "cold@acme.gr"}}},
        {"contact": {"lastActive": ""}},
    ]
    out = await mw.run_mautic_winback_sweep(dry_run=False)
    assert out["sent"] == 1
    post.assert_awaited_once()
    assert "/emails/1/contact/42/send" in post.await_args.args[0]
    state = json.loads(redis.store[mw._state_key("cold@acme.gr")])
    assert state["step"] == 0 and state["last_sent"]


@pytest.mark.asyncio
async def test_sweep_send_failure_and_no_contact(redis, mautic, leads):
    get, post = mautic
    await _seed_ids(redis)
    leads.return_value = [
        _lead(id_="c1", email="gone@acme.gr", status="Dead"),
        _lead(id_="c2", email="fail@acme.gr", status="Dead"),
    ]
    get.side_effect = [
        {"contacts": {}},                                             # gone@ → no contact
        {"contacts": {"8": {"id": 43, "email": "fail@acme.gr"}}},
        {"contact": {"lastActive": ""}},
    ]
    post.return_value = {"success": False}
    out = await mw.run_mautic_winback_sweep(dry_run=False)
    assert out["skipped_no_contact"] == 1
    assert out["errors"] == 1  # send returned no success


@pytest.mark.asyncio
async def test_sweep_step_progression_to_exhausted(redis, mautic, leads):
    """Step 2 (breakup) send marks the sequence exhausted."""
    get, post = mautic
    await _seed_ids(redis)
    leads.return_value = [_lead(id_="c1", email="cold@acme.gr", status="Dead")]
    old = (datetime.now(UTC) - timedelta(days=20)).isoformat()
    await redis.set(mw._state_key("cold@acme.gr"), json.dumps({
        "step": 1, "started_at": old, "last_sent": old,
        "next_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
    }))
    get.side_effect = [
        {"contacts": {"9": {"id": 42, "email": "cold@acme.gr"}}},
        {"contact": {"lastActive": ""}},
    ]
    out = await mw.run_mautic_winback_sweep(dry_run=False)
    assert out["sent"] == 1
    assert "/emails/3/contact/42/send" in post.await_args.args[0]
    state = json.loads(redis.store[mw._state_key("cold@acme.gr")])
    assert state["status"] == "exhausted" and state["step"] == 2


@pytest.mark.asyncio
async def test_sweep_missing_email_id(redis, mautic, leads):
    """No Mautic email template resolvable → per-lead error, no send."""
    get, post = mautic
    get.return_value = {"emails": []}  # _email_ids fetch finds nothing
    leads.return_value = [_lead(id_="c1", email="cold@acme.gr", status="Dead")]
    out = await mw.run_mautic_winback_sweep(dry_run=True)
    assert out["errors"] == 1 and out["sent"] == 0
    post.assert_not_called()


@pytest.mark.asyncio
async def test_sweep_lead_exception_isolated(redis, mautic, leads):
    get, _ = mautic
    await _seed_ids(redis)
    leads.return_value = [_lead(id_="c1", email="cold@acme.gr", status="Dead")]
    get.side_effect = RuntimeError("mautic down")
    out = await mw.run_mautic_winback_sweep(dry_run=False)
    assert out["errors"] == 1


def test_task_wrapper(monkeypatch):
    """Sync test — the Celery task is sync; asyncio.run needs no loop."""
    from app.worker.tasks import mautic_winback as task

    async def _svc(**kw):
        return {"sent": 1}

    monkeypatch.setattr(task.mautic_winback, "run_mautic_winback_sweep", _svc)
    monkeypatch.setattr(task, "run_async", lambda coro: asyncio.run(coro))
    assert task.run_mautic_winback_sweep(dry_run=True) == {"sent": 1}

    async def _boom(**kw):
        raise RuntimeError("x")

    monkeypatch.setattr(task.mautic_winback, "run_mautic_winback_sweep", _boom)
    assert task.run_mautic_winback_sweep() is None
