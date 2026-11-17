"""Unit tests for app/services/linkedin_ads_control.py — LinkedIn ads pause/resume
via the browser sidecar, plus Slack outcome notification."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import httpx
import pytest

import app.services.linkedin_ads_control as A


@pytest.fixture(autouse=True)
def _api_down_by_default(monkeypatch):
    """Sidecar-path tests predate the Marketing API lane — keep the API
    path unavailable unless a test opts in via _api_patch."""
    monkeypatch.setattr(A, "_api_token", AsyncMock(side_effect=RuntimeError("api disabled in test")))


def _settings(**kw):
    base = dict(
        LINKEDIN_AD_ACCOUNT_ID="512642510",
        LINKEDIN_BROWSER_SIDECAR_URL="http://sidecar:9225/",
        LINKEDIN_ADS_CAMPAIGN_ID="907100946",
        SLACK_ADS_WEBHOOK_URL="",
        SLACK_WEBHOOK_URL="http://slack/hook",
        SLACK_ADS_CHANNEL_ID="C1",
    )
    base.update(kw)
    return Mock(**{k: v for k, v in base.items()})


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._p = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPError(f"http {self.status_code}")

    def json(self):
        return self._p


class _Client:
    """Routes POST/GET by URL substring → queued responses."""

    def __init__(self, routes):
        self.routes = routes  # list of (needle, response-or-exception)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def _hit(self, method, url, **kw):
        self.calls.append((method, url, kw))
        for needle, resp in self.routes:
            if needle in url:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        raise AssertionError(f"unrouted {url}")

    async def post(self, url, **kw):
        return await self._hit("post", url, **kw)

    async def get(self, url, **kw):
        return await self._hit("get", url, **kw)


def _patch_client(monkeypatch, client):
    monkeypatch.setattr(A.httpx, "AsyncClient", lambda **kw: client)
    monkeypatch.setattr(A.asyncio, "sleep", AsyncMock())


# ── pure helpers ──────────────────────────────────────────────────────


def test_cm_campaigns_url(monkeypatch):
    monkeypatch.setattr(A, "get_settings", lambda: _settings(LINKEDIN_AD_ACCOUNT_ID="123"))
    assert A._cm_campaigns_url().endswith("/accounts/123/campaigns")
    monkeypatch.setattr(A, "get_settings", lambda: _settings(LINKEDIN_AD_ACCOUNT_ID=None))
    assert "/accounts/512642510/" in A._cm_campaigns_url()


def test_campaign_status_parse():
    text = "some header\n907100946 Cloudless boost\n\nActive\nnext row"
    assert A._campaign_status(text, "907100946") == "active"
    assert A._campaign_status("no match here", "907100946") == "unknown"


# ── sidecar plumbing ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_navigate_eval_pagetext(monkeypatch):
    monkeypatch.setattr(A, "get_settings", lambda: _settings())
    client = _Client(
        [
            ("navigate", _Resp()),
            ("eval", _Resp(payload={"result": "checkbox clicked"})),
            ("page-text", _Resp(payload={"text": "campaigns"})),
        ]
    )
    _patch_client(monkeypatch, client)
    await A._navigate("http://cm")
    assert await A._eval("js") == "checkbox clicked"
    assert await A._page_text() == "campaigns"
    # eval without "result" key returns whole payload
    client.routes[1] = ("eval", _Resp(payload={"raw": 1}))
    assert await A._eval("js") == {"raw": 1}


# ── set_campaign_status orchestration ────────────────────────────────


@pytest.mark.asyncio
async def test_set_status_guards(monkeypatch):
    monkeypatch.setattr(A, "get_settings", lambda: _settings(LINKEDIN_ADS_CAMPAIGN_ID=None))
    out = await A.set_campaign_status("pause")
    assert out["ok"] is False and "not configured" in out["detail"]

    monkeypatch.setattr(A, "get_settings", lambda: _settings())
    out = await A.set_campaign_status("explode")
    assert out["ok"] is False and "unknown action" in out["detail"]


def _cm_routes(status_before="907100946 Boost\n\nActive", status_after=None, eval_results=None):
    """Build a sidecar route table for a full toggle run."""
    page = _Resp(payload={"text": status_before})
    verify = _Resp(payload={"text": status_after or status_before})
    routes = [
        ("navigate", _Resp()),
        ("page-text", page),
        ("eval", _Resp(payload={"result": "row clicked"})),
    ]
    if eval_results:
        routes = [
            ("navigate", _Resp()),
            ("eval", eval_results),
            ("page-text", verify),
        ]
    return routes


@pytest.mark.asyncio
async def test_set_status_already_paused(monkeypatch):
    monkeypatch.setattr(A, "get_settings", lambda: _settings())
    client = _Client(
        [
            ("navigate", _Resp()),
            ("page-text", _Resp(payload={"text": "907100946\n\nPaused"})),
        ]
    )
    _patch_client(monkeypatch, client)
    out = await A.set_campaign_status("pause")
    assert out == {"ok": True, "status": "paused", "detail": "Already paused"}


@pytest.mark.asyncio
async def test_set_status_unreadable(monkeypatch):
    monkeypatch.setattr(A, "get_settings", lambda: _settings())
    client = _Client(
        [
            ("navigate", _Resp()),
            ("page-text", _Resp(payload={"text": "not logged in"})),
        ]
    )
    _patch_client(monkeypatch, client)
    out = await A.set_campaign_status("pause")
    assert out["ok"] is False and "Could not read" in out["detail"]


@pytest.mark.asyncio
async def test_set_status_full_toggle(monkeypatch):
    monkeypatch.setattr(A, "get_settings", lambda: _settings())
    evals = iter(
        [
            _Resp(payload={"result": "checkbox clicked"}),  # select row
            _Resp(payload={"result": "clicked Set status"}),  # open menu
            _Resp(payload={"result": "clicked Pause"}),  # menu item
            _Resp(payload={"result": "clicked Confirm"}),  # confirm
        ]
    )
    pages = iter(
        [
            _Resp(payload={"text": "907100946\n\nActive"}),  # initial read
            _Resp(payload={"text": "907100946\n\nPaused"}),  # verify read
        ]
    )

    class _C(_Client):
        async def post(self, url, **kw):
            if "eval" in url:
                return next(evals)
            return await super().post(url, **kw)

        async def get(self, url, **kw):
            if "page-text" in url:
                return next(pages)
            return await super().get(url, **kw)

    client = _C([("navigate", _Resp())])
    _patch_client(monkeypatch, client)
    out = await A.set_campaign_status("pause")
    assert out == {"ok": True, "status": "paused", "detail": "Campaign is now paused"}


@pytest.mark.asyncio
async def test_set_status_row_not_found_and_menu_fail(monkeypatch):
    monkeypatch.setattr(A, "get_settings", lambda: _settings())
    pages = [_Resp(payload={"text": "907100946\n\nActive"})]

    def _mk(evals):
        it = iter(evals)
        pit = iter(pages)

        class _C(_Client):
            async def post(self, url, **kw):
                if "eval" in url:
                    return next(it)
                return await super().post(url, **kw)

            async def get(self, url, **kw):
                if "page-text" in url:
                    return next(pit)
                return await super().get(url, **kw)

        return _C([("navigate", _Resp())])

    _patch_client(monkeypatch, _mk([_Resp(payload={"result": "row not found"})]))
    out = await A.set_campaign_status("pause")
    assert out["ok"] is False and "row select failed" in out["detail"]

    _patch_client(
        monkeypatch,
        _mk(
            [
                _Resp(payload={"result": "row clicked"}),
                _Resp(payload={"result": "clicked Set status"}),
                _Resp(payload={"result": "not found: Pause"}),
            ]
        ),
    )
    out = await A.set_campaign_status("pause")
    assert out["ok"] is False and "menu 'Pause' failed" in out["detail"]


@pytest.mark.asyncio
async def test_set_status_verify_mismatch_and_sidecar_error(monkeypatch):
    monkeypatch.setattr(A, "get_settings", lambda: _settings())
    evals = iter([_Resp(payload={"result": "row clicked"})] * 4)
    pages = iter(
        [
            _Resp(payload={"text": "907100946\n\nActive"}),
            _Resp(payload={"text": "907100946\n\nActive"}),  # still active after toggle
        ]
    )

    class _C(_Client):
        async def post(self, url, **kw):
            if "eval" in url:
                return next(evals)
            return await super().post(url, **kw)

        async def get(self, url, **kw):
            if "page-text" in url:
                return next(pages)
            return await super().get(url, **kw)

    _patch_client(monkeypatch, _C([("navigate", _Resp())]))
    out = await A.set_campaign_status("pause")
    assert out["ok"] is False and "still reads" not in out["detail"] and "Toggled" in out["detail"]

    # sidecar down → httpx.HTTPError path
    _patch_client(monkeypatch, _Client([("navigate", httpx.HTTPError("conn refused"))]))
    out = await A.set_campaign_status("resume")
    assert out["ok"] is False and "Sidecar error" in out["detail"]


# ── notify_slack ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_notify_slack_response_url(monkeypatch):
    monkeypatch.setattr(A, "get_settings", lambda: _settings())
    posted = []

    class _C(_Client):
        async def post(self, url, **kw):
            posted.append((url, kw))
            return _Resp(200)

    _patch_client(monkeypatch, _C([]))
    await A.notify_slack("done", response_url="http://slack/resp")
    assert posted[0][0] == "http://slack/resp"
    assert posted[0][1]["json"]["response_type"] == "in_channel"


@pytest.mark.asyncio
async def test_notify_slack_fallbacks(monkeypatch):
    monkeypatch.setattr(A, "get_settings", lambda: _settings())
    slack = AsyncMock()
    monkeypatch.setattr(A, "_post_slack_text", slack)

    # response_url fails ≥300 → webhook fallback
    _patch_client(monkeypatch, _Client([("slack/resp", _Resp(500))]))
    await A.notify_slack("done", response_url="http://slack/resp")
    assert slack.await_args.kwargs["purpose"] == "ads-control"

    # response_url raises → webhook fallback
    slack.reset_mock()
    _patch_client(monkeypatch, _Client([("slack/resp", httpx.HTTPError("x"))]))
    await A.notify_slack("done", response_url="http://slack/resp")
    assert slack.await_count == 1

    # no response_url → straight to webhook
    slack.reset_mock()
    await A.notify_slack("done")
    assert slack.await_args.kwargs["text"] == "done"


# ── Marketing API path (rw_ads) ──────────────────────────────────────


class _ApiClient:
    """Minimal fake for the account-scoped Marketing API entity URL."""

    def __init__(self, gets, post=None):
        self._gets = list(gets)
        self._post = post if post is not None else _Resp(200, {"status": "PAUSED"})
        self.posts = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kw):
        return self._gets.pop(0)

    async def post(self, url, **kw):
        self.posts.append((url, kw))
        return self._post


def _api_patch(monkeypatch, client):
    monkeypatch.setattr(A, "_api_token", AsyncMock(return_value="tok"))
    monkeypatch.setattr(A.httpx, "AsyncClient", lambda **kw: client)
    monkeypatch.setattr(A, "get_settings", lambda: _settings())


@pytest.mark.asyncio
async def test_api_pause_happy(monkeypatch):
    client = _ApiClient(
        [_Resp(200, {"status": "ACTIVE"}), _Resp(200, {"status": "PAUSED"})],
        post=_Resp(200, {}),
    )
    _api_patch(monkeypatch, client)
    out = await A.set_campaign_status("pause")
    assert out["ok"] is True and out["status"] == "paused"
    assert "Marketing API" in out["detail"]
    # write went to the account-scoped entity with Rest.li partial update
    url, kw = client.posts[0]
    assert "/adAccounts/512642510/adCampaigns/907100946" in url
    assert kw["headers"]["X-RestLi-Method"] == "PARTIAL_UPDATE"
    assert kw["json"] == {"patch": {"$set": {"status": "PAUSED"}}}


@pytest.mark.asyncio
async def test_api_resume_sends_active(monkeypatch):
    client = _ApiClient(
        [_Resp(200, {"status": "PAUSED"}), _Resp(200, {"status": "ACTIVE"})],
        post=_Resp(200, {}),
    )
    _api_patch(monkeypatch, client)
    out = await A.set_campaign_status("resume")
    assert out["ok"] is True and out["status"] == "active"
    assert client.posts[0][1]["json"] == {"patch": {"$set": {"status": "ACTIVE"}}}


@pytest.mark.asyncio
async def test_api_already_in_state(monkeypatch):
    client = _ApiClient([_Resp(200, {"status": "PAUSED"})])
    _api_patch(monkeypatch, client)
    out = await A.set_campaign_status("pause")
    assert out == {"ok": True, "status": "paused", "detail": "Already paused"}
    assert client.posts == []


@pytest.mark.asyncio
async def test_api_completed_campaign_refused(monkeypatch):
    client = _ApiClient([_Resp(200, {"status": "COMPLETED"})])
    _api_patch(monkeypatch, client)
    out = await A.set_campaign_status("pause")
    assert out["ok"] is False and "completed" in out["detail"]
    assert client.posts == []


@pytest.mark.asyncio
async def test_api_403_forbidden_is_definitive(monkeypatch):
    client = _ApiClient([_Resp(403, {"message": "not configured"})])
    _api_patch(monkeypatch, client)
    out = await A.set_campaign_status("pause")
    assert out["ok"] is False and out["status"] == "forbidden"
    assert "Ad Accounts" in out["detail"]


@pytest.mark.asyncio
async def test_api_transport_failure_falls_back_to_sidecar(monkeypatch):
    monkeypatch.setattr(A, "_api_token", AsyncMock(side_effect=RuntimeError("no db")))
    monkeypatch.setattr(A, "get_settings", lambda: _settings())
    client = _Client(
        [
            ("navigate", _Resp()),
            ("page-text", _Resp(payload={"text": "907100946\n\nPaused"})),
        ]
    )
    _patch_client(monkeypatch, client)
    out = await A.set_campaign_status("pause")
    assert out == {"ok": True, "status": "paused", "detail": "Already paused"}


@pytest.mark.asyncio
async def test_api_5xx_falls_back_to_sidecar(monkeypatch):
    """API status read 503 → None → sidecar path answers."""
    monkeypatch.setattr(A, "_api_token", AsyncMock(return_value="tok"))
    monkeypatch.setattr(A, "get_settings", lambda: _settings())
    side = _Client(
        [
            ("navigate", _Resp()),
            ("page-text", _Resp(payload={"text": "907100946\n\nPaused"})),
        ]
    )
    api_gets = [_Resp(503, {"message": "down"})]

    class _Router:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **kw):
            if "adAccounts" in url:
                return api_gets.pop(0)
            return await side.get(url, **kw)

        async def post(self, url, **kw):
            if "adAccounts" in url:
                return _Resp(200, {})
            return await side.post(url, **kw)

    monkeypatch.setattr(A.httpx, "AsyncClient", lambda **kw: _Router())
    out = await A.set_campaign_status("pause")
    assert out == {"ok": True, "status": "paused", "detail": "Already paused"}
