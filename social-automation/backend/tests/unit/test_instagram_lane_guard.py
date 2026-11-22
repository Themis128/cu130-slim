"""Unit tests for the instagram publish-lane guard sweep."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.worker.tasks import instagram_lane_guard as G


def _acc(platform="instagram", team="t1", login_type="fb_onboarding", status="active", uid="acc-1", username="cloudless.gr"):
    a = MagicMock()
    a.platform = platform
    a.team_id = team
    a.meta_data = {"login_type": login_type}
    a.status = status
    a.id = uid
    a.username = username
    a.access_token_enc = b"x"
    return a


def _target(post_id="p1", account_id="acc-1", error="OAuthException #10"):
    t = MagicMock()
    t.post_id = post_id
    t.social_account_id = account_id
    t.status = "failed"
    t.error_message = error
    return t


def _result(rows=None, one=None):
    """db.execute() stand-in supporting both result shapes."""
    r = MagicMock()
    r.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=rows or [])))
    r.scalar_one_or_none = MagicMock(return_value=one)
    return r


class _Resp:
    def __init__(self, status_code=200):
        self.status_code = status_code


class _Client:
    def __init__(self, resp=None, exc=None):
        self.resp, self.exc = resp, exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, *a, **k):
        if self.exc:
            raise self.exc
        return self.resp


class _Sess:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *a):
        return False


def _sess(db):
    return lambda: _Sess(db)


# ── _is_permission_failure ───────────────────────────────────────────


@pytest.mark.parametrize(
    "err",
    [
        "OAuthException (#10) Requires instagram_content_publish",
        "requires instagram_content_publish permission",
        "user does not have permission for this action",
    ],
)
def test_permission_markers_match(err):
    assert G._is_permission_failure(err)


@pytest.mark.parametrize("err", [None, "", "caption exceeds 2200 characters", "media aspect ratio invalid"])
def test_permission_markers_ignore_content_errors(err):
    assert not G._is_permission_failure(err)


# ── _token_alive ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_token_alive_200():
    with (
        patch.object(G, "decrypt_token", return_value="tok"),
        patch.object(G.httpx, "AsyncClient", lambda **k: _Client(_Resp(200))),
    ):
        assert await G._token_alive(_acc())


@pytest.mark.asyncio
async def test_token_alive_non200_and_exception():
    with (
        patch.object(G, "decrypt_token", return_value="tok"),
        patch.object(G.httpx, "AsyncClient", lambda **k: _Client(_Resp(401))),
    ):
        assert not await G._token_alive(_acc())
    with (
        patch.object(G, "decrypt_token", return_value="tok"),
        patch.object(G.httpx, "AsyncClient", lambda **k: _Client(exc=RuntimeError("boom"))),
    ):
        assert not await G._token_alive(_acc())


# ── _sweep ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sweep_no_accounts():
    db = MagicMock()
    db.execute = AsyncMock(return_value=_result(rows=[]))
    db.commit = AsyncMock()
    with patch.object(G, "task_session", _sess(db)):
        assert await G._sweep() == {"repointed": 0, "dead_business_login": []}


@pytest.mark.asyncio
async def test_sweep_dead_business_login_alerts():
    """BL row present but token dead → dead list + alert, no repoint."""
    bl = _acc(login_type="business_login", uid="bl-1")
    fb = _acc(uid="fb-1")
    calls = iter([_result(rows=[bl, fb]), _result(rows=[bl])])
    db = MagicMock()
    db.execute = AsyncMock(side_effect=lambda *a, **k: next(calls))
    db.commit = AsyncMock()
    with (
        patch.object(G, "task_session", _sess(db)),
        patch.object(G, "_token_alive", new=AsyncMock(return_value=False)),
        patch("app.services.slack_notifications.post_alert_to_slack", new=AsyncMock()) as alert,
    ):
        out = await G._sweep()
    assert out["repointed"] == 0
    assert "cloudless.gr" in out["dead_business_login"]
    alert.assert_awaited_once()


@pytest.mark.asyncio
async def test_sweep_repoints_permission_failures():
    """Healthy BL + failed target on fb row with #10 → repoint updates run."""
    bl = _acc(login_type="business_login", uid="bl-1")
    fb = _acc(uid="fb-1")
    tgt = _target(post_id="p1", account_id="fb-1")
    calls = iter(
        [
            _result(rows=[fb, bl]),  # all IG accounts
            _result(rows=[bl]),  # business_login rows
            _result(rows=[tgt]),  # failed targets
            _result(one=None),  # active queue check → none
            _result(one=None),  # existing BL target → none
            _result(),  # queue update
            _result(),  # target update
        ]
    )
    db = MagicMock()
    db.execute = AsyncMock(side_effect=lambda *a, **k: next(calls))
    db.commit = AsyncMock()
    with (
        patch.object(G, "task_session", _sess(db)),
        patch.object(G, "_token_alive", new=AsyncMock(return_value=True)),
        patch("app.services.slack_notifications.post_alert_to_slack", new=AsyncMock()) as alert,
    ):
        out = await G._sweep()
    assert out["repointed"] == 1
    assert db.execute.await_count == 7  # 3 selects + 2 checks + 2 updates
    alert.assert_awaited_once()


@pytest.mark.asyncio
async def test_sweep_ignores_content_failures():
    """Failed target with a non-permission error is left alone."""
    bl = _acc(login_type="business_login", uid="bl-1")
    fb = _acc(uid="fb-1")
    tgt = _target(error="caption exceeds 2200 characters")
    calls = iter([_result(rows=[fb, bl]), _result(rows=[bl]), _result(rows=[tgt])])
    db = MagicMock()
    db.execute = AsyncMock(side_effect=lambda *a, **k: next(calls))
    db.commit = AsyncMock()
    with (
        patch.object(G, "task_session", _sess(db)),
        patch.object(G, "_token_alive", new=AsyncMock(return_value=True)),
        patch("app.services.slack_notifications.post_alert_to_slack", new=AsyncMock()) as alert,
    ):
        out = await G._sweep()
    assert out == {"repointed": 0, "dead_business_login": []}
    alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_sweep_retires_target_when_bl_owns_post():
    """BL already has the post → broken lane cancelled, target skipped."""
    bl = _acc(login_type="business_login", uid="bl-1")
    fb = _acc(uid="fb-1")
    tgt = _target(post_id="p1", account_id="fb-1")
    calls = iter(
        [
            _result(rows=[fb, bl]),
            _result(rows=[bl]),
            _result(rows=[tgt]),
            _result(one=MagicMock()),  # active queue row exists on BL lane
            _result(one=None),  # existing BL target check (short-circuit is post-query)
            _result(),  # cancel broken-lane queue rows
        ]
    )
    db = MagicMock()
    db.execute = AsyncMock(side_effect=lambda *a, **k: next(calls))
    db.commit = AsyncMock()
    with (
        patch.object(G, "task_session", _sess(db)),
        patch.object(G, "_token_alive", new=AsyncMock(return_value=True)),
        patch("app.services.slack_notifications.post_alert_to_slack", new=AsyncMock()),
    ):
        out = await G._sweep()
    assert out["repointed"] == 1
    assert tgt.status == "skipped"
    assert tgt.error_message == "superseded by business_login lane"


def test_run_task_delegates():
    with patch.object(G, "run_async", return_value={"repointed": 0}) as run:
        assert G.run_instagram_lane_guard() == {"repointed": 0}
    run.assert_called_once()
