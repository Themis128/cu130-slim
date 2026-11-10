"""Unit coverage for app.worker.tasks.token_expiry — warn/reauth buckets + post."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.worker.tasks.token_expiry as TE

pytestmark = pytest.mark.asyncio


class _AsyncCM:
    def __init__(self, v):
        self._v = v

    async def __aenter__(self):
        return self._v

    async def __aexit__(self, *a):
        return False


def _db(rows):
    return SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))),
        commit=AsyncMock(),
    )


def _acc(plat, name, meta, status="active", col_exp=None):
    return SimpleNamespace(platform=plat, username=name, meta_data=meta, status=status, id="id-1", token_expires_at=col_exp)


async def test_sweep_warn_and_reauth_buckets(monkeypatch):
    rows = [
        _acc("tiktok", "tt1", {"expires_at": "2020-01-01T00:00:00+00:00"}),  # expired, refreshable → warn
        _acc("linkedin", "li1", {"token_expires_at": "2020-01-01T00:00:00+00:00"}),  # expired, non-refresh → reauth
        _acc("facebook", "fb1", {"expires_at": "2999-01-01T00:00:00+00:00"}),  # far future → skip
        _acc("instagram", "ig1", {}),  # no expiry → skip
        _acc("twitter", "x1", {"expires_at": "bad-date"}),  # unparseable → skip
        _acc("whatsapp", "wa1", {"expires_at": "2999-01-01T00:00:00+00:00"}),  # no lead → skip
    ]
    monkeypatch.setattr(TE, "task_session", lambda: _AsyncCM(_db(rows)))
    rep = await TE._sweep()
    assert len(rep["warn"]) == 1 and rep["warn"][0]["platform"] == "tiktok"
    assert len(rep["reauth"]) == 1 and rep["reauth"][0]["platform"] == "linkedin"


async def test_sweep_warn_inside_lead_not_expired(monkeypatch):
    import datetime as _dt

    soon = (_dt.datetime.now(_dt.UTC) + _dt.timedelta(days=2)).isoformat()
    rows = [_acc("linkedin", "li2", {"expires_at": soon})]
    monkeypatch.setattr(TE, "task_session", lambda: _AsyncCM(_db(rows)))
    rep = await TE._sweep()
    assert rep["warn"] and not rep["reauth"]


async def test_sweep_expired_statuses_and_column_expiry(monkeypatch):
    import datetime as _dt

    past = _dt.datetime.now(_dt.UTC) - _dt.timedelta(hours=1)
    rows = [
        _acc("twitter", "tw1", {}, status="expired", col_exp=past),  # column expiry, dead status → reauth
        _acc("instagram", "ig1", {}, status="expired", col_exp=past),  # refreshable dead → warn
        _acc("tiktok", "tt1", {}, status="revoked"),  # revoked, no expiry → warn
        _acc("whatsapp", "wa1", {}, status="revoked"),  # revoked non-refreshable → reauth
    ]
    monkeypatch.setattr(TE, "task_session", lambda: _AsyncCM(_db(rows)))
    rep = await TE._sweep()
    assert {e["platform"] for e in rep["reauth"]} == {"twitter", "whatsapp"}
    assert {e["platform"] for e in rep["warn"]} == {"instagram", "tiktok"}


async def test_sweep_reauth_dedup_24h(monkeypatch):
    import datetime as _dt

    recent = _dt.datetime.now(_dt.UTC).isoformat()
    rows = [
        _acc("twitter", "tw1", {"reauth_escalated_at": recent}, status="expired"),
        _acc("linkedin", "li1", {"reauth_escalated_at": "2000-01-01T00:00:00+00:00"}, status="expired"),
    ]
    monkeypatch.setattr(TE, "task_session", lambda: _AsyncCM(_db(rows)))
    rep = await TE._sweep()
    assert [e["platform"] for e in rep["reauth"]] == ["linkedin"]


async def test_post_sends_when_nonempty(monkeypatch):
    posted = {}

    async def _fake_post(text):
        posted["text"] = text

    monkeypatch.setattr("app.services.slack_notifications.post_alert_to_slack", _fake_post)
    await TE._post({"warn": [{"platform": "tiktok", "account": "tt", "expires": "2026-11-01T00:00:00+00:00"}], "reauth": []})
    assert "Token expiry sweep" in posted["text"]
    await TE._post({"warn": [], "reauth": []})  # empty → no post


def test_task_wrapper_runs(monkeypatch):
    def _fake_run(coro):
        return {"warn": [], "reauth": []}

    monkeypatch.setattr(TE, "run_async", _fake_run)
    out = TE.run_token_expiry_sweep()
    assert out["status"] == "ok" and out["warn"] == 0


async def test_post_swallows_slack_failure(monkeypatch):
    async def _boom(text):
        raise RuntimeError("slack down")

    monkeypatch.setattr("app.services.slack_notifications.post_alert_to_slack", _boom)
    await TE._post({"warn": [{"platform": "tiktok", "account": "tt", "expires": "2026-11-01T00:00:00+00:00"}], "reauth": []})
