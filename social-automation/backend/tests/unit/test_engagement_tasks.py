"""Engagement worker tasks — dispatch sweep, feed ingestors, tiers, ledger.

Real task code; fakes only at the boundaries: async_session_maker, the
browser/sidecar HTTP (httpx.MockTransport), DMR, Slack/Telegram and actors.
"""

from __future__ import annotations

import datetime as dt
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

import app.db.session as SESSION
import app.worker._async as WA
from app.worker.tasks import engagement as ENG
from app.worker.tasks import engagement_archive as ARCH
from app.worker.tasks import engagement_ingest as ING_X
from app.worker.tasks import engagement_ingest_fb as ING_FB
from app.worker.tasks import engagement_ingest_ig as ING_IG
from app.worker.tasks import engagement_ingest_li as ING_LI
from app.worker.tasks import engagement_ingest_tt as ING_TT
from app.worker.tasks import engagement_tiers as TIERS

_RealClient = httpx.AsyncClient


class _Res:
    def __init__(self, rows=None, rowcount=0):
        self._rows = list(rows or [])
        self.rowcount = rowcount

    def scalars(self):
        return SimpleNamespace(
            all=lambda: list(self._rows),
            first=lambda: self._rows[0] if self._rows else None,
        )

    def all(self):
        return list(self._rows)


class _DB:
    def __init__(self, results=()):
        self.results = list(results)
        self.added = []
        self.commits = 0

    async def execute(self, stmt):
        r = self.results.pop(0) if self.results else []
        return r if isinstance(r, _Res) else _Res(r)

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        self.commits += 1


class _ACM:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *a):
        return False


def _use_db(monkeypatch, db):
    monkeypatch.setattr(SESSION, "async_session_maker", lambda: _ACM(db))
    return db


def _mock_http(monkeypatch, mod, handler):
    calls: list[httpx.Request] = []

    def _h(req):
        calls.append(req)
        return handler(req)

    def _factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(_h)
        return _RealClient(*a, **kw)

    monkeypatch.setattr(mod.httpx, "AsyncClient", _factory)
    return calls


async def _nosleep(*_a, **_k):
    return None


def _bridge(result, start_fails=False):
    def h(req):
        if req.url.path == "/session/start" and start_fails:
            raise httpx.ConnectError("busy", request=req)
        if req.url.path == "/session/evaluate":
            return httpx.Response(200, json={"result": result})
        return httpx.Response(200, json={})

    return h


# ── Celery wrappers ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "task,args",
    [
        (ENG.dispatch_engagements, (3,)),
        (ARCH.compact_ledger, (7,)),
        (ING_X.ingest_x_feed, ()),
        (ING_FB.ingest_fb_groups, ()),
        (ING_IG.ingest_ig_feed, ()),
        (ING_LI.ingest_linkedin_feed, ()),
        (ING_TT.ingest_tiktok_tags, ()),
        (TIERS.update_connection_tiers, ()),
    ],
)
def test_task_wrappers_submit_coroutine(monkeypatch, task, args):
    seen = []

    def _fake_run_async(coro):
        seen.append(coro.cr_code.co_name)
        coro.close()
        return {"ran": True}

    monkeypatch.setattr(WA, "run_async", _fake_run_async)
    assert task(*args) == {"ran": True}
    assert seen and seen[0] in ("_run", "_x")


# ── dispatch_engagements._run ──────────────────────────────────────────


def _row_cand(**kw):
    now = dt.datetime(2026, 10, 1, 12, tzinfo=dt.UTC)
    base = dict(
        platform="twitter", author_handle="h", author_name="N", post_id="p1",
        post_text="serverless edge", posted_at=now - dt.timedelta(minutes=5),
        ingested_at=now, connection_tier="medium", is_followed=True,
        is_connection=False, in_crm=False, scored=False, meta_data={"viral": False},
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _row_action(**kw):
    base = dict(platform="twitter", action="like", target_post_id="p1",
                target_author="h", score=9.0, comment_draft=None,
                status="pending", acted_at=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _patch_actors(monkeypatch, result_by_platform):
    from app.services.engagement_actors import (
        fb_actor,
        ig_actor,
        linkedin_actor,
        threads_actor,
        tiktok_actor,
        x_actor,
    )

    mods = {"twitter": x_actor, "threads": threads_actor, "linkedin": linkedin_actor,
            "instagram": ig_actor, "facebook": fb_actor, "tiktok": tiktok_actor}
    for plat, mod in mods.items():
        monkeypatch.setattr(mod, "act", AsyncMock(return_value=result_by_platform.get(plat, {"ok": True})))


@pytest.mark.asyncio
async def test_dispatch_full_sweep(monkeypatch):
    import app.services.dmr as DMR
    import app.services.engagement as SVC
    import app.services.slack_notifications as SN
    import app.services.telegram_notify as TN

    fresh = _row_cand(posted_at=dt.datetime(2026, 10, 1, 13, tzinfo=dt.UTC))  # future → age clamps to 0
    drafted = _row_action(platform="linkedin", action="comment", status="pending_human_gate", target_post_id="p1")
    long_draft = _row_action(platform="mastodon", action="comment", status="pending_human_gate", target_post_id="p2")
    no_cand = _row_action(platform="linkedin", action="comment", status="pending_human_gate", target_post_id="gone")
    empty = _row_action(platform="tiktok", action="comment", status="pending_human_gate", target_post_id="p3")
    cand_li = _row_cand(platform="linkedin", post_id="p1", author_name="")
    cand_m = _row_cand(platform="mastodon", post_id="p2")
    cand_t = _row_cand(platform="tiktok", post_id="p3")

    ok_like = _row_action(platform="twitter")
    skip_like = _row_action(platform="instagram", target_author="ig")
    fail_like = _row_action(platform="facebook", target_author="fb")
    no_actor = _row_action(platform="mastodon")

    db = _use_db(monkeypatch, _DB([
        [fresh],
        [drafted, long_draft, no_cand, empty],
        [cand_li], [cand_m], [], [cand_t],
        [ok_like, skip_like, fail_like, no_actor],
    ]))
    queued = []
    monkeypatch.setattr(SVC, "queue_engagement", AsyncMock(side_effect=lambda c, d: queued.append(c) or {}))
    replies = iter([{"text": '"Sharp take — what about D1?"'}, {"text": "y" * 200}, {"text": "  "}])
    dmr = AsyncMock(side_effect=lambda *a, **k: next(replies))
    monkeypatch.setattr(DMR, "call_dmr_chat", dmr)
    slack = AsyncMock(return_value=(True, None, None))
    monkeypatch.setattr(SN, "_post_slack_text", slack)
    tg = AsyncMock(side_effect=[True, RuntimeError("tg down")])
    monkeypatch.setattr(TN, "send_telegram", tg)
    monkeypatch.setenv("SLACK_ENGAGEMENT_WEBHOOK_URL", "https://hooks.example/x")
    monkeypatch.setenv("SLACK_ENGAGEMENT_CHANNEL_ID", "C1")
    _patch_actors(monkeypatch, {
        "instagram": {"error": "busy", "skip": True},
        "facebook": {"error": "x" * 100},
    })

    out = await ENG._run(5)

    # 1. scoring
    assert fresh.scored is True
    assert queued[0].posted_seconds_ago == 0
    assert queued[0].meta_data is None  # viral duet_flag path not wired to dispatch yet
    # 2. drafts — quotes stripped, long draft kept, blank → None
    assert drafted.comment_draft == "Sharp take — what about D1?"
    assert long_draft.comment_draft == "y" * 200
    assert empty.comment_draft is None
    assert no_cand.comment_draft is None
    assert dmr.await_count == 3
    prompt = dmr.await_args_list[0].args[0]
    assert "h's post" in prompt  # author_name empty → handle
    # gate pings: only rows with drafts; deep link only for known platforms
    assert slack.await_count == 2
    t0 = slack.await_args_list[0].kwargs
    assert t0["webhook_url"] == "https://hooks.example/x"
    assert t0["channel_id"] == "C1"
    assert "linkedin://feed" in t0["text"]
    t1 = slack.await_args_list[1].kwargs["text"]
    assert "y" * 140 + "…" in t1
    assert "Open source post" not in t1
    assert tg.await_count == 2
    # 3. dispatch
    assert ok_like.status == "acted" and ok_like.acted_at is not None
    assert skip_like.status == "pending_actor"
    assert fail_like.status == "failed"
    assert no_actor.status == "pending_actor"
    assert out["scored"] == 1
    assert out["drafted"] == 4
    assert out["dispatched"] == ["twitter:like@h"]
    assert out["failed"] == ["ig:busy", "fb:" + "x" * 60, "mastodon:no-actor"]
    assert db.commits == 3


@pytest.mark.asyncio
async def test_dispatch_alert_failure_is_swallowed(monkeypatch):
    import app.services.dmr as DMR
    import app.services.slack_notifications as SN

    a = _row_action(platform="linkedin", action="comment", status="pending_human_gate")
    _use_db(monkeypatch, _DB([[], [a], [_row_cand(platform="linkedin")], []]))
    monkeypatch.setattr(DMR, "call_dmr_chat", AsyncMock(return_value={"text": "draft"}))
    monkeypatch.setattr(SN, "_post_slack_text", AsyncMock(side_effect=RuntimeError("slack down")))
    monkeypatch.delenv("SLACK_ENGAGEMENT_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("SLACK_ENGAGEMENT_CHANNEL_ID", raising=False)
    _patch_actors(monkeypatch, {})
    out = await ENG._run(2)
    assert a.comment_draft == "draft"
    assert out == {"scored": 0, "drafted": 1, "dispatched": [], "failed": []}


@pytest.mark.asyncio
async def test_dispatch_nothing_to_do(monkeypatch):
    _use_db(monkeypatch, _DB([[], [], []]))
    _patch_actors(monkeypatch, {})
    out = await ENG._run(1)
    assert out == {"scored": 0, "drafted": 0, "dispatched": [], "failed": []}


# ── X ingest ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ingest_x_no_account_and_empty_token(monkeypatch):
    import app.core.security as sec

    _use_db(monkeypatch, _DB([[]]))
    assert await ING_X._x() == {"error": "no active twitter account"}
    monkeypatch.setattr(sec, "decrypt_token", lambda v: "")
    _use_db(monkeypatch, _DB([[SimpleNamespace(access_token_enc="e", meta_data=None)]]))
    assert await ING_X._x() == {"error": "empty token"}


@pytest.mark.asyncio
async def test_ingest_x_timeline(monkeypatch):
    import app.core.security as sec

    monkeypatch.setattr(sec, "decrypt_token", lambda v: "tok")
    acct = SimpleNamespace(access_token_enc="e", meta_data={"user_id": "u9"})
    tweets = [
        {"id": "t1", "author_id": "a1", "text": "hello", "created_at": "2026-10-01T10:00:00Z"},
        {"id": "t2", "created_at": "2026-10-01T11:00:00Z"},
    ]
    calls = _mock_http(monkeypatch, ING_X, lambda r: httpx.Response(200, json={"data": tweets}))
    db = _use_db(monkeypatch, _DB([[acct], [], [SimpleNamespace()]]))
    assert await ING_X._x() == {"new_candidates": 1}
    assert calls[0].url.path == "/2/users/u9/timelines/reverse_chronological"
    row = db.added[0]
    assert (row.platform, row.author_handle, row.post_id, row.post_text) == ("twitter", "a1", "t1", "hello")
    assert row.posted_at == dt.datetime(2026, 10, 1, 10, tzinfo=dt.UTC)

    _mock_http(monkeypatch, ING_X, lambda r: httpx.Response(429))
    _use_db(monkeypatch, _DB([[acct]]))
    assert await ING_X._x() == {"error": "x timeline 429"}


# ── Facebook groups ingest ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ingest_fb_no_groups(monkeypatch):
    monkeypatch.setenv("FACEBOOK_GROUP_URLS", " , ")
    assert await ING_FB._run() == {"skipped": "no FACEBOOK_GROUP_URLS configured"}


@pytest.mark.asyncio
async def test_ingest_fb_browser_busy(monkeypatch):
    monkeypatch.setenv("FACEBOOK_GROUP_URLS", "https://fb.com/groups/a")
    _mock_http(monkeypatch, ING_FB, _bridge("[]", start_fails=True))
    assert await ING_FB._run() == {"skipped": "browser busy"}


@pytest.mark.asyncio
async def test_ingest_fb_groups(monkeypatch):
    monkeypatch.setattr(ING_FB.asyncio, "sleep", _nosleep)
    monkeypatch.setenv("FACEBOOK_GROUP_URLS", "https://fb.com/groups/a,https://fb.com/groups/b?x=1")
    posts = [
        {"href": "/groups/a/posts/111", "author": "Ana", "txt": "t" * 50},
        {"href": "/permalink.php?story_fbid=222&id=1", "author": "Bo", "txt": "u"},
        {"href": "/groups/a/posts/333", "author": "Dup", "txt": "dup"},
        {"href": "", "author": "", "txt": ""},
        {"href": "https://x/other-link", "author": "Cy"},
    ]
    calls = _mock_http(monkeypatch, ING_FB, _bridge(json.dumps(posts)))
    # per group: lookups for 111, 222, 333 (dup), other-link; 2nd group all dups
    db = _use_db(monkeypatch, _DB([[], [], [object()], [], [object()], [object()], [object()], [object()]]))
    out = await ING_FB._run()
    assert out == {"new_candidates": 3, "groups": 2}
    assert [r.post_id for r in db.added] == ["111", "222&id=1", "https://x/other-link"]
    assert db.added[0].author_name == "Ana" and db.added[0].platform == "facebook"
    navs = [json.loads(c.content)["url"] for c in calls if c.url.path == "/session/navigate"]
    assert navs == [
        "https://fb.com/groups/a?sorting_setting=CHRONOLOGICAL",
        "https://fb.com/groups/b?x=1&sorting_setting=CHRONOLOGICAL",
    ]


@pytest.mark.asyncio
async def test_ingest_fb_empty_eval(monkeypatch):
    monkeypatch.setattr(ING_FB.asyncio, "sleep", _nosleep)
    monkeypatch.setenv("FACEBOOK_GROUP_URLS", "https://fb.com/groups/a")
    _mock_http(monkeypatch, ING_FB, _bridge(None))
    _use_db(monkeypatch, _DB())
    assert await ING_FB._run() == {"new_candidates": 0, "groups": 1}


# ── Instagram feed ingest ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ingest_ig_busy(monkeypatch):
    _mock_http(monkeypatch, ING_IG, _bridge("[]", start_fails=True))
    assert await ING_IG._run() == {"skipped": "browser busy: ConnectError"}


@pytest.mark.asyncio
async def test_ingest_ig_feed(monkeypatch):
    monkeypatch.setattr(ING_IG.asyncio, "sleep", _nosleep)
    posts = [
        {"href": "/p/ABCDEF/", "author": "ana", "txt": "hi", "time": "2026-10-01T09:00:00Z"},
        {"href": "/reel/QWERTY/", "author": "bo", "txt": "yo", "time": "garbage"},
        {"href": "/p/XYZ12/", "author": "dup"},
        {"href": "/p/abc/", "author": "short"},
    ]
    _mock_http(monkeypatch, ING_IG, _bridge(json.dumps(posts)))
    db = _use_db(monkeypatch, _DB([[], [], [object()]]))
    assert await ING_IG._run() == {"new_candidates": 2}
    a, b = db.added
    assert (a.post_id, a.author_handle) == ("ABCDEF", "ana")
    assert a.posted_at == dt.datetime(2026, 10, 1, 9, tzinfo=dt.UTC)
    assert b.post_id == "QWERTY"
    assert b.posted_at.tzinfo is not None  # parse failure → now()


# ── LinkedIn feed ingest ───────────────────────────────────────────────


def _li(health, posts_payload):
    def h(req):
        if req.url.path == "/health":
            return httpx.Response(200, json=health)
        body = json.loads(req.content or b"{}")
        if req.url.path == "/debug/eval" and "feed-shared-update-v2" in body.get("expr", ""):
            return httpx.Response(200, json=posts_payload)
        return httpx.Response(200, json={"result": "ok"})

    return h


@pytest.mark.asyncio
async def test_ingest_li_rate_limited_and_no_session(monkeypatch):
    _mock_http(monkeypatch, ING_LI, _li({"rate_limited": True, "rate_limit_until": "soon"}, {}))
    assert await ING_LI._run() == {"skipped": "rate_limited", "until": "soon"}
    _mock_http(monkeypatch, ING_LI, _li({"has_session": False}, {}))
    assert await ING_LI._run() == {"skipped": "no_session"}


@pytest.mark.asyncio
async def test_ingest_li_feed(monkeypatch):
    import asyncio

    monkeypatch.setattr(asyncio, "sleep", _nosleep)
    posts = [
        {"urn": "urn:li:activity:1", "actor": "Ana", "handle": "https://li/in/ana", "txt": "a" * 40, "age": "3h •"},
        {"urn": "", "handle": "https://li/in/bo", "txt": "b" * 40, "age": "2w"},
        {"txt": "c" * 80, "age": "Edited"},
        {"urn": "urn:dup", "txt": "d"},
        {},
    ]
    calls = _mock_http(monkeypatch, ING_LI, _li({"has_session": True}, {"value": json.dumps(posts)}))
    db = _use_db(monkeypatch, _DB([[], [], [], [object()]]))
    assert await ING_LI._run() == {"new_candidates": 3}
    after = dt.datetime.now(dt.UTC)
    a, b, c = db.added
    assert a.post_id == "urn:li:activity:1" and a.author_name == "Ana"
    assert after - a.posted_at >= dt.timedelta(hours=3)
    assert b.post_id == "https://li/in/bo"
    assert after - b.posted_at >= dt.timedelta(weeks=2)
    assert c.post_id == "c" * 60
    assert sum(1 for r in calls if r.url.path == "/debug/eval") == 5


@pytest.mark.asyncio
async def test_ingest_li_result_list_payload(monkeypatch):
    import asyncio

    monkeypatch.setattr(asyncio, "sleep", _nosleep)
    _mock_http(monkeypatch, ING_LI, _li({"has_session": True}, {"result": [{"urn": "u1", "txt": "x"}]}))
    db = _use_db(monkeypatch, _DB([[]]))
    assert await ING_LI._run() == {"new_candidates": 1}
    assert db.added[0].post_id == "u1"


# ── TikTok tag ingest ──────────────────────────────────────────────────


def test_tt_views_parser():
    assert ING_TT._views("1.5M") == 1_500_000
    assert ING_TT._views("12K") == 12_000
    assert ING_TT._views("2B") == 2_000_000_000
    assert ING_TT._views("812") == 812
    assert ING_TT._views("") == 0
    assert ING_TT._views(None) == 0


@pytest.mark.asyncio
async def test_ingest_tt_busy(monkeypatch):
    _mock_http(monkeypatch, ING_TT, _bridge("[]", start_fails=True))
    assert await ING_TT._run() == {"skipped": "browser busy"}


@pytest.mark.asyncio
async def test_ingest_tt_tags(monkeypatch):
    monkeypatch.setattr(ING_TT.asyncio, "sleep", _nosleep)
    monkeypatch.setenv("TIKTOK_TARGET_TAGS", "#serverless, ,ai")
    posts = [
        {"href": "/@a/video/111", "author": "a", "txt": "hot", "views": "12K"},
        {"href": "/@b/video/222", "author": "b", "views": "900"},
        {"href": "/@c/photo/9"},
        {"href": "/@d/video/333"},
    ]
    calls = _mock_http(monkeypatch, ING_TT, _bridge(json.dumps(posts)))
    # tag1: 111 new, 222 new, 333 dup; tag2: all dup
    db = _use_db(monkeypatch, _DB([[], [], [object()], [object()], [object()], [object()]]))
    assert await ING_TT._run() == {"new_candidates": 2}
    a, b = db.added
    assert a.meta_data == {"views": 12000, "viral": True, "source_tag": "serverless"}
    assert b.meta_data["viral"] is False
    navs = [json.loads(c.content)["url"] for c in calls if c.url.path == "/session/navigate"]
    assert navs == ["https://www.tiktok.com/tag/serverless", "https://www.tiktok.com/tag/ai"]


@pytest.mark.asyncio
async def test_ingest_tt_default_tags_empty_eval(monkeypatch):
    monkeypatch.setattr(ING_TT.asyncio, "sleep", _nosleep)
    monkeypatch.delenv("TIKTOK_TARGET_TAGS", raising=False)
    calls = _mock_http(monkeypatch, ING_TT, _bridge(None))
    _use_db(monkeypatch, _DB())
    assert await ING_TT._run() == {"new_candidates": 0}
    assert sum(1 for c in calls if c.url.path == "/session/navigate") == 3  # capped


# ── tiers + ledger ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_connection_tiers(monkeypatch):
    def c(handle, tier, **kw):
        base = dict(author_handle=handle, connection_tier=tier, in_crm=False, is_connection=False, is_followed=False)
        base.update(kw)
        return SimpleNamespace(**base)

    crm = c("crm", "low", in_crm=True)
    reciprocal = c("rec", "medium")
    conn = c("conn", "low", is_connection=True)
    cold = c("cold", "medium")
    same = c("same", "low")
    _use_db(monkeypatch, _DB([[("rec", 2), ("conn", 1)], [crm, reciprocal, conn, cold, same]]))
    assert await TIERS._run() == {"high": 2, "medium": 1, "low": 1}
    assert [x.connection_tier for x in (crm, reciprocal, conn, cold, same)] == ["high", "high", "medium", "low", "low"]


@pytest.mark.asyncio
async def test_compact_ledger(monkeypatch):
    db = _use_db(monkeypatch, _DB([_Res(rowcount=4), _Res(rowcount=7)]))
    assert await ARCH._run(30) == {"actions_pruned": 4, "candidates_pruned": 7}
    assert db.commits == 1
