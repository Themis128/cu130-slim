"""Coverage: linkedin_engagement helpers + _run branch paths."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.worker.tasks import linkedin_engagement as ENG

# ── helpers ──────────────────────────────────────────────────────────────


def test_watchlist_and_templates(monkeypatch):
    monkeypatch.setenv("LINKEDIN_ENGAGE_AUTHORS", "urn:li:org:1, bad, urn:li:person:2,")
    monkeypatch.setenv("LINKEDIN_ENGAGE_COMMENT_TEMPLATES", " hi |  | there ")
    assert ENG._watchlist() == ["urn:li:org:1", "urn:li:person:2"]
    assert ENG._comment_templates() == ["hi", "there"]
    monkeypatch.setenv("LINKEDIN_ENGAGE_AUTHORS", "")
    monkeypatch.setenv("LINKEDIN_ENGAGE_COMMENT_TEMPLATES", "")
    assert ENG._watchlist() == [] and ENG._comment_templates() == []


def test_post_urn_variants():
    assert ENG._post_urn({"id": "urn:li:share:1"}) == "urn:li:share:1"
    assert ENG._post_urn({"shareUrn": "urn:li:share:2"}) == "urn:li:share:2"
    assert ENG._post_urn({"activity": "urn:li:activity:3"}) == "urn:li:activity:3"
    assert ENG._post_urn({"object": "urn:li:object:4"}) == "urn:li:object:4"
    assert ENG._post_urn({"id": "12345"}) is None
    assert ENG._post_urn({}) is None
    assert ENG._post_urn({"shareUrn": 123}) is None


def test_post_age_h_variants():
    now_ms = datetime.now(UTC).timestamp() * 1000
    # dict timestamp
    age = ENG._post_age_h({"publishedAt": {"time": now_ms - 3_600_000}})
    assert age is not None and 0.9 < age < 1.1
    # numeric timestamp
    age = ENG._post_age_h({"createdAt": now_ms - 7_200_000})
    assert age is not None and 1.9 < age < 2.1
    # lastModified fallback
    assert ENG._post_age_h({"lastModified": now_ms}) is not None
    # no ts / falsy ts / non-numeric
    assert ENG._post_age_h({}) is None
    assert ENG._post_age_h({"publishedAt": {}}) is None
    assert ENG._post_age_h({"publishedAt": "soon"}) is None


# ── db helpers ───────────────────────────────────────────────────────────


class _ScalarResult:
    def __init__(self, value):
        self._v = value

    def scalar_one_or_none(self):
        return self._v

    def scalars(self):
        return SimpleNamespace(all=lambda: self._v)


class _DB:
    def __init__(self, existing=None, metas=None):
        self._existing = existing
        self._metas = metas or []
        self.added = []
        self.commits = 0

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def execute(self, q):
        # distinguish by which columns the query selects — inspect via _existing/_metas order
        if self._metas is not None and getattr(self, "_next", None) is None:
            pass
        return self._result

    @property
    def _result(self):
        return _ScalarResult(self._existing)


def _make_db(existing=None, metas=None):
    db = _DB()
    db._existing = existing
    db._metas = metas
    return db


@pytest.mark.asyncio
async def test_already_acted():
    db = _make_db(existing=123)
    assert await ENG._already_acted(db, "t1", "urn:li:x") is True
    db2 = _make_db(existing=None)
    assert await ENG._already_acted(db2, "t1", "urn:li:x") is False


@pytest.mark.asyncio
async def test_actions_today_counts():
    class _DB2:
        def __init__(self):
            self._calls = 0

        async def execute(self, q):
            self._calls += 1
            if self._calls == 1:
                return SimpleNamespace(
                    scalars=lambda: SimpleNamespace(all=lambda: [{"action": "reaction"}, {"action": "comment"}, {"action": "reaction"}, None, {}])
                )
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

    r, c = await ENG._actions_today(_DB2(), "t1")
    assert (r, c) == (2, 1)


@pytest.mark.asyncio
async def test_record_adds_event_and_commits():
    db = _DB()
    await ENG._record(db, "t1", "acc-1", "urn:li:share:9", "reaction", {"type": "LIKE"})
    assert len(db.added) == 1
    ev = db.added[0]
    assert ev.event_type == "linkedin_engagement" and ev.platform_event_id == "urn:li:share:9"
    assert db.commits == 1


# ── _run branches ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_no_team(monkeypatch):
    monkeypatch.setenv("LINKEDIN_ENGAGE_AUTHORS", "urn:li:org:1")

    class _CM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, q):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: None))

    monkeypatch.setattr(ENG, "task_session", lambda: _CM())
    out = await ENG._run()
    assert out == {"error": "no-team"}


@pytest.mark.asyncio
async def test_run_list_posts_error_and_branches(monkeypatch):
    monkeypatch.setenv("LINKEDIN_ENGAGE_AUTHORS", "urn:li:org:1,urn:li:org:2")
    monkeypatch.setenv("LINKEDIN_ENGAGE_COMMENT_TEMPLATES", "nice post")

    team = SimpleNamespace(id="team-1")
    acct = SimpleNamespace(account_type="person", id="acc-p")
    fresh = {"time": datetime.now(UTC).timestamp() * 1000}

    class _CM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, q):
            call_n["n"] += 1
            if call_n["n"] == 1:
                return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: team))
            if call_n["n"] == 2:
                return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [acct]))
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

        def add(self, obj):
            pass

        async def commit(self):
            pass

    call_n = {"n": 0}

    async def fake_list(author, count=5):
        if author == "urn:li:org:1":
            return {"status": 500, "posts": []}  # -> errors + continue (158-159)
        return {
            "status": 200,
            "posts": [
                {"id": "no-urn-here"},  # no urn -> skip (163)
                {"id": "urn:li:share:old", "publishedAt": {"time": 1}},  # age skip
                {"id": "urn:li:share:ok1", "publishedAt": fresh},  # react ok + comment ok
                {"id": "urn:li:share:ok2", "publishedAt": fresh},  # react ok + comment 500
                {"id": "urn:li:share:ok3", "publishedAt": fresh},  # react ok + comment 403
                {"id": "urn:li:share:dedup", "publishedAt": fresh},  # already acted
                {"id": "urn:li:share:reactfail", "publishedAt": fresh},  # react generic error
                {"id": "urn:li:share:authfail", "publishedAt": fresh},  # react 401 -> break
            ],
        }

    async def fake_react(kind, urn, rtype):
        if urn == "urn:li:share:authfail":
            return {"status": 401}  # -> error + break (181-183)
        if urn == "urn:li:share:reactfail":
            return {"status": 500, "error": "react-boom"}
        return {"ok": True}

    async def fake_comment(kind, urn, text):
        if urn == "urn:li:share:ok2":
            return {"status": 500, "error": "nope"}  # -> comment generic error (200)
        if urn == "urn:li:share:ok3":
            return {"status": 403}  # -> comment 401/403 (197-198)
        return {"ok": True}

    monkeypatch.setattr(ENG, "task_session", lambda: _CM())
    monkeypatch.setattr(ENG.linkedin_feed, "list_posts", fake_list)
    monkeypatch.setattr(ENG.linkedin_feed, "react", fake_react)
    monkeypatch.setattr(ENG.linkedin_feed, "comment", fake_comment)
    monkeypatch.setattr(ENG, "_already_acted", AsyncMock(side_effect=[False, False, False, True, False, False]))

    out = await ENG._run()
    assert out["skipped_age"] == 1
    assert out["skipped_dedup"] == 1
    assert out["reacted"] == 3
    assert out["commented"] == 2
    assert any("list_posts 500" in e for e in out["errors"])
    assert any("HTTP 401" in e for e in out["errors"])
    assert any("nope" in e for e in out["errors"])
    assert any("react-boom" in e for e in out["errors"])
    assert any("HTTP 403" in e for e in out["errors"])


@pytest.mark.asyncio
async def test_run_comment_generic_error_and_caps(monkeypatch):
    monkeypatch.setenv("LINKEDIN_ENGAGE_AUTHORS", "urn:li:org:1")
    monkeypatch.setenv("LINKEDIN_ENGAGE_COMMENT_TEMPLATES", "t1")

    team = SimpleNamespace(id="team-1")
    acct = SimpleNamespace(account_type="person", id="acc-p")
    call_n = {"n": 0}

    class _CM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, q):
            call_n["n"] += 1
            if call_n["n"] == 1:
                return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: team))
            if call_n["n"] == 2:
                return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [acct]))
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

        def add(self, obj):
            pass

        async def commit(self):
            pass

    async def fake_list(author, count=5):
        return {
            "status": 200,
            "posts": [
                {"id": "urn:li:share:cap1", "publishedAt": {"time": datetime.now(UTC).timestamp() * 1000}},
                {"id": "urn:li:share:cap2", "publishedAt": {"time": datetime.now(UTC).timestamp() * 1000}},
            ],
        }

    async def fake_react(kind, urn, rtype):
        return {"status": 503, "error": "down"}  # generic react error, not 401/403

    async def fake_comment(kind, urn, text):
        return {"status": 500, "error": "nope"}  # generic comment error

    monkeypatch.setattr(ENG, "task_session", lambda: _CM())
    monkeypatch.setattr(ENG.linkedin_feed, "list_posts", fake_list)
    monkeypatch.setattr(ENG.linkedin_feed, "react", fake_react)
    monkeypatch.setattr(ENG.linkedin_feed, "comment", fake_comment)
    monkeypatch.setattr(ENG, "_already_acted", AsyncMock(return_value=False))
    monkeypatch.setattr(ENG, "_actions_today", AsyncMock(return_value=(ENG.MAX_REACTIONS_DAY, ENG.MAX_COMMENTS_DAY)))

    out = await ENG._run()
    assert out["skipped_cap"] == 2


def test_task_wrapper_calls_run_async(monkeypatch):
    sentinel = object()

    def fake_run_async(coro):
        coro.close()
        return sentinel

    monkeypatch.setattr(ENG, "run_async", fake_run_async)
    assert ENG.engage_linkedin_watchlist.run() is sentinel
