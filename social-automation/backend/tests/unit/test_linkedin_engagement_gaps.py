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


# ── _warmup_factor health-probe branches ────────────────────────────────


def _health(monkeypatch, payload):
    import httpx

    monkeypatch.setattr(httpx, "get", lambda *a, **k: SimpleNamespace(json=lambda: payload))


def test_warmup_factor_cooldown_and_ramp(monkeypatch):
    now_ms = datetime.now(UTC).timestamp() * 1000
    # still cooling → 0.0 (writes halted)
    _health(monkeypatch, {"rate_limit_until": now_ms + 60_000})
    assert ENG._warmup_factor() == 0.0
    # no cooldown recorded → 1.0
    _health(monkeypatch, {})
    assert ENG._warmup_factor() == 1.0
    # cooldown ended long ago → fully warm
    _health(monkeypatch, {"cooldown_ended_at": now_ms - (ENG.WARMUP_DAYS + 1) * 86_400_000})
    assert ENG._warmup_factor() == 1.0
    # cooldown ended 1 day ago → ramp ~0.9
    _health(monkeypatch, {"cooldown_ended_at": now_ms - 86_400_000})
    factor = ENG._warmup_factor()
    assert 0.3 <= factor < 1.0
    # probe failure → fail-open 1.0
    import httpx

    def _boom(*a, **k):
        raise RuntimeError("sidecar down")

    monkeypatch.setattr(httpx, "get", _boom)
    assert ENG._warmup_factor() == 1.0


# ── _run: warmup<=0 early return + org-page reaction pass ───────────────


@pytest.mark.asyncio
async def test_run_warmup_zero_skips_writes(monkeypatch):
    monkeypatch.setenv("LINKEDIN_ENGAGE_AUTHORS", "urn:li:org:1")
    team = SimpleNamespace(id="team-1")
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
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

        def add(self, obj):
            pass

        async def commit(self):
            pass

    monkeypatch.setattr(ENG, "task_session", lambda: _CM())
    monkeypatch.setattr(ENG, "_warmup_factor", lambda: 0.0)
    monkeypatch.setattr(ENG, "_actions_today", AsyncMock(return_value=(0, 0)))
    out = await ENG._run()
    assert out["warmup_factor"] == 0.0
    assert out["scanned"] == 0


@pytest.mark.asyncio
async def test_run_org_reaction_pass(monkeypatch):
    """Company-voice lane: org account reacts (never comments) on watchlist
    posts — ok / generic-error / 403-break / dedup-cap / no-urn arcs."""
    monkeypatch.setenv("LINKEDIN_ENGAGE_AUTHORS", "urn:li:org:1,urn:li:org:2")
    monkeypatch.setenv("LINKEDIN_ENGAGE_COMMENT_TEMPLATES", "")

    team = SimpleNamespace(id="team-1")
    person = SimpleNamespace(account_type="person", id="acc-p")
    org_acc = SimpleNamespace(account_type="organization", id="acc-o")
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
                return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [person, org_acc]))
            # org_rows journal query → no prior org reactions
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

        def add(self, obj):
            pass

        async def commit(self):
            pass

    call_n = {"n": 0}
    posts_a = [
        {"id": "urn:li:share:a-ok", "publishedAt": fresh},
        {"id": "urn:li:share:a-gen", "publishedAt": fresh},
        {"id": "urn:li:share:a-403", "publishedAt": fresh},
    ]
    posts_b = [
        {"id": "bogus-no-urn"},  # _post_urn None → skip
        {"id": "urn:li:share:b-old", "publishedAt": {"time": 1}},  # too old → age skip
        {"id": "urn:li:share:b-ok1", "publishedAt": fresh},
        {"id": "urn:li:share:b-ok2", "publishedAt": fresh},  # org-deduped
        {"id": "urn:li:share:b-ok3", "publishedAt": fresh},
        {"id": "urn:li:share:b-cap", "publishedAt": fresh},
    ]

    async def fake_list(author, count=5):
        return {"status": 200, "posts": posts_a if author == "urn:li:org:1" else posts_b}

    org_results = {
        "urn:li:share:a-ok": {"ok": True},
        "urn:li:share:a-gen": {"status": 500, "error": "org-boom"},
        "urn:li:share:a-403": {"status": 403},
        "urn:li:share:b-ok1": {"ok": True},
        "urn:li:share:b-ok2": {"ok": True},
        "urn:li:share:b-ok3": {"ok": True},
        "urn:li:share:b-cap": {"ok": True},
    }

    async def fake_react(kind, urn, rtype):
        if kind == "member":
            return {"ok": True}
        return org_results[urn]

    async def fake_acted(db, team_id, event_id):
        # only the org-pass dedup key for b-ok2 is already journaled
        return event_id == "org:urn:li:share:b-ok2"

    monkeypatch.setattr(ENG, "task_session", lambda: _CM())
    monkeypatch.setattr(ENG, "_warmup_factor", lambda: 1.0)
    monkeypatch.setattr(ENG, "_actions_today", AsyncMock(return_value=(0, 0)))
    monkeypatch.setattr(ENG, "_already_acted", fake_acted)
    monkeypatch.setattr(ENG.linkedin_feed, "list_posts", fake_list)
    monkeypatch.setattr(ENG.linkedin_feed, "react", fake_react)
    monkeypatch.setattr(ENG, "ORG_MAX_REACTIONS_DAY", 3)

    out = await ENG._run()
    # org pass: a-ok ok(1), a-gen error, a-403 error+break; b: bogus skip,
    # b-old age skip, b-ok1 ok(2), b-ok2 org-dedup skip, b-ok3 ok(3)=cap,
    # b-cap skipped by cap guard
    assert out["org_reacted"] == 3
    assert any("org react urn:li:share:a-gen: org-boom" in e for e in out["errors"])
    assert any("org react urn:li:share:a-403: HTTP 403" in e for e in out["errors"])
    # member lane also ran on all fresh posts (a×3 + b-ok1/2/3/cap = 7)
    assert out["reacted"] == 7
