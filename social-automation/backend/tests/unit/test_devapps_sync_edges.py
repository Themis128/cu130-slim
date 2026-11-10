"""Edge-branch coverage for devapps_sync pullers, dispatcher, helpers,
task wrappers, and the lake exporters that consume the snapshots."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from app.services import devapps_sync as ds
from tests.unit.test_devapps_sync_pullers import _GRAPH, _account, _FakeDB


def _boom_client(**_kw: Any):
    raise RuntimeError("no http client")


# ── tiny helpers ──────────────────────────────────────────────────────────────


def test_resp_json_edges() -> None:
    class _R:
        def __init__(self, payload: Any) -> None:
            self._p = payload

        def json(self):
            if isinstance(self._p, Exception):
                raise self._p
            return self._p

    assert ds._resp_json(_R(ValueError("bad"))) == {}
    assert ds._resp_json(_R([1, 2])) == {"value": [1, 2]}
    assert ds._resp_json(_R({"a": 1})) == {"a": 1}


@pytest.mark.asyncio
async def test_meta_debug_token_empty_scope_names() -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__regex=rf"{_GRAPH}debug_token.*").mock(
            return_value=respx.MockResponse(200, json={"data": {"is_valid": True, "granular_scopes": [{"no_scope_key": 1}]}})
        )
        async with httpx.AsyncClient() as client:
            out = await ds._meta_debug_token(client, "tok")
    assert out["token_valid"] is True
    assert "debug_scopes" not in out


# ── facebook section failures ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pull_facebook_section_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__regex=rf"{_GRAPH}debug_token.*").mock(side_effect=httpx.ConnectError("down"))
        mock.get(url__regex=rf"{_GRAPH}12345\?.*").mock(side_effect=httpx.ConnectError("down"))
        monkeypatch.setattr(
            "app.services.analytics_sync._meta_insights_get",
            AsyncMock(side_effect=RuntimeError("insights boom")),
        )
        out = await ds._pull_facebook(None, _account("facebook"), "tok")
    assert out["ok"] is True
    assert out["health"]["token_valid"] is False
    assert "debug_error" in out["health"]
    assert "object_error" in out["insights"]
    assert "insights_error" in out["insights"]


@pytest.mark.asyncio
async def test_pull_facebook_insights_row_edges() -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__regex=rf"{_GRAPH}debug_token.*").mock(return_value=respx.MockResponse(200, json={"data": {"is_valid": True}}))
        mock.get(url__regex=rf"{_GRAPH}12345\?.*").mock(return_value=respx.MockResponse(200, json={"name": "P", "followers_count": 3}))
        mock.get(url__regex=rf"{_GRAPH}12345/insights.*").mock(
            return_value=respx.MockResponse(
                200,
                json={
                    "data": [
                        {"name": "empty_metric", "values": []},
                        {"name": "short_metric", "values": [{"value": 1}, {"value": 2}, {"value": 3}]},
                    ]
                },
            )
        )
        out = await ds._pull_facebook(None, _account("facebook"), "tok")
    ins = out["insights"]
    assert "empty_metric_7d" not in ins
    assert ins["short_metric_7d"] == 3
    assert "short_metric_30d" not in ins  # <7 values → no 30d aggregate


@pytest.mark.asyncio
async def test_pull_facebook_outer_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ds.httpx, "AsyncClient", _boom_client)
    out = await ds._pull_facebook(None, _account("facebook"), "tok")
    assert out["ok"] is False
    assert "no http client" in out["error"]


# ── instagram section failures ────────────────────────────────────────────────
#
# debug_token / object / demographics all go through client.get; the insights
# helper is patched so the insights URL is only hit by the demographics call.


class _IGClientSel:
    """client.get: insights URL → canned response, everything else → ConnectError."""

    insights_resp: Any = None

    def __init__(self, *a: Any, **kw: Any) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, **kw):
        if "insights" in str(url):
            if isinstance(self.insights_resp, Exception):
                raise self.insights_resp
            return self.insights_resp
        raise httpx.ConnectError("down")


def _patch_ig_client(monkeypatch: pytest.MonkeyPatch, insights_resp: Any) -> None:
    cls = type("_IG", (_IGClientSel,), {"insights_resp": insights_resp})
    monkeypatch.setattr(ds.httpx, "AsyncClient", cls)


@pytest.mark.asyncio
async def test_pull_instagram_section_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    # non-200 demographics → skipped; debug/object get ConnectError → section errors
    _patch_ig_client(monkeypatch, SimpleNamespace(status_code=500, json=lambda: {}))
    monkeypatch.setattr(
        "app.services.analytics_sync._meta_insights_get",
        AsyncMock(return_value=SimpleNamespace(status_code=500, json=lambda: {})),
    )
    out = await ds._pull_instagram(None, _account("instagram"), "tok")
    assert out["ok"] is True
    assert out["health"]["token_valid"] is False
    assert "debug_error" in out["health"]
    assert "object_error" in out["insights"]
    assert out["demographics"] == {}


@pytest.mark.asyncio
async def test_pull_instagram_insights_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_ig_client(monkeypatch, httpx.ConnectError("down"))  # demographics raises → pass
    responses = iter(
        [
            # 200 with an empty-values row → loop-continue branch
            SimpleNamespace(status_code=200, json=lambda: {"data": [{"name": "reach", "values": []}]}),
            # non-200 → rows skipped
            SimpleNamespace(status_code=500, json=lambda: {}),
            RuntimeError("graph 500"),  # helper raises → insights_error
        ]
    )

    async def _fake_insights(*a: Any, **kw: Any):
        item = next(responses)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr("app.services.analytics_sync._meta_insights_get", _fake_insights)
    for _ in range(3):
        out = await ds._pull_instagram(None, _account("instagram"), "tok")
    assert out["ok"] is True
    assert "insights_error" in out["insights"]
    assert out["demographics"] == {}


@pytest.mark.asyncio
async def test_pull_instagram_outer_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ds.httpx, "AsyncClient", _boom_client)
    out = await ds._pull_instagram(None, _account("instagram"), "tok")
    assert out["ok"] is False


# ── threads ───────────────────────────────────────────────────────────────────


class _ThreadsEdge:
    def __init__(self, *a: Any, **kw: Any) -> None:
        pass

    async def validate_token(self):
        raise RuntimeError("token dead")

    async def get_profile(self):
        raise RuntimeError("profile gone")

    async def get_insights(self, metric: str):
        if metric == "views":
            return "not-a-dict"
        if metric == "likes":
            raise RuntimeError("metric down")
        return {"metric": metric}


@pytest.mark.asyncio
async def test_pull_threads_section_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.threads_api.ThreadsAPIClient", _ThreadsEdge)
    out = await ds._pull_threads(None, _account("threads"), "tok")
    assert out["ok"] is True
    assert out["health"]["token_valid"] is False
    assert "profile_error" in out["insights"]
    assert "insights_replies" in out["insights"]
    assert "insights_views" not in out["insights"]  # non-dict skipped
    assert "insights_likes" not in out["insights"]  # raise → pass


@pytest.mark.asyncio
async def test_pull_threads_outer_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _ctor(*a: Any, **kw: Any):
        raise RuntimeError("ctor boom")

    monkeypatch.setattr("app.services.threads_api.ThreadsAPIClient", _ctor)
    out = await ds._pull_threads(None, _account("threads"), "tok")
    assert out["ok"] is False
    assert "ctor boom" in out["error"]


# ── linkedin ──────────────────────────────────────────────────────────────────


class _LinkedInEdge:
    def __init__(self, *a: Any, **kw: Any) -> None:
        pass

    async def validate_token(self):
        raise RuntimeError("401 unauthorized")

    async def get_follower_count(self, urn: str):
        raise RuntimeError("followers down")

    async def get_organization(self, org_id: str):
        raise RuntimeError("org down")

    async def get_organization_lifetime_stats(self, urn: str):
        raise RuntimeError("stats down")


@pytest.mark.asyncio
async def test_pull_linkedin_section_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.linkedin_api.LinkedInAPIClient", _LinkedInEdge)
    monkeypatch.setattr("app.services.analytics_sync._org_urn", lambda a: "urn:li:organization:42")
    out = await ds._pull_linkedin(None, _account("linkedin"), "tok")
    assert out["ok"] is True
    assert out["health"]["token_valid"] is False
    assert "error" in out["health"]
    assert "note" in out["insights"]  # member account note still set

    account = _account("linkedin", account_type="organization", meta_data={"author_urn": "urn:li:organization:42"})
    out2 = await ds._pull_linkedin(None, account, "tok")
    assert "followers_error" in out2["insights"]
    assert "organization_error" in out2["insights"]
    assert "stats_error" in out2["insights"]


@pytest.mark.asyncio
async def test_pull_linkedin_outer_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _ctor(*a: Any, **kw: Any):
        raise RuntimeError("li ctor")

    monkeypatch.setattr("app.services.linkedin_api.LinkedInAPIClient", _ctor)
    out = await ds._pull_linkedin(None, _account("linkedin"), "tok")
    assert out["ok"] is False


# ── twitter / tiktok ──────────────────────────────────────────────────────────


class _TwitterEdge:
    def __init__(self, *a: Any, **kw: Any) -> None:
        pass

    async def validate_token(self):
        raise RuntimeError("token revoked")

    async def get_me_metrics(self):
        return {"ok": True}


class _TikTokEdge:
    def __init__(self, *a: Any, **kw: Any) -> None:
        pass

    async def validate_token(self):
        raise RuntimeError("tiktok 401")

    async def get_creator_info(self):
        raise RuntimeError("creator scope missing")

    async def get_stats(self):
        raise RuntimeError("stats scope missing")


@pytest.mark.asyncio
async def test_pull_twitter_validate_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.twitter_api.TwitterAPIClient", _TwitterEdge)
    out = await ds._pull_twitter(None, _account("twitter"), "tok")
    assert out["ok"] is True
    assert out["health"]["token_valid"] is False
    assert out["insights"]["me_metrics"]["ok"] is True


@pytest.mark.asyncio
async def test_pull_twitter_outer_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _ctor(*a: Any, **kw: Any):
        raise RuntimeError("tw ctor")

    monkeypatch.setattr("app.services.twitter_api.TwitterAPIClient", _ctor)
    out = await ds._pull_twitter(None, _account("twitter"), "tok")
    assert out["ok"] is False


@pytest.mark.asyncio
async def test_pull_tiktok_section_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.tiktok_api.TikTokAPIClient", _TikTokEdge)
    out = await ds._pull_tiktok(None, _account("tiktok"), "tok")
    assert out["ok"] is True
    assert out["health"]["token_valid"] is False
    assert "creator_error" in out["insights"]
    assert "stats_error" in out["insights"]


@pytest.mark.asyncio
async def test_pull_tiktok_outer_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _ctor(*a: Any, **kw: Any):
        raise RuntimeError("tt ctor")

    monkeypatch.setattr("app.services.tiktok_api.TikTokAPIClient", _ctor)
    out = await ds._pull_tiktok(None, _account("tiktok"), "tok")
    assert out["ok"] is False


# ── telegram ──────────────────────────────────────────────────────────────────


class _TelegramEdge:
    def __init__(self, *a: Any, **kw: Any) -> None:
        pass

    async def get_me(self):
        raise RuntimeError("bot token invalid")

    async def get_webhook_info(self):
        raise RuntimeError("webhook down")

    async def get_chat(self, chat_id):
        raise RuntimeError("chat gone")


@pytest.mark.asyncio
async def test_pull_telegram_section_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.telegram_api.TelegramAPIClient", _TelegramEdge)
    monkeypatch.setattr(ds, "decrypt_token", lambda raw: "tok")
    account = _account(
        "telegram",
        meta_data={"bot_token_enc": b"e", "telegram_channel": {"chat_id": "-100"}},
    )
    out = await ds._pull_telegram(None, account)
    assert out["ok"] is True
    assert out["health"]["token_valid"] is False
    assert "webhook" not in out["health"]  # webhook failure swallowed
    assert "channel_error" in out["insights"]

    # no managed channel → chat lookup skipped entirely
    account2 = _account("telegram", meta_data={"bot_token_enc": b"e"})
    out2 = await ds._pull_telegram(None, account2)
    assert "channel" not in out2["insights"]
    assert "channel_error" not in out2["insights"]


@pytest.mark.asyncio
async def test_pull_telegram_outer_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _decrypt(_raw):
        raise RuntimeError("decrypt fail")

    monkeypatch.setattr(ds, "decrypt_token", _decrypt)
    out = await ds._pull_telegram(None, _account("telegram"))
    assert out["ok"] is False
    assert "decrypt fail" in out["error"]


# ── whatsapp / viber outer errors ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pull_whatsapp_section_and_outer_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    class _WAEdge:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

        async def get_phone_number_info_by_id(self, pid: str):
            raise RuntimeError("phone info down")

    monkeypatch.setattr("app.services.whatsapp_api.WhatsAppAPIClient", _WAEdge)
    out = await ds._pull_whatsapp(None, _account("whatsapp"), "tok")
    assert out["ok"] is True
    assert out["health"]["token_valid"] is False

    def _ctor(*a: Any, **kw: Any):
        raise RuntimeError("wa ctor")

    monkeypatch.setattr("app.services.whatsapp_api.WhatsAppAPIClient", _ctor)
    out2 = await ds._pull_whatsapp(None, _account("whatsapp"), "tok")
    assert out2["ok"] is False


@pytest.mark.asyncio
async def test_pull_viber_outer_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _ctor(*a: Any, **kw: Any):
        raise RuntimeError("viber ctor")

    monkeypatch.setattr("app.services.viber_api.ViberAPIClient", _ctor)
    out = await ds._pull_viber(None, _account("viber"), "tok")
    assert out["ok"] is False


# ── dispatcher edges ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sync_devapp_account_telegram_and_token_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ds, "decrypt_token", lambda raw: "tok")

    class _TG:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

        async def get_me(self):
            return {"id": 1}

        async def get_webhook_info(self):
            return {}

    monkeypatch.setattr("app.services.telegram_api.TelegramAPIClient", _TG)
    db = _FakeDB()
    tg = _account("telegram", meta_data={"bot_token_enc": b"e"})
    payload = await ds.sync_devapp_account(db, tg)
    assert payload["ok"] is True and payload["health"]["token_valid"] is True

    # regular puller receives the decrypted token
    seen: dict[str, Any] = {}

    async def _fake_puller(_db, account, token):
        seen["token"] = token
        return {"ok": True, "platform": account.platform}

    monkeypatch.setitem(ds._PULLERS, "fakeplat", _fake_puller)
    acc = _account("fakeplat")
    out = await ds.sync_devapp_account(db, acc)
    assert seen["token"] == "tok"
    assert out["ok"] is True


@pytest.mark.asyncio
async def test_sync_devapp_account_puller_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ds, "decrypt_token", lambda raw: "tok")

    async def _raiser(_db, account, token):
        raise RuntimeError("puller exploded")

    monkeypatch.setitem(ds._PULLERS, "boomplat", _raiser)
    out = await ds.sync_devapp_account(_FakeDB(), _account("boomplat"))
    assert out["ok"] is False
    assert "puller exploded" in out["error"]


@pytest.mark.asyncio
async def test_sync_all_platform_filter_and_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    acct_a = _account("plat-a", username="a")
    acct_b = _account("plat-b", username="b")

    class _Result:
        def scalars(self):
            return self

        def all(self):
            return [acct_a, acct_b]

    class _DB(_FakeDB):
        async def execute(self, q, p=None):
            return _Result()

    calls = {"n": 0}

    async def _fake_sync(db, account):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("account exploded")
        return {"ok": True, "platform": account.platform}

    monkeypatch.setattr(ds, "sync_devapp_account", _fake_sync)
    res = await ds.sync_all_devapps(_DB(), platform="plat-a")
    assert res["accounts"] == 2
    assert res["ok"] == 1 and res["failed"] == 1
    assert res["results"][0]["ok"] is False
    assert "account exploded" in res["results"][0]["error"]


# ── channel-action helper edges ───────────────────────────────────────────────


def test_health_action_expiry_far_future_and_bad_iso() -> None:
    from datetime import UTC, datetime, timedelta

    now = datetime(2026, 11, 8, tzinfo=UTC)
    far = {"platform": "x", "ok": True, "health": {"token_valid": True, "expires_at": (now + timedelta(days=30)).isoformat()}, "insights": {}}
    assert ds._health_action(far, now) is None
    bad = {"platform": "x", "ok": True, "health": {"token_valid": True, "expires_at": "garbage"}, "insights": {}}
    assert ds._health_action(bad, now) is None


def test_followers_creator_non_dict_and_num_recursion_miss() -> None:
    assert ds._followers({"insights": {"creator_info": "weird"}}) is None
    assert ds._num({"a": {"b": {"c": 1}}}, "a", "missing") is None
    assert ds._num({"a": {"b": {"c": 7}}}, "a", "b", "c") == 7.0


# ── task wrappers ─────────────────────────────────────────────────────────────


def test_sync_devapps_task_runs_inner(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    import app.services.devapps_sync as svc
    import app.worker.tasks.devapps_sync as task_mod

    class _CM:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(task_mod, "task_session", lambda: _CM())
    monkeypatch.setattr(task_mod, "run_async", lambda coro: asyncio.run(coro))
    fake = AsyncMock(return_value={"accounts": 0, "ok": 0, "failed": 0, "results": []})
    monkeypatch.setattr(svc, "sync_all_devapps", fake)
    out = task_mod.sync_devapps(platform="facebook")
    assert out["accounts"] == 0
    fake.assert_awaited_once()
    assert fake.await_args.kwargs["platform"] == "facebook"


def test_run_channel_actions_wrapper(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.worker.tasks.devapp_channel_actions as task_mod

    monkeypatch.setattr(task_mod, "run_async", lambda coro: (coro.close(), "done")[1])
    assert task_mod.run_channel_actions() == "done"


@pytest.mark.asyncio
async def test_channel_actions_run_teams_and_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.worker.tasks.devapp_channel_actions as task_mod

    t_ok = uuid.uuid4()
    t_none = uuid.uuid4()
    healthy = {
        "platform": "facebook",
        "account_id": "a1",
        "username": "u",
        "account_type": "page",
        "ok": True,
        "health": {"token_valid": True},
        "insights": {},
        "captured_at": "2026-11-08T00:00:00+00:00",
    }
    rows = [
        {"team_id": t_ok, "meta_data": healthy},
        {"team_id": t_none, "meta_data": {"platform": "x", "ok": True, "health": {"token_valid": True}, "insights": {}}},
        {"team_id": uuid.uuid4(), "meta_data": "not-a-dict"},
    ]

    class _Rows:
        def mappings(self):
            return self

        def all(self):
            return rows

    class _DB:
        async def execute(self, q, p=None):
            return _Rows()

        async def get(self, model, pk):
            return object() if pk == t_ok else None

        async def commit(self):
            pass

    class _CM:
        async def __aenter__(self):
            return _DB()

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(task_mod, "task_session", lambda: _CM())
    sched = AsyncMock(return_value=[{"status": "scheduled"}])
    monkeypatch.setattr("app.services.monetization_executor.schedule_report_actions", sched)
    out = await task_mod._run()
    assert out["teams"] == 2
    assert sched.await_count == 1
    assert out["results"] == [{"team_id": str(t_ok), "status": "scheduled"}]


# ── lake exporters ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_export_devapps_and_channel_actions() -> None:
    from app.worker.tasks import datalake_export as de

    snap = {
        "platform": "facebook",
        "ok": True,
        "health": {"token_valid": True},
        "insights": {"followers": 5},
    }

    class _Rows:
        def __init__(self, rows):
            self._rows = rows

        def mappings(self):
            return self

        def all(self):
            return self._rows

    class _DB:
        def __init__(self):
            self.calls = 0

        async def execute(self, q, p=None):
            self.calls += 1
            return _Rows([{"meta_data": snap}, {"meta_data": None}])

    rows = await de._export_devapps(_DB())
    assert rows == [snap, {}]
    actions = await de._export_channel_actions(_DB())
    assert len(actions) == 2
    assert {a["type"] for a in actions} == {"channel_action"}
