"""Unit tests for scripts/funnel_audit.py — the Tolstova & Silakova stage
audit: reach/engagement/drip SQL helpers, win-back Redis stats, stage
assembly (conv_pct, proxy exclusion, truncation, sources), weakest
transition, auction warning, render output and the broken-stage exit."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import scripts.funnel_audit as FA

# ── DB helpers ──────────────────────────────────────────────────────────


class _Scalar:
    def __init__(self, value):
        self._v = value

    def scalar(self):
        return self._v


class _Session:
    """async_session_maker() → async CM yielding a db whose execute().scalar()
    pops values from a queue (one per SQL call, in order)."""

    def __init__(self, values):
        self._values = list(values)
        self.queries = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, clause):
        self.queries.append(str(clause))
        return _Scalar(self._values.pop(0) if self._values else 0)


def _patch_session(monkeypatch, values):
    session = _Session(values)
    monkeypatch.setattr(FA, "async_session_maker", lambda: session)
    return session


@pytest.mark.asyncio
async def test_social_reach_latest_and_30d_ago(monkeypatch):
    s = _patch_session(monkeypatch, [1200, 900])
    latest, prev = await FA._social_reach()
    assert (latest, prev) == (1200, 900)
    assert len(s.queries) == 2
    assert "interval '30 days'" in s.queries[1]


@pytest.mark.asyncio
async def test_engaged_and_drip_counts(monkeypatch):
    _patch_session(monkeypatch, [42])
    assert await FA._engaged_30d() == 42
    _patch_session(monkeypatch, [7])
    assert await FA._drip_sends() == 7


@pytest.mark.asyncio
async def test_helpers_scalar_none_becomes_zero(monkeypatch):
    _patch_session(monkeypatch, [None, None])
    assert await FA._social_reach() == (0, 0)
    _patch_session(monkeypatch, [None])
    assert await FA._engaged_30d() == 0
    _patch_session(monkeypatch, [None])
    assert await FA._drip_sends() == 0


# ── win-back stats ──────────────────────────────────────────────────────


class _FakeRedis:
    def __init__(self, keys_by_pattern, values=None):
        self._keys = keys_by_pattern
        self._values = values or {}
        self.closed = False

    async def scan_iter(self, match=None):
        for k in self._keys.get(match, []):
            yield k

    async def get(self, key):
        return self._values.get(key)

    async def aclose(self):
        self.closed = True


def _patch_redis(monkeypatch, fake):
    import redis.asyncio as aioredis

    monkeypatch.setattr(aioredis, "from_url", lambda *a, **k: fake)
    monkeypatch.setenv("REDIS_URL", "redis://fake")


@pytest.mark.asyncio
async def test_winback_no_redis_url_returns_zeros(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setattr(FA, "get_settings", lambda: SimpleNamespace(REDIS_URL=""), raising=False)
    # get_settings is imported inside the function from app.core.config
    import app.core.config as cfg

    monkeypatch.setattr(cfg, "get_settings", lambda: SimpleNamespace(REDIS_URL=""))
    assert await FA._winback_stats() == {"winback_signals": 0, "winback_engaged": 0, "winback_exhausted": 0}


@pytest.mark.asyncio
async def test_winback_counts_signals_and_statuses(monkeypatch):
    fake = _FakeRedis(
        {"wb:noted:*": ["wb:noted:1", "wb:noted:2"], "wb:seq:*": ["wb:seq:a", "wb:seq:b", "wb:seq:c", "wb:seq:d"]},
        {
            "wb:seq:a": json.dumps({"status": "engaged"}),
            "wb:seq:b": json.dumps({"status": "exhausted"}),
            "wb:seq:c": "not-json{{{",
            # wb:seq:d → get returns None → skipped
        },
    )
    _patch_redis(monkeypatch, fake)
    out = await FA._winback_stats()
    assert out == {"winback_signals": 2, "winback_engaged": 1, "winback_exhausted": 1}
    assert fake.closed is True


@pytest.mark.asyncio
async def test_winback_redis_failure_swallowed(monkeypatch):
    import redis.asyncio as aioredis

    def _boom(*a, **k):
        raise ConnectionError("redis down")

    monkeypatch.setattr(aioredis, "from_url", _boom)
    monkeypatch.setenv("REDIS_URL", "redis://fake")
    assert (await FA._winback_stats())["winback_signals"] == 0


# ── audit() stage assembly ──────────────────────────────────────────────


def _leads():
    return [
        {"id": "l1", "status": "New", "source": "linkedin", "createdAt": "2026-01-01"},
        {"id": "l2", "status": "Contacted", "source": "linkedin", "createdAt": "2026-01-02"},
        {"id": "l3", "status": "Dead", "source": "x", "createdAt": "2026-01-03"},
        {"id": "l4", "status": None, "source": None, "createdAt": "2026-01-04"},
    ]


def _opps():
    return [
        {"id": "o1", "stage": "Qualification", "createdAt": "2026-01-01"},
        {"id": "o2", "stage": "Closed Won", "createdAt": "2026-01-02"},
        {"id": "o3", "stage": "closed lost", "createdAt": "2026-01-03"},
    ]


def _patch_audit_io(monkeypatch, *, reach=(1000, 800), engaged=50, drip=10, winback=None, leads=None, opps=None):
    monkeypatch.setattr(FA, "_social_reach", AsyncMock(return_value=reach))
    monkeypatch.setattr(FA, "_engaged_30d", AsyncMock(return_value=engaged))
    monkeypatch.setattr(FA, "_drip_sends", AsyncMock(return_value=drip))
    monkeypatch.setattr(
        FA,
        "_winback_stats",
        AsyncMock(return_value=winback or {"winback_signals": 0, "winback_engaged": 0, "winback_exhausted": 0}),
    )
    from app.services import espocrm_client as espo

    monkeypatch.setattr(espo, "list_all", AsyncMock(side_effect=[leads if leads is not None else _leads(), opps if opps is not None else _opps()]))


@pytest.mark.asyncio
async def test_audit_stage_counts_and_transitions(monkeypatch):
    _patch_audit_io(monkeypatch)
    r = await FA.audit()
    stages = {s["stage"]: s for s in r["stages"]}
    assert stages["audience"]["count"] == 1000
    assert stages["engaged"]["count"] == 50 and stages["engaged"]["proxy"] is True
    assert stages["lead"]["count"] == 4
    # nurtured = past-New/non-Dead (l2 Contacted) + l4? l4 status None counts as 'New'-ish → not nurtured
    assert stages["nurtured"]["count"] == 1
    assert stages["opportunity"]["count"] == 1  # active only — won/lost excluded
    assert stages["won"]["count"] == 1
    # conv_pct: audience has none; engaged is proxy → its conv still computed
    assert stages["audience"]["conv_pct"] is None
    assert stages["engaged"]["conv_pct"] == 5.0  # 50/1000
    # proxy transitions excluded → weakest is a real stage pair
    assert r["weakest_transition"] is not None
    assert "engaged" not in (r["weakest_transition"] or "")
    assert r["lead_sources"] == {"linkedin": 2, "x": 1, "(none)": 1}
    # reach grew (1000 > 800) and 4/1000 = 0.4% < 0.5% → auction warning fires
    assert r["auction_warning"] is True


@pytest.mark.asyncio
async def test_audit_auction_warning_fires(monkeypatch):
    # reach grew and lead conversion vs audience < 0.5%
    _patch_audit_io(monkeypatch, reach=(10000, 8000), engaged=10, leads=[{"id": "l", "status": "New", "source": "x"}] * 10, opps=[])
    r = await FA.audit()
    assert r["auction_warning"] is True  # 10/10000 = 0.1%
    # same reach but not growing → no warning
    _patch_audit_io(monkeypatch, reach=(8000, 10000), engaged=10, leads=[{"id": "l", "status": "New", "source": "x"}] * 10, opps=[])
    r2 = await FA.audit()
    assert r2["auction_warning"] is False
    # growing but healthy conversion (4/500 = 0.8%) → no warning
    _patch_audit_io(monkeypatch, reach=(500, 400), engaged=50, leads=_leads(), opps=_opps())
    r3 = await FA.audit()
    assert r3["auction_warning"] is False


@pytest.mark.asyncio
async def test_audit_truncation_note(monkeypatch):
    big = [{"id": f"l{i}", "status": "New", "source": "x"} for i in range(25000)]
    _patch_audit_io(monkeypatch, leads=big, opps=[])
    r = await FA.audit()
    lead_stage = next(s for s in r["stages"] if s["stage"] == "lead")
    assert "truncated" in lead_stage["note"]


@pytest.mark.asyncio
async def test_audit_winback_passthrough(monkeypatch):
    wb = {"winback_signals": 3, "winback_engaged": 1, "winback_exhausted": 2}
    _patch_audit_io(monkeypatch, winback=wb)
    r = await FA.audit()
    assert r["winback"] == wb


# ── render() ────────────────────────────────────────────────────────────


def _report(**kw):
    base = {
        "generated_at": "2026-11-15T10:00:00+00:00",
        "lead_sources": {"linkedin": 3, "x": 1},
        "stages": [
            {"stage": "audience", "count": 1000, "conv_pct": None, "note": "followers"},
            {"stage": "engaged", "count": 50, "conv_pct": 5.0, "proxy": True, "note": "posts"},
            {"stage": "lead", "count": 4, "conv_pct": 8.0, "note": "leads"},
        ],
        "weakest_transition": "lead→nurtured (0.0%)",
        "auction_warning": True,
        "winback": {"winback_signals": 2, "winback_engaged": 1, "winback_exhausted": 0},
    }
    base.update(kw)
    return base


def test_render_full():
    out = FA.render(_report())
    assert "Funnel audit" in out
    assert "(proxy)" in out
    assert "weakest transition: lead→nurtured (0.0%)" in out
    assert "auction warning" in out
    assert "linkedin=3" in out and "x=1" in out
    assert "win-back engine" in out and "2 re-engagement signals" in out


def test_render_minimal():
    out = FA.render(
        _report(
            stages=[{"stage": "audience", "count": 0, "conv_pct": None, "note": "n"}],
            weakest_transition=None,
            auction_warning=False,
            winback={},
            lead_sources={},
        )
    )
    assert "weakest transition" not in out and "auction warning" not in out
    assert "lead sources:" in out and "win-back" not in out


# ── main() broken-stage exit ────────────────────────────────────────────


def test_main_exits_1_on_broken_stage(monkeypatch):
    report = _report(
        stages=[
            {"stage": "audience", "count": 100, "conv_pct": None, "note": "n"},
            {"stage": "lead", "count": 0, "conv_pct": 0.0, "note": "n"},  # zero with upstream volume
        ]
    )

    async def _fake_audit():
        return report

    monkeypatch.setattr(FA, "audit", _fake_audit)
    monkeypatch.setattr(sys, "argv", ["funnel_audit.py"])
    with pytest.raises(SystemExit) as e:
        FA.main()
    assert e.value.code == 1


def test_main_exits_0_when_healthy(monkeypatch, capsys):
    report = _report(
        stages=[
            {"stage": "audience", "count": 100, "conv_pct": None, "note": "n"},
            {"stage": "lead", "count": 5, "conv_pct": 5.0, "note": "n"},
        ],
        lead_sources={},
        winback={},
        weakest_transition=None,
        auction_warning=False,
    )

    async def _fake_audit():
        return report

    monkeypatch.setattr(FA, "audit", _fake_audit)
    monkeypatch.setattr(sys, "argv", ["funnel_audit.py", "--json"])
    with pytest.raises(SystemExit) as e:
        FA.main()
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert json.loads(out)["stages"][1]["count"] == 5
