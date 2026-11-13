"""Tests for the linkedin_portability_sync celery task — self-gating,
snapshot events, changelog cursor, and Pages DP hydration."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.worker.tasks import linkedin_portability_sync as LPS


def _account(**kw):
    base = dict(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        platform="linkedin",
        status="active",
        account_type="person",
        scopes=[],
        access_token_enc=b"enc",
        account_id="12345",
        meta_data={},
    )
    base.update(kw)
    return SimpleNamespace(**base)


class _Res:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return self

    def all(self):
        return self._items


class _DB:
    def __init__(self, accounts):
        self.accounts = accounts
        self.added = []
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt):
        return _Res(self.accounts)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


class _FakeRedis:
    def __init__(self):
        self.kv = {}

    def get(self, k):
        return self.kv.get(k)

    def set(self, k, v, **kw):
        self.kv[k] = v
        return True


@pytest.fixture
def wired(monkeypatch):
    """Fake db/session/redis/decrypt; lp.* functions stay AsyncMock-able."""
    state = {"db": _DB([]), "redis": _FakeRedis()}
    monkeypatch.setattr(LPS, "task_session", lambda: state["db"])
    monkeypatch.setattr(LPS, "decrypt_token", lambda b: "tok")
    monkeypatch.setattr(LPS, "_redis", lambda: state["redis"])
    return state


# ── _has_scope / _org_ids / _event helpers ────────────────────────────


def test_has_scope_empty_list_probes_anyway():
    assert LPS._has_scope(_account(scopes=[]), LPS.MEMBER_SCOPE) is True
    assert LPS._has_scope(_account(scopes=["r_dma_portability_self_serve"]), LPS.MEMBER_SCOPE) is True
    assert LPS._has_scope(_account(scopes=["other"]), LPS.MEMBER_SCOPE) is False


def test_org_ids_author_urn_and_fallback():
    org_urn, page_urn = LPS._org_ids(_account(meta_data={"author_urn": "urn:li:organization:999"}))
    assert org_urn == "urn:li:organization:999"
    assert page_urn == "urn:li:organizationalPage:999"
    org_urn, page_urn = LPS._org_ids(_account(account_id="777", meta_data={}))
    assert org_urn == "urn:li:organization:777"
    assert page_urn == "urn:li:organizationalPage:777"


def test_event_shape():
    acc = _account()
    ev = LPS._event(acc, "linkedin_portability_member", {"a": 1})
    assert ev.platform == "linkedin" and ev.event_type == "linkedin_portability_member"
    assert ev.team_id == acc.team_id and ev.meta_data == {"a": 1}


def test_redis_failure_returns_none(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def _blocked(name, *a, **kw):
        if name == "redis":
            raise ImportError("no redis")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    assert LPS._redis() is None


# ── _run orchestration ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_no_linkedin_accounts(wired):
    wired["db"] = _DB([])
    out = await LPS._run()
    assert out == {"skip": "no-linkedin-accounts"}


@pytest.mark.asyncio
async def test_run_scopes_not_granted_skips_both(wired, monkeypatch):
    person = _account(scopes=["w_member_social"], account_type="person")
    org = _account(scopes=["w_organization_social"], account_type="organization", account_id="9")
    wired["db"] = _DB([person, org])
    spy = AsyncMock()
    monkeypatch.setattr(LPS.lp, "member_snapshot_domain", spy)
    out = await LPS._run()
    assert out["member_dp"] == {"skip": "scope-not-granted"}
    assert out["pages_dp"] == {"skip": "scope-not-granted"}
    spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_member_dp_pending_grant(wired, monkeypatch):
    person = _account(scopes=[LPS.MEMBER_SCOPE], account_type="person")
    wired["db"] = _DB([person])
    monkeypatch.setattr(
        LPS.lp,
        "member_snapshot_domain",
        AsyncMock(return_value={"ok": False, "not_granted": True, "status": 403}),
    )
    out = await LPS._run()
    assert out["member_dp"] == {"pending-grant": "member-dp", "domains_done": 1}
    assert out["pages_dp"]["skip"] == "scope-not-granted"
    assert wired["db"].commits == 1


@pytest.mark.asyncio
async def test_run_member_dp_exception_isolated(wired, monkeypatch):
    person = _account(scopes=[LPS.MEMBER_SCOPE], account_type="person")
    wired["db"] = _DB([person])
    monkeypatch.setattr(
        LPS.lp,
        "member_snapshot_domain",
        AsyncMock(side_effect=RuntimeError("boom")),
    )
    out = await LPS._run()
    assert "RuntimeError" in out["member_dp"]["error"]


# ── _member_dp ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_member_dp_happy_path_with_changelog_cursor(wired, monkeypatch):
    person = _account()
    db = wired["db"]
    wired["redis"].kv[LPS.CHANGELOG_CURSOR_KEY] = "2026-01-01T00:00:00Z"

    async def _domain(client, token, domain):
        # oversized element list → truncated_domains arm
        return {"ok": True, "elements": [{"n": i} for i in range(LPS.MAX_ELEMENTS_STORE + 5)], "total": 999, "pages": 3}

    monkeypatch.setattr(LPS.lp, "member_snapshot_domain", _domain)
    monkeypatch.setattr(
        LPS.lp,
        "member_changelog",
        AsyncMock(return_value={"ok": True, "data": {"elements": [{"processedAt": "2026-02-01T00:00:00Z"}, {"processedAt": "2026-03-01T00:00:00Z"}]}}),
    )
    out = await LPS._member_dp(db, person, client=None)
    assert out["domains"] == len(LPS.lp.MEMBER_DOMAINS)
    assert out["truncated"]
    types = {e.event_type for e in db.added}
    assert types == {"linkedin_portability_member", "linkedin_portability_changelog"}
    cl_event = next(e for e in db.added if e.event_type == "linkedin_portability_changelog")
    assert cl_event.meta_data["cursor_in"] == "2026-01-01T00:00:00Z"
    assert cl_event.meta_data["cursor_out"] == "2026-03-01T00:00:00Z"
    assert wired["redis"].kv[LPS.CHANGELOG_CURSOR_KEY] == "2026-03-01T00:00:00Z"


@pytest.mark.asyncio
async def test_member_dp_domain_error_non_denied_continues(wired, monkeypatch):
    person = _account()
    db = wired["db"]

    async def _domain(client, token, domain):
        if domain == LPS.lp.MEMBER_DOMAINS[0]:
            return {"ok": False, "status": 500, "error": "boom"}
        return {"ok": True, "elements": [], "total": 0, "pages": 1}

    monkeypatch.setattr(LPS.lp, "member_snapshot_domain", _domain)
    monkeypatch.setattr(LPS.lp, "member_changelog", AsyncMock(return_value={"ok": False, "status": 500}))
    out = await LPS._member_dp(db, person, client=None)
    assert out["domains"] == len(LPS.lp.MEMBER_DOMAINS)
    cl_event = next(e for e in db.added if e.event_type == "linkedin_portability_changelog")
    assert cl_event.meta_data["error"] == 500


@pytest.mark.asyncio
async def test_member_dp_changelog_not_granted(wired, monkeypatch):
    person = _account()
    monkeypatch.setattr(
        LPS.lp,
        "member_snapshot_domain",
        AsyncMock(return_value={"ok": True, "elements": [], "total": 0, "pages": 1}),
    )
    monkeypatch.setattr(
        LPS.lp,
        "member_changelog",
        AsyncMock(return_value={"ok": False, "not_granted": True, "status": 403}),
    )
    out = await LPS._member_dp(wired["db"], person, client=None)
    assert out == {"pending-grant": "member-dp"}
    assert wired["db"].added == []


# ── _pages_dp ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pages_dp_full_path(wired, monkeypatch):
    org = _account(account_type="organization", account_id="555", meta_data={"author_urn": "urn:li:organization:555"})
    db = wired["db"]
    monkeypatch.setattr(
        LPS.lp,
        "pages_organizations",
        AsyncMock(return_value={"ok": True, "data": {"elements": [{"id": "org1"}]}}),
    )
    monkeypatch.setattr(
        LPS.lp,
        "pages_feed_page",
        AsyncMock(
            return_value={
                "ok": True,
                "data": {
                    "elements": [{"id": "urn:li:share:1"}, {"id": "urn:li:share:2"}, {"bad": True}, {"id": "not-a-urn"}],
                    "metadata": {"nextPaginationCursor": "cur"},
                },
            }
        ),
    )
    monkeypatch.setattr(LPS.lp, "pages_batch_get", AsyncMock(side_effect=[[{"p": 1}], [{"m": 1}]]))
    monkeypatch.setattr(
        LPS.lp,
        "pages_content_trend",
        AsyncMock(return_value={"ok": True, "data": {"elements": [{"metric": "IMPRESSIONS"}]}}),
    )
    out = await LPS._pages_dp(db, org, client=None)
    assert out == {"posts_seen": 2}
    assert db.commits == 0  # commit happens in _run
    ev = db.added[0]
    assert ev.event_type == "linkedin_portability_pages"
    assert ev.meta_data["feed"]["count"] == 2
    assert ev.meta_data["feed"]["newest_cursor"] == "cur"
    assert ev.meta_data["posts"] == [{"p": 1}]
    assert ev.meta_data["social_metadata"] == [{"m": 1}]
    assert ev.meta_data["analytics_trend"] == [{"metric": "IMPRESSIONS"}]
    assert "organizations" in ev.meta_data


@pytest.mark.asyncio
async def test_pages_dp_orgs_and_feed_errors_non_granted(wired, monkeypatch):
    org = _account(account_type="organization", account_id="556")
    db = wired["db"]
    monkeypatch.setattr(LPS.lp, "pages_organizations", AsyncMock(return_value={"ok": False, "status": 500}))
    monkeypatch.setattr(LPS.lp, "pages_feed_page", AsyncMock(return_value={"ok": False, "status": 500}))
    monkeypatch.setattr(LPS.lp, "pages_content_trend", AsyncMock(return_value={"ok": False, "status": 500}))
    out = await LPS._pages_dp(db, org, client=None)
    assert out == {"posts_seen": 0}
    ev = db.added[0]
    assert ev.meta_data["organizations_error"] == 500
    assert ev.meta_data["feed_error"] == 500
    assert ev.meta_data["analytics_error"] == 500


@pytest.mark.asyncio
async def test_pages_dp_pending_grants(wired, monkeypatch):
    org = _account(account_type="organization", account_id="557")
    monkeypatch.setattr(LPS.lp, "pages_organizations", AsyncMock(return_value={"ok": False, "not_granted": True}))
    out = await LPS._pages_dp(wired["db"], org, client=None)
    assert out == {"pending-grant": "pages-dp"}

    monkeypatch.setattr(LPS.lp, "pages_organizations", AsyncMock(return_value={"ok": True, "data": {"elements": []}}))
    monkeypatch.setattr(LPS.lp, "pages_feed_page", AsyncMock(return_value={"ok": False, "not_granted": True}))
    out = await LPS._pages_dp(wired["db"], org, client=None)
    assert out == {"pending-grant": "pages-dp"}


@pytest.mark.asyncio
async def test_pages_dp_feed_without_urns_skips_hydrate(wired, monkeypatch):
    org = _account(account_type="organization", account_id="558")
    db = wired["db"]
    monkeypatch.setattr(LPS.lp, "pages_organizations", AsyncMock(return_value={"ok": True, "data": {"elements": []}}))
    monkeypatch.setattr(LPS.lp, "pages_feed_page", AsyncMock(return_value={"ok": True, "data": {"elements": [{"id": "junk"}], "metadata": {}}}))
    monkeypatch.setattr(LPS.lp, "pages_content_trend", AsyncMock(return_value={"ok": False, "status": 404}))
    batch = AsyncMock()
    monkeypatch.setattr(LPS.lp, "pages_batch_get", batch)
    out = await LPS._pages_dp(db, org, client=None)
    assert out == {"posts_seen": 0}
    batch.assert_not_awaited()
    assert db.added[0].meta_data["analytics_error"] == 404


@pytest.mark.asyncio
async def test_run_pages_dp_via_org_fallback_and_exception(wired, monkeypatch):
    person = _account(scopes=["w_member_social"], account_type="person")
    org = _account(
        scopes=[LPS.PAGES_SCOPE],
        account_type="organization",
        account_id="560",
        meta_data={"author_urn": "urn:li:organization:560"},
    )
    wired["db"] = _DB([person, org])
    monkeypatch.setattr(LPS.lp, "pages_organizations", AsyncMock(side_effect=RuntimeError("pages boom")))
    out = await LPS._run()
    assert out["member_dp"] == {"skip": "scope-not-granted"}
    assert "RuntimeError" in out["pages_dp"]["error"]
    assert wired["db"].commits == 1


def test_redis_success_path(monkeypatch):
    """_redis returns a client when redis + settings are available."""
    import redis as real_redis

    monkeypatch.setattr(real_redis.Redis, "from_url", classmethod(lambda cls, url, **kw: "client"))
    assert LPS._redis() == "client"


def test_task_wrapper_runs_coro(monkeypatch):
    seen = {}

    def _fake_run_async(coro):
        seen["coro"] = coro
        coro.close()
        return {"ok": True}

    monkeypatch.setattr(LPS, "run_async", _fake_run_async)
    assert LPS.sync_linkedin_portability() == {"ok": True}
    assert seen["coro"].cr_code is LPS._run.__code__
