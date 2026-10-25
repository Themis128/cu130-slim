"""Engagement actors — X/Threads REST + LinkedIn/IG/FB/TikTok browser bridges.

Real actor code; HTTP is served by httpx.MockTransport and the DB/session
lookups are faked at the boundary.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from app.services.engagement_actors import (
    fb_actor,
    ig_actor,
    linkedin_actor,
    threads_actor,
    tiktok_actor,
    x_actor,
)

_RealClient = httpx.AsyncClient


def _mock_http(monkeypatch, mod, handler):
    """Route every AsyncClient the actor opens through ``handler``."""
    calls: list[httpx.Request] = []

    def _h(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return handler(req)

    def _factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(_h)
        return _RealClient(*a, **kw)

    monkeypatch.setattr(mod.httpx, "AsyncClient", _factory)
    return calls


async def _nosleep(*_a, **_k):
    return None


def _action(**kw):
    base = dict(action="like", target_post_id="123", comment_draft=None, platform="x", target_author="a")
    base.update(kw)
    return SimpleNamespace(**base)


class _Res:
    def __init__(self, row):
        self._row = row

    def scalars(self):
        return SimpleNamespace(first=lambda: self._row)


class _DB:
    def __init__(self, row):
        self.row = row

    async def execute(self, stmt):
        return _Res(self.row)


def _acct(meta=None):
    return SimpleNamespace(access_token_enc="enc", account_id="uid-th", meta_data=meta)


# ── X ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_x_no_account():
    assert await x_actor.act(_action(), _DB(None)) == {"error": "no active twitter account"}


@pytest.mark.asyncio
async def test_x_empty_decrypted_token(monkeypatch):
    import app.core.security as sec

    monkeypatch.setattr(sec, "decrypt_token", lambda v: "")
    assert await x_actor.act(_action(), _DB(_acct())) == {"error": "no active twitter account"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,path",
    [("like", "/2/users/u1/likes"), ("repost", "/2/users/u1/retweets"), ("comment", "/2/tweets")],
)
async def test_x_actions_success(monkeypatch, kind, path):
    import app.core.security as sec

    monkeypatch.setattr(sec, "decrypt_token", lambda v: "tok")
    calls = _mock_http(monkeypatch, x_actor, lambda r: httpx.Response(201, json={"data": {"id": "9"}}))
    out = await x_actor.act(_action(action=kind, comment_draft="hi"), _DB(_acct({"user_id": "u1"})))
    assert out == {"ok": True, "resp": {"id": "9"}}
    assert calls[0].url.path == path
    assert calls[0].headers["Authorization"] == "Bearer tok"
    body = json.loads(calls[0].content)
    if kind == "comment":
        assert body == {"text": "hi", "reply": {"in_reply_to_tweet_id": "123"}}
    else:
        assert body == {"tweet_id": "123"}


@pytest.mark.asyncio
async def test_x_comment_empty_and_unknown_action(monkeypatch):
    import app.core.security as sec

    monkeypatch.setattr(sec, "decrypt_token", lambda v: "tok")
    _mock_http(monkeypatch, x_actor, lambda r: httpx.Response(200, json={}))
    db = _DB(_acct(None))
    assert await x_actor.act(_action(action="comment"), db) == {"error": "empty comment draft"}
    assert await x_actor.act(_action(action="dance"), db) == {"error": "unknown action dance"}


@pytest.mark.asyncio
async def test_x_credit_depleted_and_failure(monkeypatch):
    import app.core.security as sec

    monkeypatch.setattr(sec, "decrypt_token", lambda v: "tok")
    _mock_http(monkeypatch, x_actor, lambda r: httpx.Response(402, text="pay"))
    db = _DB(_acct({"user_id": "u"}))
    assert await x_actor.act(_action(), db) == {"error": "x credits depleted", "skip": True}
    _mock_http(monkeypatch, x_actor, lambda r: httpx.Response(403, text="forbidden"))
    assert await x_actor.act(_action(), db) == {"error": "403: forbidden"}


# ── Threads ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_threads_no_account():
    assert await threads_actor.act(_action(), _DB(None)) == {"error": "no active threads account"}


@pytest.mark.asyncio
async def test_threads_like_skips_and_empty_draft(monkeypatch):
    import app.core.security as sec

    monkeypatch.setattr(sec, "decrypt_token", lambda v: "tok")
    db = _DB(_acct())
    out = await threads_actor.act(_action(action="like"), db)
    assert out["skip"] is True
    assert await threads_actor.act(_action(action="comment"), db) == {"error": "empty comment draft"}


@pytest.mark.asyncio
async def test_threads_reply_publish_flow(monkeypatch):
    import app.core.security as sec

    monkeypatch.setattr(sec, "decrypt_token", lambda v: "tok")

    def handler(req):
        if req.url.path.endswith("/threads"):
            return httpx.Response(200, json={"id": "c1"})
        return httpx.Response(200, json={"id": "pub1"})

    calls = _mock_http(monkeypatch, threads_actor, handler)
    out = await threads_actor.act(_action(action="comment", comment_draft="nice"), _DB(_acct()))
    assert out == {"ok": True, "resp": {"id": "pub1"}}
    assert calls[0].url.path == "/v1.0/uid-th/threads"
    assert b"reply_to_id=123" in calls[0].content
    assert b"creation_id=c1" in calls[1].content


@pytest.mark.asyncio
async def test_threads_create_and_publish_failures(monkeypatch):
    import app.core.security as sec

    monkeypatch.setattr(sec, "decrypt_token", lambda v: "tok")
    _mock_http(monkeypatch, threads_actor, lambda r: httpx.Response(400, text="bad"))
    out = await threads_actor.act(_action(action="comment", comment_draft="x"), _DB(_acct()))
    assert out == {"error": "400: bad"}

    def handler(req):
        if req.url.path.endswith("/threads"):
            return httpx.Response(201, json={"id": "c1"})
        return httpx.Response(500, text="boom")

    _mock_http(monkeypatch, threads_actor, handler)
    out = await threads_actor.act(_action(action="comment", comment_draft="x"), _DB(_acct()))
    assert out == {"error": "publish 500: boom"}


# ── LinkedIn sidecar ───────────────────────────────────────────────────


def _li_handler(health, result=201):
    def h(req):
        if req.url.path == "/health":
            return httpx.Response(200, json=health)
        return httpx.Response(200, json={"result": result})

    return h


@pytest.mark.asyncio
async def test_linkedin_rate_limited_and_no_session(monkeypatch):
    _mock_http(monkeypatch, linkedin_actor, _li_handler({"rate_limited": True}))
    assert await linkedin_actor.act(_action(), None) == {"error": "rate_limited", "skip": True}
    _mock_http(monkeypatch, linkedin_actor, _li_handler({"has_session": False}))
    assert await linkedin_actor.act(_action(), None) == {"error": "no_session"}


@pytest.mark.asyncio
async def test_linkedin_like_and_comment(monkeypatch):
    calls = _mock_http(monkeypatch, linkedin_actor, _li_handler({"has_session": True}, 201))
    out = await linkedin_actor.act(_action(target_post_id="urn:li:ugcPost:777"), None)
    assert out == {"ok": True, "resp": {"voyager_status": 201}}
    assert "777" in json.loads(calls[-1].content)["expr"]

    out = await linkedin_actor.act(
        _action(action="comment", comment_draft='say "hi"', target_post_id="urn:li:ugcPost:5"), None
    )
    assert out["ok"] is True
    expr = json.loads(calls[-1].content)["expr"]
    assert json.dumps('say "hi"') in expr
    assert "urn:li:ugcPost:5" in expr


@pytest.mark.asyncio
async def test_linkedin_comment_empty_repost_and_failure(monkeypatch):
    _mock_http(monkeypatch, linkedin_actor, _li_handler({"has_session": True}, 403))
    assert await linkedin_actor.act(_action(action="comment"), None) == {"error": "empty comment draft"}
    out = await linkedin_actor.act(_action(action="repost"), None)
    assert out["skip"] is True
    assert await linkedin_actor.act(_action(target_post_id="a:b:1"), None) == {"error": "voyager 403"}


# ── browser-bridge actors (IG / FB / TikTok) ──────────────────────────


def _bridge(result, start_fails=False):
    def h(req):
        if req.url.path == "/session/start" and start_fails:
            raise httpx.ConnectError("busy", request=req)
        if req.url.path == "/session/evaluate":
            return httpx.Response(200, json={"result": result})
        return httpx.Response(200, json={})

    return h


@pytest.mark.asyncio
async def test_ig_non_like_skips():
    out = await ig_actor.act(_action(action="comment"), None)
    assert out == {"error": "ig: only like auto-acted", "skip": True}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result,expected",
    [
        ("liked", {"ok": True}),
        ("already-liked-or-no-btn", {"ok": True, "resp": {"note": "already-liked-or-no-btn"}}),
        ("", {"error": "browser busy", "skip": True}),
        ("post-not-visible", {"error": "post-not-visible", "skip": True}),
        ("weird", {"error": "weird"}),
    ],
)
async def test_ig_like_outcomes(monkeypatch, result, expected):
    calls = _mock_http(monkeypatch, ig_actor, _bridge(result))
    assert await ig_actor.act(_action(target_post_id="ABCDE"), None) == expected
    assert "ABCDE" in json.loads(calls[-1].content)["expression"]


@pytest.mark.asyncio
async def test_ig_browser_busy(monkeypatch):
    _mock_http(monkeypatch, ig_actor, _bridge("liked", start_fails=True))
    assert await ig_actor.act(_action(), None) == {"error": "browser busy", "skip": True}


@pytest.mark.asyncio
async def test_fb_busy_like_and_unsupported(monkeypatch):
    _mock_http(monkeypatch, fb_actor, _bridge("liked", start_fails=True))
    assert await fb_actor.act(_action(), None) == {"error": "browser busy", "skip": True}
    _mock_http(monkeypatch, fb_actor, _bridge("liked"))
    assert await fb_actor.act(_action(), None) == {"ok": True}
    _mock_http(monkeypatch, fb_actor, _bridge("no-btn"))
    assert await fb_actor.act(_action(), None) == {"error": "no-btn", "skip": True}
    out = await fb_actor.act(_action(action="repost"), None)
    assert out == {"error": "fb: repost not auto-acted", "skip": True}


@pytest.mark.asyncio
async def test_fb_comment_paths(monkeypatch):
    monkeypatch.setattr(fb_actor.asyncio, "sleep", _nosleep)
    _mock_http(monkeypatch, fb_actor, _bridge("commented"))
    assert await fb_actor.act(_action(action="comment"), None) == {"error": "empty draft"}
    calls = _mock_http(monkeypatch, fb_actor, _bridge("commented"))
    url = "https://facebook.com/groups/1/posts/2"
    out = await fb_actor.act(_action(action="comment", comment_draft="great", target_post_id=url), None)
    assert out == {"ok": True}
    assert json.loads(calls[1].content) == {"url": url}
    _mock_http(monkeypatch, fb_actor, _bridge("no-comment-box"))
    out = await fb_actor.act(_action(action="comment", comment_draft="x"), None)
    assert out == {"error": "no-comment-box", "skip": True}
    _mock_http(monkeypatch, fb_actor, _bridge(""))
    out = await fb_actor.act(_action(action="comment", comment_draft="x"), None)
    assert out == {"error": "eval failed", "skip": False}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["repost", "duet_flag"])
async def test_tiktok_manual_actions_skip(kind):
    out = await tiktok_actor.act(_action(action=kind), None)
    assert out["skip"] is True
    assert "manual" in out["error"]


@pytest.mark.asyncio
async def test_tiktok_busy_like_comment(monkeypatch):
    monkeypatch.setattr(tiktok_actor.asyncio, "sleep", _nosleep)
    _mock_http(monkeypatch, tiktok_actor, _bridge("liked", start_fails=True))
    assert await tiktok_actor.act(_action(), None) == {"error": "browser busy", "skip": True}

    calls = _mock_http(monkeypatch, tiktok_actor, _bridge("liked"))
    assert await tiktok_actor.act(_action(target_post_id="42"), None) == {"ok": True}
    assert json.loads(calls[1].content) == {"url": "https://www.tiktok.com/video/42"}
    _mock_http(monkeypatch, tiktok_actor, _bridge("no-btn"))
    assert await tiktok_actor.act(_action(), None) == {"error": "no-btn", "skip": True}

    _mock_http(monkeypatch, tiktok_actor, _bridge("commented"))
    assert await tiktok_actor.act(_action(action="comment"), None) == {"error": "empty draft"}
    assert await tiktok_actor.act(_action(action="comment", comment_draft="fire"), None) == {"ok": True}
    _mock_http(monkeypatch, tiktok_actor, _bridge("no-comment-box"))
    out = await tiktok_actor.act(_action(action="comment", comment_draft="fire"), None)
    assert out == {"error": "no-comment-box", "skip": True}
    _mock_http(monkeypatch, tiktok_actor, _bridge(None))
    out = await tiktok_actor.act(_action(action="comment", comment_draft="fire"), None)
    assert out == {"error": "eval failed", "skip": False}
    out = await tiktok_actor.act(_action(action="save"), None)
    assert out == {"error": "tt: save unsupported", "skip": True}
