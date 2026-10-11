"""Unit tests for app/services/linkedin_invites.py — invite-to-follow batch.

The real service code runs end to end; only the browser-bridge I/O
(``BrowserBridgeClient.evaluate``/``navigate``/``mouse_click``/
``start_session``), the DMR ``call_inference`` seam, the orchestrator
``browser_session`` lock, and ``asyncio.sleep`` are faked — CI has no
live bridge or DMR.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.linkedin_invites as LI


class _FakeBridge:
    """Routes evaluate() responses by JS-expression content."""

    def __init__(self, routes=None):
        self.routes = routes or {}
        self.clicks: list[tuple] = []
        self.navigated: list[str] = []
        self.started: list[dict] = []
        self._seq: dict[str, int] = {}

    async def evaluate(self, expr):
        for key, val in self.routes.items():
            if key in expr:
                if isinstance(val, tuple):
                    i = self._seq.get(key, 0)
                    self._seq[key] = i + 1
                    val = val[min(i, len(val) - 1)]
                return {"result": val}
        return {"result": None}

    async def mouse_click(self, x, y):
        self.clicks.append((x, y))

    async def navigate(self, url):
        self.navigated.append(url)

    async def start_session(self, *a, **kw):
        self.started.append(kw)


class _Session:
    def __init__(self):
        self.renew = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


@pytest.fixture(autouse=True)
def _sleep(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())


def _wire_batch(monkeypatch, client=None):
    """Wire send_invite_batch seams; returns the fake bridge client."""
    client = client or _FakeBridge()
    monkeypatch.setattr(LI, "get_settings",
                        lambda: SimpleNamespace(BROWSER_BRIDGE_URL="http://b"))
    monkeypatch.setattr(LI, "BrowserBridgeClient",
                        lambda *a, **k: client)
    monkeypatch.setattr(LI, "browser_session", lambda *a, **k: _Session())
    return client


# ── _eval + small helpers ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_eval_variants():
    c = _FakeBridge()
    c.evaluate = AsyncMock(return_value={"result": '{"a": 1}'})
    assert await LI._eval(c, "x") == {"a": 1}          # JSON string parsed
    c.evaluate = AsyncMock(return_value={"result": "plain"})
    assert await LI._eval(c, "x") == "plain"           # non-JSON str kept
    c.evaluate = AsyncMock(return_value={"result": 42})
    assert await LI._eval(c, "x") == 42                # native value
    c.evaluate = AsyncMock(return_value="raw")
    assert await LI._eval(c, "x") == "raw"             # non-dict res


@pytest.mark.asyncio
async def test_click_button_by_text():
    c = _FakeBridge({"offsetParent": {"x": 10, "y": 20}})
    assert await LI._click_button_by_text(c, "Show more results") is True
    assert c.clicks == [(10, 20)]

    c = _FakeBridge()  # no button found → null
    assert await LI._click_button_by_text(c, "X") is False


@pytest.mark.asyncio
async def test_selected_count():
    assert await LI._selected_count(
        _FakeBridge({"selected": "3 selected"})) == 3
    assert await LI._selected_count(_FakeBridge()) == 0
    assert await LI._selected_count(
        _FakeBridge({"selected": "none"})) == 0


@pytest.mark.asyncio
async def test_wait_for_dialog():
    c = _FakeBridge({"!!document.querySelector": [False, True]})
    assert await LI._wait_for_dialog(c, tries=3) is True
    c = _FakeBridge()
    assert await LI._wait_for_dialog(c, tries=2) is False


@pytest.mark.asyncio
async def test_login_wall():
    assert await LI._login_wall(
        _FakeBridge({"session_key": True})) is True
    assert await LI._login_wall(_FakeBridge()) is False

    class _ErrBridge(_FakeBridge):
        async def evaluate(self, expr):
            raise LI.BrowserBridgeError(503, "down")

    assert await LI._login_wall(_ErrBridge()) is False  # bridge error → False


@pytest.mark.asyncio
async def test_dialog_open_and_credits():
    c = _FakeBridge({"!!document.querySelector": True})
    assert await LI._dialog_open(c) is True

    class _ErrBridge(_FakeBridge):
        async def evaluate(self, expr):
            raise LI.BrowserBridgeError(503, "down")

    assert await LI._dialog_open(_ErrBridge()) is False

    c = _FakeBridge({"credits available": "12/50 credits available"})
    assert await LI._read_credits(c, 40) == 12
    c = _FakeBridge()  # no match → default
    assert await LI._read_credits(c, 40) == 40


@pytest.mark.asyncio
async def test_click_checkbox_at_index():
    c = _FakeBridge({"cbs[": True})
    assert await LI._click_checkbox_at_index(c, 2) is True
    c = _FakeBridge({"cbs[": False})
    assert await LI._click_checkbox_at_index(c, 2) is False


# ── harvest ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_harvest_candidates_pagination(monkeypatch):
    rows_page1 = [{"name": "Ada", "headline": "CTO"},
                  {"name": "Ada", "headline": "CTO"},   # deduped
                  {"name": "Bob", "headline": "DevOps"}]
    c = _FakeBridge({
        "input[type=checkbox]')].map": (
            rows_page1, [{"name": "Cid", "headline": "SRE"}]),
        "Show more results": ({"x": 1, "y": 2}, None),  # 2nd page: no button
        "input[type=checkbox]').length": 50,
    })
    out = await LI._harvest_candidates(c)
    assert [r["name"] for r in out] == ["Ada", "Bob", "Cid"]
    assert c.clicks == [(1, 2)]  # one pagination click

    # stale pagination: two rounds with no new rows → break
    c = _FakeBridge({
        "input[type=checkbox]')].map": [{"name": "A", "headline": "h"}],
        "Show more results": {"x": 1, "y": 1},
        "input[type=checkbox]').length": 1,
    })
    out = await LI._harvest_candidates(c)
    assert len(out) == 1

    # renew hook called per round
    c = _FakeBridge()
    renew = AsyncMock()
    await LI._harvest_candidates(c, renew)
    renew.assert_awaited()


# ── NLP scoring ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_score_candidates(monkeypatch):
    import app.services.inference as INF
    call = AsyncMock(side_effect=[
        {"response": {"scores": [{"i": 0, "s": 90}, {"i": 1, "s": 20}]}},
        {"response": '{"scores": [{"i": 40, "s": 77}]}'},
    ])
    monkeypatch.setattr(INF, "call_inference", call)
    cands = [{"i": i, "name": f"n{i}", "headline": "h"} for i in range(41)]
    scores = await LI._score_candidates(cands)
    assert scores == {0: 90, 1: 20, 40: 77}   # two chunks merged
    assert call.await_count == 2

    assert await LI._score_candidates([]) == {}

    call = AsyncMock(side_effect=RuntimeError("dmr down"))
    monkeypatch.setattr(INF, "call_inference", call)
    assert await LI._score_candidates(
        [{"i": 0, "name": "n", "headline": "h"}]) == {}


# ── invite dialog navigation ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_click_invite_anywhere(monkeypatch):
    # direct hit via the multi-role finder
    c = _FakeBridge({"invite connections|invite to follow": {"x": 5, "y": 6}})
    assert await LI._click_invite_anywhere(c) is True
    assert c.clicks == [(5, 6)]

    # plain-button fallback
    c = _FakeBridge({"offsetParent&&(e.innerText||'').trim()==='Invite connections'":
                     {"x": 7, "y": 8}})
    monkeypatch.setattr(LI, "_click_button_by_text", AsyncMock(
        return_value=True))
    assert await LI._click_invite_anywhere(c) is True

    # overflow-menu path: open ⋯ then click menu item
    monkeypatch.setattr(LI, "_click_button_by_text", AsyncMock(
        return_value=False))
    c = _FakeBridge({
        "more\\b|⋯|\\.\\.\\.": True,
        "role=menuitem": True,
    })
    assert await LI._click_invite_anywhere(c) is True

    # menu never opens → False
    c = _FakeBridge()
    assert await LI._click_invite_anywhere(c) is False


@pytest.mark.asyncio
async def test_open_invite_dialog(monkeypatch):
    # login wall on first URL → abort early (no more navs)
    c = _FakeBridge({"session_key": True})
    monkeypatch.setattr(LI, "_click_invite_anywhere", AsyncMock())
    assert await LI._open_invite_dialog(c) is False
    assert len(c.navigated) == 1

    # first URL fails to find control, second works
    c = _FakeBridge()
    click = AsyncMock(side_effect=[False, True])
    monkeypatch.setattr(LI, "_click_invite_anywhere", click)
    monkeypatch.setattr(LI, "_wait_for_dialog", AsyncMock(return_value=True))
    assert await LI._open_invite_dialog(c) is True
    assert len(c.navigated) == 2

    # navigate raises on every URL → False
    c = _FakeBridge()

    async def _boom(url):
        raise LI.BrowserBridgeError(500, "nav")
    c.navigate = _boom
    assert await LI._open_invite_dialog(c) is False

    # click works but dialog never appears → all surfaces tried → False
    c = _FakeBridge()
    monkeypatch.setattr(LI, "_click_invite_anywhere", AsyncMock(
        return_value=True))
    monkeypatch.setattr(LI, "_wait_for_dialog", AsyncMock(
        return_value=False))
    assert await LI._open_invite_dialog(c) is False
    assert len(c.navigated) == 3


# ── send_invite_batch: phase A skips ─────────────────────────────────


@pytest.mark.asyncio
async def test_batch_session_not_logged_in(monkeypatch):
    _wire_batch(monkeypatch, _FakeBridge({"location.href": "/login?x"}))
    out = await LI.send_invite_batch()
    assert out["status"] == "skipped"
    assert out["reason"] == "linkedin_session_not_logged_in"

    # checkpoint URL also triggers the skip; start_session contention → force
    c = _FakeBridge({"location.href": "checkpoint/x"})

    async def _contend(*a, **kw):
        if not kw.get("force"):
            raise LI.BrowserBridgeError(409, "busy")
    c.start_session = _contend
    _wire_batch(monkeypatch, c)
    out = await LI.send_invite_batch()
    assert out["reason"] == "linkedin_session_not_logged_in"


@pytest.mark.asyncio
async def test_batch_phase_a_skips(monkeypatch):
    _wire_batch(monkeypatch)

    monkeypatch.setattr(LI, "_open_invite_dialog", AsyncMock(
        return_value=False))
    out = await LI.send_invite_batch()
    assert out["reason"] == "invite_dialog_missing"

    monkeypatch.setattr(LI, "_open_invite_dialog", AsyncMock(
        return_value=True))
    monkeypatch.setattr(LI, "_read_credits", AsyncMock(return_value=0))
    out = await LI.send_invite_batch()
    assert out["reason"] == "no_credits" and out["credits_available"] == 0

    monkeypatch.setattr(LI, "_read_credits", AsyncMock(return_value=10))
    monkeypatch.setattr(LI, "_harvest_candidates", AsyncMock(
        return_value=[]))
    out = await LI.send_invite_batch()
    assert out["reason"] == "no_candidates" and out["candidates"] == 0


@pytest.mark.asyncio
async def test_batch_phase_a_errors(monkeypatch):
    _wire_batch(monkeypatch)
    monkeypatch.setattr(LI, "_open_invite_dialog",
                        AsyncMock(side_effect=LI.BrowserBridgeError(503, "x")))
    out = await LI.send_invite_batch()
    assert out["reason"] == "bridge:503"

    monkeypatch.setattr(LI, "_open_invite_dialog",
                        AsyncMock(side_effect=RuntimeError("weird")))
    out = await LI.send_invite_batch()
    assert out["reason"] == "RuntimeError"


# ── send_invite_batch: phase C ───────────────────────────────────────


def _phase_b(monkeypatch, scores=None, n=3):
    """Patch phase-A helpers to reach phase C with `n` candidates."""
    monkeypatch.setattr(LI, "_open_invite_dialog", AsyncMock(
        return_value=True))
    monkeypatch.setattr(LI, "_read_credits", AsyncMock(return_value=10))
    cands = [{"i": i, "name": f"n{i}", "headline": "h"}
             for i in range(n)]
    monkeypatch.setattr(LI, "_harvest_candidates", AsyncMock(
        return_value=cands))
    monkeypatch.setattr(LI, "_score_candidates", AsyncMock(
        return_value=scores if scores is not None else {}))


@pytest.mark.asyncio
async def test_batch_happy_path(monkeypatch):
    _wire_batch(monkeypatch, _FakeBridge({
        "location.href": "https://www.linkedin.com/company/x",
        "\\d+ selected": "3 selected",
        "offsetParent&&(e.innerText||'').trim().startsWith('Invite ')":
            {"x": 1, "y": 1},
        "document.body.innerText.slice": "All good. Invitations sent!",
        "rows.find": True,
    }))
    _phase_b(monkeypatch)
    out = await LI.send_invite_batch(batch_size=3)
    assert out["status"] == "sent" and out["sent"] == 3
    assert out["picked_clicked"] == 3

    # scored ranking puts highest score first
    _phase_b(monkeypatch, scores={0: 10, 1: 99, 2: 50})
    out = await LI.send_invite_batch(batch_size=2)
    assert out["top_scores"] == [99, 50]


@pytest.mark.asyncio
async def test_batch_dialog_lost_in_phase_c(monkeypatch):
    _wire_batch(monkeypatch)
    _phase_b(monkeypatch)
    monkeypatch.setattr(LI, "_dialog_open", AsyncMock(return_value=False))
    # phase A opens fine; phase C re-open fails → dialog lost
    monkeypatch.setattr(LI, "_open_invite_dialog", AsyncMock(
        side_effect=[True, False]))
    out = await LI.send_invite_batch()
    assert out["reason"] == "invite_dialog_lost"


@pytest.mark.asyncio
async def test_batch_session_stolen(monkeypatch):
    c = _wire_batch(monkeypatch, _FakeBridge())
    _phase_b(monkeypatch)
    monkeypatch.setattr(LI, "_dialog_open", AsyncMock(return_value=True))

    # checkbox click raises 409 → re-claim fails → stolen
    async def _eval409(expr):
        if "rows.find" in expr:
            raise LI.BrowserBridgeError(409, "stolen")
        return {"result": None}
    c.evaluate = _eval409
    # phase A + phase C entry ok; the post-409 re-claim raises → stolen
    c.start_session = AsyncMock(
        side_effect=[None, None, LI.BrowserBridgeError(409, "still busy")])
    out = await LI.send_invite_batch()
    assert out["reason"] == "browser_session_stolen"
    assert out["picked_clicked"] == 0

    # 409 → re-claim ok but dialog gone → invite_dialog_lost
    c.start_session = AsyncMock()
    monkeypatch.setattr(LI, "_dialog_open", AsyncMock(
        side_effect=[True, False]))
    out = await LI.send_invite_batch()
    assert out["reason"] == "invite_dialog_lost"


@pytest.mark.asyncio
async def test_batch_send_failures(monkeypatch):
    _wire_batch(monkeypatch, _FakeBridge({
        "rows.find": True,
        "\\d+ selected": "0 selected",
    }))
    _phase_b(monkeypatch)
    monkeypatch.setattr(LI, "_dialog_open", AsyncMock(return_value=True))
    out = await LI.send_invite_batch()
    assert out["reason"] == "nothing_selected"

    _wire_batch(monkeypatch, _FakeBridge({
        "rows.find": True,
        "\\d+ selected": "2 selected",
        # no Invite-button coords → click fails
    }))
    monkeypatch.setattr(LI, "_open_invite_dialog", AsyncMock(
        return_value=True))
    _phase_b(monkeypatch)
    monkeypatch.setattr(LI, "_dialog_open", AsyncMock(return_value=True))
    out = await LI.send_invite_batch()
    assert out["reason"] == "invite_send_button_missing"

    # click ok but confirmation text absent → send_unconfirmed
    _wire_batch(monkeypatch, _FakeBridge({
        "rows.find": True,
        "\\d+ selected": "2 selected",
        "startsWith('Invite ')": {"x": 1, "y": 1},
        "document.body.innerText.slice": "something else",
    }))
    monkeypatch.setattr(LI, "_open_invite_dialog", AsyncMock(
        return_value=True))
    _phase_b(monkeypatch)
    monkeypatch.setattr(LI, "_dialog_open", AsyncMock(return_value=True))
    out = await LI.send_invite_batch()
    assert out["reason"] == "send_unconfirmed"

    # BridgeError in phase C → bridge reason
    c = _wire_batch(monkeypatch)
    _phase_b(monkeypatch)
    monkeypatch.setattr(LI, "_dialog_open", AsyncMock(return_value=True))
    c.start_session = AsyncMock(
        side_effect=[None, LI.BrowserBridgeError(409, "x"),
                     LI.BrowserBridgeError(500, "y")])
    out = await LI.send_invite_batch()
    assert out["reason"] == "bridge:500"

    # generic error in phase C → type name
    _wire_batch(monkeypatch)
    _phase_b(monkeypatch)
    monkeypatch.setattr(LI, "_dialog_open",
                        AsyncMock(side_effect=ValueError("v")))
    out = await LI.send_invite_batch()
    assert out["reason"] == "ValueError"


@pytest.mark.asyncio
async def test_harvest_max_candidates_break():
    rows = [{"name": f"n{i}", "headline": "h"} for i in range(261)]
    c = _FakeBridge({"input[type=checkbox]')].map": rows})
    out = await LI._harvest_candidates(c)
    assert len(out) == 261  # _MAX_CANDIDATES break — no pagination round
    assert c.clicks == []


@pytest.mark.asyncio
async def test_open_dialog_bridge_error_continues(monkeypatch):
    monkeypatch.setattr(LI, "_login_wall", AsyncMock(return_value=False))
    monkeypatch.setattr(LI, "_click_invite_anywhere", AsyncMock(
        side_effect=[LI.BrowserBridgeError(500, "x"), True]))
    monkeypatch.setattr(LI, "_wait_for_dialog", AsyncMock(return_value=True))
    c = _FakeBridge()
    assert await LI._open_invite_dialog(c) is True
    assert len(c.navigated) == 2  # first surface errored → next URL tried


@pytest.mark.asyncio
async def test_batch_eval_bridge_errors(monkeypatch):
    c = _wire_batch(monkeypatch, _FakeBridge({
        "\\d+ selected": "2 selected",
        "startsWith('Invite ')": {"x": 1, "y": 1},
        "document.body.innerText.slice": "Invitations sent",
    }))
    _phase_b(monkeypatch)
    monkeypatch.setattr(LI, "_dialog_open", AsyncMock(return_value=True))

    # non-409 bridge error on a checkbox click → skip that pick, keep going
    calls = {"n": 0}

    async def _eval500(expr):
        if "rows.find" in expr:
            calls["n"] += 1
            if calls["n"] == 1:
                raise LI.BrowserBridgeError(500, "oops")
            return {"result": True}
        for key, val in c.routes.items():
            if key in expr:
                return {"result": val}
        return {"result": None}
    c.evaluate = _eval500
    out = await LI.send_invite_batch(batch_size=3)
    assert out["status"] == "sent"
    assert out["picked_clicked"] == 2

    # 409 → re-claim ok → dialog still open → continue clicking (line 454)
    calls["n"] = 0
    c.start_session = AsyncMock(side_effect=[None, None, None])

    async def _eval409once(expr):
        if "rows.find" in expr:
            calls["n"] += 1
            if calls["n"] == 1:
                raise LI.BrowserBridgeError(409, "preempted")
            return {"result": True}
        for key, val in c.routes.items():
            if key in expr:
                return {"result": val}
        return {"result": None}
    c.evaluate = _eval409once
    monkeypatch.setattr(LI, "_dialog_open", AsyncMock(return_value=True))
    out = await LI.send_invite_batch(batch_size=3)
    assert out["status"] == "sent"
    assert out["picked_clicked"] == 2
