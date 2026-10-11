"""Unit tests for app/worker/tasks/skool_watch.py — Skool VEC watcher."""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.worker.tasks.skool_watch as S

# ── fakes ─────────────────────────────────────────────────────────────


class _Redis:
    def __init__(self):
        self.kv: dict = {}
        self.sets: dict = {}
        self.hashes: dict = {}
        self.closed = False

    async def get(self, k):
        return self.kv.get(k)

    async def set(self, k, v, ex=None):
        self.kv[k] = v

    async def delete(self, k):
        self.kv.pop(k, None)

    async def sadd(self, k, v):
        self.sets.setdefault(k, set()).add(v)

    async def smembers(self, k):
        return self.sets.get(k, set())

    async def hset(self, k, mapping=None, **kw):
        self.hashes.setdefault(k, {}).update(mapping or kw)

    async def hgetall(self, k):
        return self.hashes.get(k, {})

    async def hincrby(self, k, f, n):
        h = self.hashes.setdefault(k, {})
        h[f] = str(int(h.get(f) or 0) + n)

    async def scan_iter(self, pattern):
        prefix = pattern.rstrip("*")
        for k in list(self.hashes):
            if k.startswith(prefix):
                yield k

    async def aclose(self):
        self.closed = True


class _Bridge:
    """Browser-bridge fake — evaluate routes on the JS constant."""

    def __init__(self, pages=None, nav_exc=None, status="done",
                 start_ok=True, sleep=None):
        self.pages = pages or {}   # js-expr-substring -> evaluate payload
        self.nav_exc = nav_exc     # dict url->exc or exc
        self.status = status
        self.start_ok = start_ok
        self.nav_calls: list[str] = []

    async def navigate(self, url):
        self.nav_calls.append(url)
        exc = self.nav_exc
        if isinstance(exc, dict):
            exc = exc.get(url)
        if exc:
            raise exc
        self.nav_exc = None  # failures fire once

    async def evaluate(self, expr):
        for needle, payload in self.pages.items():
            if needle in expr:
                return {"result": json.dumps(payload)}
        return {"result": "{}"}

    async def session_status(self):
        return {"status": self.status}

    async def start_session(self, platform, force=False):
        self.force_used = force
        if not self.start_ok:
            from app.services.browser_bridge import BrowserBridgeError
            raise BrowserBridgeError("busy", "")


class _Res:
    def __init__(self, first=None):
        self._first = first

    def scalars(self):
        return SimpleNamespace(first=lambda: self._first,
                               all=lambda: [self._first] if self._first else [])


class _DB:
    def __init__(self, queue=()):
        self.queue = list(queue)
        self.added: list = []
        self.commits = 0

    async def execute(self, *a, **k):
        return self.queue.pop(0) if self.queue else _Res()

    def add(self, obj):
        if not getattr(obj, "id", None):
            obj.id = uuid.uuid4()
        self.added.append(obj)

    async def flush(self):
        pass

    async def commit(self):
        self.commits += 1


class _SessionCM:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *e):
        return False


def _bridge_error(msg="x"):
    from app.services.browser_bridge import BrowserBridgeError
    return BrowserBridgeError(msg, "")


@pytest.fixture(autouse=True)
def _common(monkeypatch):
    monkeypatch.setattr(S.asyncio, "sleep", AsyncMock())
    monkeypatch.setenv("SOCIAL_ADMIN_EMAIL", "a@b.c")


def _wire(monkeypatch, bridge, redis=None, db=None, slack_alert=None,
          slack_digest=None):
    import app.services.browser_bridge as BB
    import app.services.slack_notifications as SN
    monkeypatch.setattr(BB, "BrowserBridgeClient", lambda *a, **k: bridge)
    r = redis or _Redis()
    monkeypatch.setattr(S, "_redis", AsyncMock(return_value=r))
    monkeypatch.setattr(S, "_worker_db",
                        lambda: _SessionCM(db or _DB()))
    monkeypatch.setattr(SN, "post_alert_to_slack",
                        slack_alert or AsyncMock())
    monkeypatch.setattr(SN, "post_digest_text_to_slack",
                        slack_digest or AsyncMock())
    return r


# ── pure helpers ──────────────────────────────────────────────────────


def test_extract_task():
    assert S._extract_task("no markers") == ""
    out = S._extract_task(
        "YOUR TASK\n  do the thing\n  today COMMUNITY ACTION x")
    assert out == "do the thing today"
    # 1200-char cap
    assert len(S._extract_task("YOUR TASK " + "x" * 2000)) == 1200


@pytest.mark.asyncio
async def test_eval_json():
    client = SimpleNamespace(evaluate=AsyncMock(
        return_value={"result": '{"a": 1}'}))
    assert await S._eval_json(client, "js") == {"a": 1}
    client.evaluate = AsyncMock(return_value={"result": "not-json"})
    assert await S._eval_json(client, "js") == "not-json"
    client.evaluate = AsyncMock(return_value={"result": ""})
    assert await S._eval_json(client, "js") == ""


@pytest.mark.asyncio
async def test_acquire_browser():
    # busy states → no force
    b = _Bridge(status="active")
    assert await S._acquire_browser(b) is True
    assert b.force_used is False
    for st in ("done", "idle", "error"):
        b = _Bridge(status=st)
        assert await S._acquire_browser(b) is True
        assert b.force_used is True
    # unknown state → skip
    b = _Bridge(status="composing")
    assert await S._acquire_browser(b) is False
    # status probe raises → "" → force
    b = _Bridge()
    b.session_status = AsyncMock(side_effect=RuntimeError("x"))
    assert await S._acquire_browser(b) is True
    assert b.force_used is True
    # start fails → False
    b = _Bridge(status="done", start_ok=False)
    assert await S._acquire_browser(b) is False


def test_run_async_paths(monkeypatch):
    async def _coro():
        return 42
    import app.worker._async as W

    def _run(coro):
        coro.close()
        return 42
    monkeypatch.setattr(W, "run_async", _run)
    assert S._run_async(_coro()) == 42


# ── _create_draft ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_draft(monkeypatch):
    import app.services.brand_compliance as BC
    import app.services.inference as INF

    tid, uid, aid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    # already drafted → None (no inference call)
    monkeypatch.setattr(INF, "call_inference", AsyncMock())
    db = _DB(queue=[_Res(first=uuid.uuid4())])
    assert await S._create_draft(db, tid, uid, aid, "md", "t", "txt",
                                 "task") is None
    INF.call_inference.assert_not_awaited()

    # inference empty → None
    monkeypatch.setattr(BC, "load_brand_context", AsyncMock(
        return_value=(1, 2, "ctx")))
    monkeypatch.setattr(INF, "call_inference", AsyncMock(
        return_value={}))
    db = _DB(queue=[_Res()])
    assert await S._create_draft(db, tid, uid, aid, "md", "t", "txt",
                                 "") is None

    # list text → joined
    monkeypatch.setattr(INF, "call_inference", AsyncMock(
        return_value={"text": ["l1", "l2"]}))
    db = _DB(queue=[_Res()])
    out = await S._create_draft(db, tid, uid, aid, "md", "t", "txt", "")
    assert out and db.added[0].content_text == "l1\nl2"
    assert db.added[0].meta_data["needs_media"] is True

    # JSON envelope → unwrapped
    monkeypatch.setattr(INF, "call_inference", AsyncMock(
        return_value={"text": '{"response": "envelope post"}'}))
    db = _DB(queue=[_Res()])
    out = await S._create_draft(db, tid, uid, aid, "md", "t", "txt", "")
    assert db.added[0].content_text == "envelope post"

    # plain text happy path
    monkeypatch.setattr(INF, "call_inference", AsyncMock(
        return_value={"response": "real post"}))
    db = _DB(queue=[_Res()])
    out = await S._create_draft(db, tid, uid, aid, "md", "t", "txt", "")
    assert out and db.commits == 1 and len(db.added) == 2  # Post + Target


# ── _watch gates ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_watch_gates(monkeypatch):
    bridge = _Bridge()

    # disabled flag → skip before touching browser
    r = _Redis()
    r.kv["skool:vec:disabled"] = "1"
    _wire(monkeypatch, bridge, redis=r)
    out = await S._watch()
    assert out["reason"].startswith("disabled")
    assert bridge.nav_calls == []
    assert r.closed is True

    # bridge hard-down → unavailable
    bridge = _Bridge(nav_exc=_bridge_error("connection refused"))
    _wire(monkeypatch, bridge)
    out = await S._watch()
    assert "browser unavailable" in out["reason"]

    # no session + acquire fails → busy
    bridge = _Bridge(
        nav_exc=_bridge_error("No active browser session"),
        start_ok=False)
    _wire(monkeypatch, bridge)
    out = await S._watch()
    assert "browser busy" in out["reason"]

    # logged out → slack alert once, flag set; second run → no alert
    alert = AsyncMock()
    bridge = _Bridge(pages={"location.href": {"url": "https://skool.com/about",
                                          "text": "about page"}})
    r = _wire(monkeypatch, bridge, slack_alert=alert)
    out = await S._watch()
    assert out["reason"] == "logged out"
    assert alert.await_count == 1
    assert r.kv["skool:vec:session_alert"] == "1"
    await S._watch()
    assert alert.await_count == 1  # TTL'd — no spam


@pytest.mark.asyncio
async def test_watch_seed_sweep(monkeypatch):
    alert, digest = AsyncMock(), AsyncMock()
    modules = [{"t": "DAY 1: x", "h": "/c?md=" + "a" * 32},
               {"t": "week", "h": "/c?md=" + "b" * 32}]
    posts = [{"t": "a post", "h": "/sofia-kakkava-coaching/abc123"}]
    bridge = _Bridge(pages={
        "location.href": {"url": S._COURSE_URL, "text": "ok"},
        'a[href*="md="]': modules,
        "Leaderboards": posts,
    })
    team = SimpleNamespace(id=uuid.uuid4(), owner_id=uuid.uuid4())
    user = SimpleNamespace(id=uuid.uuid4())
    acct = SimpleNamespace(id=uuid.uuid4())
    db = _DB(queue=[_Res(first=team), _Res(first=user),
                    _Res(first=acct)])
    r = _wire(monkeypatch, bridge, db=db, slack_alert=alert,
              slack_digest=digest)
    out = await S._watch()
    assert out["seeded"] is True and out["new_modules"] == 0
    # everything silently marked seen
    assert r.sets["skool:vec:seen_modules"] == {"a" * 32, "b" * 32}
    alert.assert_not_awaited() and digest.assert_not_awaited()


@pytest.mark.asyncio
async def test_watch_new_day_module(monkeypatch):
    alert, digest = AsyncMock(), AsyncMock()
    md = "c" * 32
    lesson = "YOUR TASK post about it COMMUNITY ACTION"
    bridge = _Bridge(pages={
        "location.href": {"url": S._COURSE_URL, "text": "ok",
                          "lesson": lesson},
        'a[href*="md="]': [{"t": "DAY 3: thing", "h": f"/c?md={md}"}],
        "Leaderboards": [],
    })
    # _JS_TEXT fires on both classroom + lesson pages
    calls = {"n": 0}
    orig_eval = bridge.evaluate

    async def _eval(expr):
        if "location.href" in expr:
            calls["n"] += 1
            body = lesson if calls["n"] > 1 else "ok"
            return {"result": json.dumps(
                {"url": S._COURSE_URL, "text": body})}
        return await orig_eval(expr)
    bridge.evaluate = _eval

    team = SimpleNamespace(id=uuid.uuid4(), owner_id=uuid.uuid4())
    user = SimpleNamespace(id=uuid.uuid4())
    acct = SimpleNamespace(id=uuid.uuid4())
    db = _DB(queue=[_Res(first=team), _Res(first=user),
                    _Res(first=acct), _Res()])
    r = _Redis()
    r.kv["skool:vec:seeded"] = "1"
    _wire(monkeypatch, bridge, redis=r, db=db, slack_alert=alert,
          slack_digest=digest)
    draft = AsyncMock(return_value="d" * 32)
    monkeypatch.setattr(S, "_create_draft", draft)
    out = await S._watch()
    assert out["drafts"] == ["d" * 32]
    assert r.hashes[f"skool:vec:lesson:{md}"]["draft_id"] == "d" * 32
    assert alert.await_count == 1
    # draft kwargs carry team/user/account
    assert draft.await_args.args[1:4] == (team.id, user.id, acct.id)


@pytest.mark.asyncio
async def test_watch_partial_render_and_posts(monkeypatch):
    digest = AsyncMock()
    bridge = _Bridge(pages={
        "location.href": {"url": S._COURSE_URL, "text": "ok"},
        'a[href*="md="]': [{"t": "x", "h": "/c?md=" + "a" * 32}],
        "Leaderboards": [{"t": "new community post",
                          "h": "/sofia-kakkava-coaching/zzz999"}],
    })
    r = _Redis()
    r.kv["skool:vec:seeded"] = "1"
    r.sets["skool:vec:seen_modules"] = {"a" * 32, "b" * 32}  # 2 known
    team = SimpleNamespace(id=uuid.uuid4(), owner_id=None)
    db = _DB(queue=[_Res(first=team), _Res(first=None),
                    _Res(first=None)])
    _wire(monkeypatch, bridge, redis=r, db=db, slack_digest=digest)
    out = await S._watch()
    # partial render (1 < 2 known) → module diff skipped
    assert out["new_modules"] == 0
    # community post still processed + digested
    assert out["new_posts"] == 1
    assert r.sets["skool:vec:seen_posts"]
    digest.assert_awaited_once()


@pytest.mark.asyncio
async def test_watch_stranded_draft_retry(monkeypatch):
    alert = AsyncMock()
    bridge = _Bridge(pages={
        "location.href": {"url": S._COURSE_URL, "text": "ok"},
        'a[href*="md="]': [],
        "Leaderboards": [],
    })
    r = _Redis()
    r.kv["skool:vec:seeded"] = "1"
    # stranded DAY lesson, 1 attempt — retryable
    r.hashes["skool:vec:lesson:dead1"] = {
        "title": "DAY 5: x", "text": "t", "task": "k",
        "draft_attempts": "1"}
    # exhausted attempts — skipped
    r.hashes["skool:vec:lesson:dead2"] = {
        "title": "DAY 6: y", "draft_attempts": "3"}
    # already drafted — skipped
    r.hashes["skool:vec:lesson:dead3"] = {
        "title": "DAY 7: z", "draft_id": "ok"}
    team = SimpleNamespace(id=uuid.uuid4(), owner_id=None)
    user = SimpleNamespace(id=uuid.uuid4())
    acct = SimpleNamespace(id=uuid.uuid4())
    db = _DB(queue=[_Res(first=team), _Res(first=user),
                    _Res(first=acct)])
    _wire(monkeypatch, bridge, redis=r, db=db, slack_alert=alert)
    monkeypatch.setattr(S, "_create_draft", AsyncMock(
        return_value="e" * 32))
    out = await S._watch()
    assert out["drafts"] == ["e" * 32]
    assert r.hashes["skool:vec:lesson:dead1"]["draft_id"] == "e" * 32
    assert alert.await_count == 1  # recovery alert

    # retry failure → attempts incremented
    r.hashes["skool:vec:lesson:dead1"].pop("draft_id")
    monkeypatch.setattr(S, "_create_draft", AsyncMock(
        return_value=None))
    db = _DB(queue=[_Res(first=team), _Res(first=user),
                    _Res(first=acct)])
    _wire(monkeypatch, bridge, redis=r, db=db)
    out = await S._watch()
    assert r.hashes["skool:vec:lesson:dead1"]["draft_attempts"] == "2"
