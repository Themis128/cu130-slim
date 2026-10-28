"""Coverage: owner_gates — gate listing, reply consume, digest-on-change."""

from unittest.mock import AsyncMock, MagicMock

import pytest


def _client(responses):
    """responses: list of (json, status) returned in order."""
    c = MagicMock()
    it = iter(responses)

    async def _req(method, url, **kw):
        try:
            body, status = next(it)
        except StopIteration:
            body, status = [], 200
        r = MagicMock(status_code=status)
        r.json = MagicMock(return_value=body)
        return r

    c.request = _req
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    return c


def _issue(n, title="gate"):
    return {"number": n, "title": title, "pull_request": None}


def _comment(login, body):
    return {"user": {"login": login}, "body": body}


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    from app.worker.tasks import owner_gates as M

    monkeypatch.setattr(M, "STATE", tmp_path / "st.json")


@pytest.mark.asyncio
async def test_token_from_env(monkeypatch):
    from app.worker.tasks import owner_gates as M

    monkeypatch.setenv("GITHUB_TOKEN", "gh")
    assert M._token() == "gh"


@pytest.mark.asyncio
async def test_token_fallback_settings(monkeypatch):
    from app.worker.tasks import owner_gates as M

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr("app.core.config.get_settings", lambda: MagicMock(GITHUB_TOKEN="s3"))
    assert M._token() == "s3"


@pytest.mark.asyncio
async def test_token_none(monkeypatch):
    from app.worker.tasks import owner_gates as M

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr("app.core.config.get_settings", MagicMock(side_effect=RuntimeError("x")))
    assert M._token() == ""


@pytest.mark.asyncio
async def test_gh_error_returns_none(monkeypatch):
    from app.worker.tasks import owner_gates as M

    monkeypatch.setattr(M.httpx, "AsyncClient", lambda *a, **k: _client([({}, 403)]))
    assert await M._gh("t", "/x") is None


@pytest.mark.asyncio
async def test_open_gates_filters_prs(monkeypatch):
    from app.worker.tasks import owner_gates as M

    pr = _issue(9)
    pr["pull_request"] = {"url": "x"}
    monkeypatch.setattr(M.httpx, "AsyncClient", lambda *a, **k: _client([([_issue(1), pr], 200)]))
    out = await M._open_gates("t")
    assert [i["number"] for i in out] == [1]


@pytest.mark.asyncio
async def test_latest_owner_comment(monkeypatch):
    from app.worker.tasks import owner_gates as M

    cs = [_comment("bot", "a"), _comment(M.OWNER, "done")]
    monkeypatch.setattr(M.httpx, "AsyncClient", lambda *a, **k: _client([(cs, 200)]))
    assert await M._latest_owner_comment("t", 1) == "done"


@pytest.mark.asyncio
async def test_latest_owner_comment_none(monkeypatch):
    from app.worker.tasks import owner_gates as M

    monkeypatch.setattr(M.httpx, "AsyncClient", lambda *a, **k: _client([([_comment("bot", "a")], 200)]))
    assert await M._latest_owner_comment("t", 1) == ""


def test_state_roundtrip(tmp_path, monkeypatch):
    from app.worker.tasks import owner_gates as M

    monkeypatch.setattr(M, "STATE", tmp_path / "x.json")
    assert M._load_state() == {}
    M._save_state({"a": 1})
    assert M._load_state() == {"a": 1}


def test_state_corrupt(tmp_path, monkeypatch):
    from app.worker.tasks import owner_gates as M

    p = tmp_path / "x.json"
    p.write_text("{bad")
    monkeypatch.setattr(M, "STATE", p)
    assert M._load_state() == {}


def _wire_poll(monkeypatch, M, calls, posted):
    """calls = list of responses consumed by _gh in order."""
    seq = list(calls)
    monkeypatch.setattr(M, "_open_gates", AsyncMock(side_effect=lambda t: seq[0] if isinstance(seq[0], list) else seq[0]))
    comments = seq[1] if len(seq) > 1 else {}
    close_calls = []
    monkeypatch.setattr(M, "_latest_owner_comment", AsyncMock(side_effect=lambda t, n: comments.get(n, "")))

    async def _gh(t, path, method="GET", **kw):
        if method == "PATCH":
            close_calls.append(path)
        return {}

    monkeypatch.setattr(M, "_gh", _gh)
    monkeypatch.setattr("app.services.slack_notifications.post_alert_to_slack", AsyncMock(side_effect=lambda m: posted.append(m)))
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    return close_calls


@pytest.mark.asyncio
async def test_poll_done_closes(monkeypatch):
    from app.worker.tasks import owner_gates as M

    posted = []
    closes = _wire_poll(monkeypatch, M, [[_issue(5)], {5: "done"}], posted)
    out = await M._poll()
    assert out["actions"] == 1 and closes == ["/repos/Themis128/cu130-slim/issues/5"]
    assert "closed" in posted[0]


@pytest.mark.asyncio
async def test_poll_approve_marks(monkeypatch):
    from app.worker.tasks import owner_gates as M

    posted = []
    closes = _wire_poll(monkeypatch, M, [[_issue(7)], {7: "approved"}], posted)
    out = await M._poll()
    assert out["actions"] == 1 and not closes
    assert "approved" in posted[0]


@pytest.mark.asyncio
async def test_poll_idempotent(monkeypatch):
    from app.worker.tasks import owner_gates as M

    posted = []
    _wire_poll(monkeypatch, M, [[_issue(5)], {5: "done"}], posted)
    await M._poll()
    posted.clear()
    out = await M._poll()
    assert out["actions"] == 0 and not posted


@pytest.mark.asyncio
async def test_poll_digest_on_change(monkeypatch):
    from app.worker.tasks import owner_gates as M

    posted = []
    _wire_poll(monkeypatch, M, [[_issue(1), _issue(2)], {}], posted)
    out = await M._poll()
    assert posted and "Owner gates" in posted[0] and out["open"] == 2


@pytest.mark.asyncio
async def test_poll_no_token(monkeypatch):
    from app.worker.tasks import owner_gates as M

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(M, "_token", lambda: "")
    assert (await M._poll())["skip"].startswith("no")


@pytest.mark.asyncio
async def test_poll_other_reply_ignored(monkeypatch):
    from app.worker.tasks import owner_gates as M

    posted = []
    _wire_poll(monkeypatch, M, [[_issue(3)], {3: "will look tomorrow"}], posted)
    out = await M._poll()
    assert out["actions"] == 0


def test_wrapper_error(monkeypatch):
    from app.worker.tasks import owner_gates as M

    monkeypatch.setattr(M, "run_async", MagicMock(side_effect=RuntimeError("x")))
    assert M.poll_owner_gates()["error"] == "x"


def test_wrapper_ok(monkeypatch):
    from app.worker.tasks import owner_gates as M

    monkeypatch.setattr(M, "run_async", lambda c: (c.close(), {"open": 0})[1])
    assert M.poll_owner_gates() == {"open": 0}
