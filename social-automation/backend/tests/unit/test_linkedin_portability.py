"""LinkedIn DMA portability service + sync task unit tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.services import linkedin_portability as lp


def _resp(status, json_body=None, headers=None):
    r = SimpleNamespace(status_code=status, headers=headers or {})
    r.json = lambda: json_body or {}
    r.text = "err-body"
    return r


# ── memberSnapshotData ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_member_snapshot_ok():
    client = SimpleNamespace(get=AsyncMock(return_value=_resp(200, {"elements": [{"x": 1}], "paging": {"total": 1}})))
    out = await lp.member_snapshot(client, "tok", domain="PROFILE")
    assert out["ok"] and out["data"]["elements"] == [{"x": 1}]
    _, kwargs = client.get.await_args
    assert kwargs["params"]["domain"] == "PROFILE"
    assert kwargs["headers"]["Linkedin-Version"] == "202312"


@pytest.mark.asyncio
async def test_member_snapshot_403_not_granted():
    client = SimpleNamespace(get=AsyncMock(return_value=_resp(403)))
    out = await lp.member_snapshot(client, "tok", domain="CONNECTIONS")
    assert out["ok"] is False and out["not_granted"] is True and out["status"] == 403


@pytest.mark.asyncio
async def test_member_snapshot_domain_paginates():
    page1 = _resp(
        200,
        {
            "elements": [{"n": 1}],
            "paging": {
                "total": 2,
                "links": [{"rel": "next", "href": "/rest/memberSnapshotData?q=criteria&start=50"}],
            },
        },
    )
    page2 = _resp(200, {"elements": [{"n": 2}], "paging": {"total": 2, "links": []}})
    client = SimpleNamespace(get=AsyncMock(side_effect=[page1, page2]))
    out = await lp.member_snapshot_domain(client, "tok", "SKILLS")
    assert out["ok"] and len(out["elements"]) == 2 and out["pages"] == 2
    # second call followed the next href
    assert client.get.await_count == 2
    second_url = client.get.await_args_list[1].args[0]
    assert "start=50" in second_url


@pytest.mark.asyncio
async def test_member_changelog_sends_start_time():
    client = SimpleNamespace(get=AsyncMock(return_value=_resp(200, {"elements": []})))
    out = await lp.member_changelog(client, "tok", start_time="2026-11-01T00:00:00Z")
    assert out["ok"]
    _, kwargs = client.get.await_args
    assert kwargs["params"]["startTime"] == "2026-11-01T00:00:00Z"
    assert kwargs["params"]["q"] == "memberAndApplication"


# ── Pages DP ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pages_feed_uses_postsByAuthor_and_cursor():
    client = SimpleNamespace(get=AsyncMock(return_value=_resp(200, {"elements": [], "metadata": {}})))
    out = await lp.pages_feed_page(client, "tok", "urn:li:organization:1", cursor="abc")
    assert out["ok"]
    _, kwargs = client.get.await_args
    assert kwargs["params"]["q"] == "postsByAuthor"
    assert kwargs["params"]["author"] == "urn:li:organization:1"
    assert kwargs["params"]["paginationCursor"] == "abc"
    assert kwargs["headers"]["Linkedin-Version"] == "202604"


@pytest.mark.asyncio
async def test_pages_organizations_params():
    client = SimpleNamespace(get=AsyncMock(return_value=_resp(200, {"elements": []})))
    out = await lp.pages_organizations(client, "tok", parent_org_id="108614163")
    assert out["ok"]
    _, kwargs = client.get.await_args
    assert kwargs["params"]["q"] == "parentOrganization"
    assert kwargs["params"]["parent"] == "108614163"


@pytest.mark.asyncio
async def test_pages_batch_get_builds_restli_list():
    client = SimpleNamespace(get=AsyncMock(return_value=_resp(200, {"results": {"urn:li:share:1": {"id": "x"}}})))
    out = await lp.pages_batch_get(client, "tok", "/dmaPosts", ["urn:li:share:1", "urn:li:share:2"])
    assert out == [{"id": "x"}]
    url = client.get.await_args.args[0]
    assert "ids=List(" in url and "urn%3Ali%3Ashare%3A1" in url


@pytest.mark.asyncio
async def test_pages_content_trend_raw_query():
    client = SimpleNamespace(get=AsyncMock(return_value=_resp(200, {"elements": []})))
    out = await lp.pages_content_trend(
        client,
        "tok",
        source_entity="urn:li:organizationalPage:1",
        metric_types="List(IMPRESSIONS)",
        start_ms=1,
        end_ms=2,
    )
    assert out["ok"]
    url = client.get.await_args.args[0]
    assert "q=trend" in url and "sourceEntity=" in url and "timeIntervals=" in url


@pytest.mark.asyncio
async def test_pages_403_not_granted():
    client = SimpleNamespace(get=AsyncMock(return_value=_resp(403)))
    out = await lp.pages_feed_page(client, "tok", "urn:li:organization:1")
    assert out["ok"] is False and out["not_granted"] is True


# ── sync task self-gating ──────────────────────────────────────────────


def _account(scopes, account_type="person", account_id="a6JQwITmH3"):
    return SimpleNamespace(
        id="acc-1",
        team_id="team-1",
        platform="linkedin",
        account_type=account_type,
        account_id=account_id,
        access_token_enc=b"x",
        scopes=scopes,
        status="active",
        meta_data={"author_urn": f"urn:li:{'person' if account_type == 'person' else 'organization'}:{account_id}"},
    )


@pytest.mark.asyncio
async def test_sync_skips_when_scopes_missing(monkeypatch):
    """No r_dma_* scopes recorded → zero API calls, skip result."""
    from app.worker.tasks import linkedin_portability_sync as t

    person = _account(["openid", "profile", "w_member_social"])

    class _Res:
        def scalars(self):
            return SimpleNamespace(all=lambda: [person])

    class _DB:
        async def execute(self, q):
            return _Res()

        def add(self, *a, **k):
            pass

        async def commit(self):
            pass

    class _Session:
        async def __aenter__(self):
            return _DB()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(t, "task_session", lambda: _Session())
    out = await t._run()
    assert out["member_dp"] == {"skip": "scope-not-granted"}
    assert out["pages_dp"] == {"skip": "scope-not-granted"}


# ── memberFollowersCount (analytics_sync) ──────────────────────────────


@pytest.mark.asyncio
async def test_member_followers_lifetime_and_daily():
    from app.services import analytics_sync as A

    calls = []

    async def _get(url, **kwargs):
        calls.append(url)
        if "q=me" in url:
            return _resp(200, {"elements": [{"followerCounts": {"lifetimeFollowerCount": 42}}]})
        return _resp(200, {"elements": [{"date": {"year": 2026}, "followerCount": 5}]})

    client = SimpleNamespace(get=AsyncMock(side_effect=_get))
    out = await A._fetch_member_followers(client, "tok")
    assert out["data"]["lifetime"]["elements"][0]["followerCounts"]["lifetimeFollowerCount"] == 42
    assert len(out["data"]["daily_30d"]) == 1
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_member_followers_403_returns_status():
    from app.services import analytics_sync as A

    client = SimpleNamespace(get=AsyncMock(return_value=_resp(403)))
    out = await A._fetch_member_followers(client, "tok")
    assert out["status"] == 403 and "data" not in out


# ── error / pagination arms ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_member_snapshot_http_error():
    client = SimpleNamespace(get=AsyncMock(side_effect=httpx.ConnectError("down")))
    out = await lp.member_snapshot(client, "tok", domain="PROFILE")
    assert out["ok"] is False and out["status"] == 0 and out["error"] == "ConnectError"


@pytest.mark.asyncio
async def test_member_snapshot_domain_second_page_denied():
    page1 = _resp(
        200,
        {
            "elements": [{"n": 1}],
            "paging": {"total": 2, "links": [{"rel": "next", "href": "/rest/memberSnapshotData?q=criteria&start=50"}]},
        },
    )
    client = SimpleNamespace(get=AsyncMock(side_effect=[page1, _resp(403)]))
    out = await lp.member_snapshot_domain(client, "tok", "SKILLS")
    assert out["ok"] is False and out["not_granted"] is True


@pytest.mark.asyncio
async def test_member_snapshot_domain_http_error_on_page_two():
    page1 = _resp(
        200,
        {
            "elements": [],
            "paging": {"total": 1, "links": [{"rel": "next", "href": "/rest/memberSnapshotData?start=50"}]},
        },
    )
    client = SimpleNamespace(get=AsyncMock(side_effect=[page1, httpx.ReadTimeout("t")]))
    out = await lp.member_snapshot_domain(client, "tok", "SKILLS")
    assert out["ok"] is False and out["status"] == 0 and out["error"] == "ReadTimeout"


@pytest.mark.asyncio
async def test_member_changelog_http_error_and_denied():
    client = SimpleNamespace(get=AsyncMock(side_effect=httpx.ConnectError("x")))
    out = await lp.member_changelog(client, "tok")
    assert out["ok"] is False and out["status"] == 0
    client2 = SimpleNamespace(get=AsyncMock(return_value=_resp(403)))
    out2 = await lp.member_changelog(client2, "tok")
    assert out2["ok"] is False and out2["not_granted"] is True


@pytest.mark.asyncio
async def test_pages_get_http_error_via_feed():
    client = SimpleNamespace(get=AsyncMock(side_effect=httpx.ConnectError("x")))
    out = await lp.pages_feed_page(client, "tok", "urn:li:organization:1")
    assert out["ok"] is False and out["status"] == 0 and out["error"] == "ConnectError"


@pytest.mark.asyncio
async def test_pages_batch_get_error_returns_partial_and_bare_map():
    # first batch fails → early return of whatever accumulated (empty)
    client = SimpleNamespace(get=AsyncMock(return_value=_resp(500)))
    out = await lp.pages_batch_get(client, "tok", "/dmaPosts", ["urn:li:share:1"])
    assert out == []
    # bare-map data shape (no "results" wrapper) → values extended
    client2 = SimpleNamespace(get=AsyncMock(return_value=_resp(200, {"urn:li:share:1": {"id": "a"}, "urn:li:share:2": "skip"})))
    out2 = await lp.pages_batch_get(client2, "tok", "/dmaPosts", ["urn:li:share:1", "urn:li:share:2"])
    assert out2 == [{"id": "a"}]


@pytest.mark.asyncio
async def test_member_snapshot_without_domain():
    client = SimpleNamespace(get=AsyncMock(return_value=_resp(200, {"elements": []})))
    out = await lp.member_snapshot(client, "tok")
    assert out["ok"]
    _, kwargs = client.get.await_args
    assert "domain" not in kwargs["params"]


@pytest.mark.asyncio
async def test_member_snapshot_domain_hits_max_pages():
    nxt = {
        "elements": [],
        "paging": {"total": 9, "links": [{"rel": "next", "href": "/rest/memberSnapshotData?start=50"}]},
    }
    client = SimpleNamespace(get=AsyncMock(side_effect=[_resp(200, nxt), _resp(200, nxt)]))
    out = await lp.member_snapshot_domain(client, "tok", "SKILLS", max_pages=2)
    assert out["ok"] and out["pages"] == 2


@pytest.mark.asyncio
async def test_pages_batch_get_skips_non_dict_data():
    client = SimpleNamespace(
        get=AsyncMock(
            side_effect=[
                _resp(200, ["not", "a", "dict"]),
                _resp(200, {"results": {"urn:li:share:9": {"id": "z"}}}),
            ]
        )
    )
    out = await lp.pages_batch_get(client, "tok", "/dmaPosts", ["urn:li:share:1", "urn:li:share:2"], chunk=1)
    assert out == [{"id": "z"}]


# ── _member_dp diff-alert + redis-absent arcs ──────────────────────────


class _FakeRedis:
    def __init__(self, store=None):
        self.store = store if store is not None else {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value):
        self.store[key] = value


class _AddDB:
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)


def _member_dp_mocks(monkeypatch, t, redis_obj):
    monkeypatch.setattr(t, "_redis", lambda: redis_obj)
    monkeypatch.setattr(
        lp,
        "member_snapshot_domain",
        AsyncMock(return_value={"ok": True, "elements": [{"n": 1}], "total": 1, "pages": 1}),
    )
    monkeypatch.setattr(lp, "member_changelog", AsyncMock(return_value={"ok": True, "data": {"elements": []}}))


@pytest.mark.asyncio
async def test_member_dp_diff_alert_changed_and_failure(monkeypatch):
    """prev hash != new digest → changed_domains → Telegram alert; alert
    failure is swallowed (best-effort)."""
    import app.services.slack_notifications as SN
    from app.worker.tasks import linkedin_portability_sync as t

    person = _account(["r_dma_portability_self_serve"])
    # PROFILE seeded stale → changed-domain alert; SKILLS seeded with the
    # correct digest → unchanged arc (prev == digest → next domain)
    import hashlib
    import json

    digest = hashlib.sha256(json.dumps([{"n": 1}], sort_keys=True, default=str).encode()).hexdigest()[:16]
    store = {"linkedin:dma:hash:PROFILE": "stale", "linkedin:dma:hash:SKILLS": digest}
    _member_dp_mocks(monkeypatch, t, _FakeRedis(store))
    alert = AsyncMock()
    monkeypatch.setattr(SN, "send_telegram_alert", alert)

    out = await t._member_dp(_AddDB(), person, SimpleNamespace(), "tok")
    assert out["domains"] == len(lp.MEMBER_DOMAINS)
    alert.assert_awaited()
    assert "PROFILE" in alert.await_args.args[1]

    # alert raising → logged, not propagated (fresh stale seed so the
    # domain diffs again and the alert path re-executes)
    monkeypatch.setattr(t, "_redis", lambda: _FakeRedis({"linkedin:dma:hash:PROFILE": "stale"}))
    alert.side_effect = RuntimeError("tg down")
    out = await t._member_dp(_AddDB(), person, SimpleNamespace(), "tok")
    assert out["domains"] == len(lp.MEMBER_DOMAINS)


@pytest.mark.asyncio
async def test_member_dp_without_redis_skips_diff_block(monkeypatch):
    from app.worker.tasks import linkedin_portability_sync as t

    person = _account(["r_dma_portability_self_serve"])
    _member_dp_mocks(monkeypatch, t, None)
    out = await t._member_dp(_AddDB(), person, SimpleNamespace(), "tok")
    assert out["domains"] == len(lp.MEMBER_DOMAINS)


# ── _run: member-dp expiry alert failure swallowed ─────────────────────


@pytest.mark.asyncio
async def test_run_member_dp_expiry_alert_failure(monkeypatch):
    import app.services.slack_notifications as SN
    from app.worker.tasks import linkedin_portability_sync as t

    person = _account(["r_dma_portability_self_serve"])

    class _Res:
        def scalars(self):
            return SimpleNamespace(all=lambda: [person])

    class _DB:
        async def execute(self, q):
            return _Res()

        def add(self, *a, **k):
            pass

        async def commit(self):
            pass

    class _Session:
        async def __aenter__(self):
            return _DB()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(t, "task_session", lambda: _Session())
    monkeypatch.setattr(t, "_member_dp_secret", AsyncMock(return_value="raw-dp-token"))
    monkeypatch.setattr(t, "_member_dp", AsyncMock(return_value={"pending-grant": "member-dp"}))
    monkeypatch.setattr(SN, "send_telegram_alert", AsyncMock(side_effect=RuntimeError("tg down")))

    out = await t._run()
    assert out["member_dp"] == {"pending-grant": "member-dp"}
    assert out["pages_dp"] == {"skip": "scope-not-granted"}
