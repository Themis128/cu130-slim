"""X capacity errors DEFER the X target; permanent errors still fail.

Covers the 2026-10-04 incident: X API credits ran out before the web
fallback was configured, the target was soft-skipped, the queue row
completed and the posts never retried.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.models.queue import QueueStatus
from app.services import publishing as pub
from app.services import x_web
from app.services.twitter_api import TwitterAPIError
from app.worker.tasks import publishing as wp


class _Resp:
    def __init__(self, status_code, body, headers=None):
        self.status_code = status_code
        self._body = body
        self.text = str(body)
        self.headers = headers or {}

    def json(self):
        return self._body


class _Client:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None, **kw):
        self.calls.append({"url": url, "json": json})
        return self.responses.pop(0) if self.responses else _Resp(500, "none")


@pytest.fixture
def account():
    return SimpleNamespace(account_id="42", username="TBaltzakis", id=uuid.uuid4())


def _patch_api(resp):
    fake = _Client([resp])
    return patch("app.services.twitter_api.httpx.AsyncClient", new=lambda timeout=30.0: fake)


# ── service layer: defer vs permanent ────────────────────────────────────────


@pytest.mark.asyncio
async def test_credits_depleted_without_fallback_defers(account, monkeypatch):
    """No x_web, browser bridge unavailable → deferred, not skipped/failed."""
    monkeypatch.setattr(x_web, "is_configured", lambda: False)
    monkeypatch.setattr(
        pub,
        "_publish_twitter_via_browser",
        AsyncMock(return_value=pub.PublishResult(success=False, skipped=True, error="bridge down")),
    )
    before = datetime.now(UTC)
    with _patch_api(_Resp(402, {"title": "CreditsDepleted", "detail": "credits-depleted"})):
        res = await pub._publish_twitter("tok", "Hello!", account, SimpleNamespace(), [])
    assert not res.success and not res.skipped and not res.permanent
    assert res.retry_after is not None and res.retry_after > before
    assert "deferred" in res.error.lower()


@pytest.mark.asyncio
async def test_429_rate_limit_defers_to_reset_header(account, monkeypatch):
    monkeypatch.setattr(x_web, "is_configured", lambda: False)
    monkeypatch.setattr(
        pub,
        "_publish_twitter_via_browser",
        AsyncMock(return_value=pub.PublishResult(success=False, skipped=True, error="bridge down")),
    )
    reset = int((datetime.now(UTC) + timedelta(minutes=12)).timestamp())
    resp = _Resp(
        429,
        {"title": "Too Many Requests", "type": "https://api.x.com/2/problems/rate-limit-exceeded"},
        headers={"x-rate-limit-reset": str(reset), "x-rate-limit-remaining": "0"},
    )
    with _patch_api(resp):
        res = await pub._publish_twitter("tok", "Hello!", account, SimpleNamespace(), [])
    assert res.retry_after is not None and not res.permanent and not res.skipped
    assert abs(res.retry_after.timestamp() - (reset + 30)) < 2


def test_retry_at_usage_capped_ignores_15min_window():
    """usage-capped: x-rate-limit-reset describes the short window, not the cap."""
    soon = int((datetime.now(UTC) + timedelta(minutes=5)).timestamp())
    exc = TwitterAPIError(
        429,
        '{"type":"https://api.x.com/2/problems/usage-capped"}',
        "u",
        headers={"x-rate-limit-reset": str(soon)},
    )
    assert pub._x_retry_at(exc) is None  # no real reset time → caller backs off
    res = pub._x_capacity_defer_result("usage-capped", pub._x_retry_at(exc))
    assert res.retry_after > datetime.now(UTC) + timedelta(minutes=1)  # backoff, not 5 min


def test_retry_at_24h_user_cap_header():
    reset = int((datetime.now(UTC) + timedelta(hours=7)).timestamp())
    exc = TwitterAPIError(
        429,
        "Too Many Requests",
        "u",
        headers={"x-user-limit-24hour-reset": str(reset), "x-user-limit-24hour-remaining": "0"},
    )
    when = pub._x_retry_at(exc)
    assert when is not None and abs(when.timestamp() - (reset + 30)) < 2


def test_raise_for_status_keeps_rate_limit_headers():
    from app.services.twitter_api import TwitterAPIClient

    c = TwitterAPIClient(access_token="t")
    resp = _Resp(429, "slow down", headers={"X-Rate-Limit-Reset": "123", "Content-Type": "x"})
    with pytest.raises(TwitterAPIError) as ei:
        c._raise_for_status(resp, "https://api.x.com/2/tweets")
    assert ei.value.headers == {"x-rate-limit-reset": "123"}


@pytest.mark.asyncio
async def test_fallback_cap_reached_defers_to_slot_time(account, monkeypatch):
    slot = datetime.now(UTC) + timedelta(hours=3)
    monkeypatch.setattr(x_web, "is_configured", lambda: True)
    monkeypatch.setattr(
        x_web,
        "publish_via_x_web",
        AsyncMock(return_value=x_web.XWebOutcome(status="deferred", error="X web daily cap reached (5/5)", retry_after=slot)),
    )
    browser = AsyncMock()
    monkeypatch.setattr(pub, "_publish_twitter_via_browser", browser)
    with _patch_api(_Resp(402, {"detail": "credits-depleted"})):
        res = await pub._publish_twitter("tok", "Hello!", account, SimpleNamespace(), [])
    assert res.retry_after == slot and not res.permanent and not res.skipped
    browser.assert_not_awaited()


@pytest.mark.asyncio
async def test_fallback_breaker_open_defers_to_reopen_time(account, monkeypatch):
    reopen = datetime.now(UTC) + timedelta(hours=5)
    monkeypatch.setattr(x_web, "is_configured", lambda: True)
    monkeypatch.setattr(
        x_web,
        "publish_via_x_web",
        AsyncMock(return_value=x_web.XWebOutcome(status="breaker", error="X web circuit breaker open", retry_after=reopen)),
    )
    with _patch_api(_Resp(402, {"detail": "credits-depleted"})):
        res = await pub._publish_twitter("tok", "Hello!", account, SimpleNamespace(), [])
    assert res.retry_after == reopen and not res.permanent


@pytest.mark.asyncio
async def test_fallback_lock_challenge_stays_permanent(account, monkeypatch):
    monkeypatch.setattr(x_web, "is_configured", lambda: True)
    monkeypatch.setattr(
        x_web,
        "publish_via_x_web",
        AsyncMock(return_value=x_web.XWebOutcome(status="tripped", error="LockedAccount — circuit breaker tripped for 6h")),
    )
    with _patch_api(_Resp(402, {"detail": "credits-depleted"})):
        res = await pub._publish_twitter("tok", "Hello!", account, SimpleNamespace(), [])
    assert res.permanent and res.retry_after is None


@pytest.mark.parametrize("status", ["identity", "media_error", "too_long", "ambiguous"])
@pytest.mark.asyncio
async def test_fallback_permanent_statuses(account, monkeypatch, status):
    monkeypatch.setattr(x_web, "is_configured", lambda: True)
    monkeypatch.setattr(
        x_web,
        "publish_via_x_web",
        AsyncMock(return_value=x_web.XWebOutcome(status=status, error=f"X web: {status}")),
    )
    with _patch_api(_Resp(402, {"detail": "credits-depleted"})):
        res = await pub._publish_twitter("tok", "Hello!", account, SimpleNamespace(), [])
    assert res.permanent and res.retry_after is None


@pytest.mark.asyncio
async def test_invalid_media_is_permanent_and_never_deferred(account, tmp_path):
    bad = tmp_path / "x.bin"
    bad.write_bytes(b"not an image")
    res = await pub._publish_twitter("tok", "Hello!", account, SimpleNamespace(media_ids=["m"]), [str(bad)])
    assert res.permanent and res.retry_after is None


@pytest.mark.asyncio
async def test_missing_media_never_publishes(account):
    res = await pub._publish_twitter("tok", "Hello!", account, SimpleNamespace(media_ids=["a", "b"]), [])
    assert not res.success and res.retry_after is None and "incomplete" in res.error


@pytest.mark.asyncio
async def test_publish_to_platform_empty_text_not_deferred(monkeypatch):
    monkeypatch.setattr(pub, "decrypt_token", lambda enc: "tok")
    monkeypatch.setattr(pub, "auto_correct", AsyncMock(return_value=None))
    acc = SimpleNamespace(platform="twitter", access_token_enc=b"enc")
    post = SimpleNamespace(content_text="", platform_specific={}, hashtags=[], link_url=None, media_ids=[])
    res = await pub.publish_to_platform(acc, post, db=SimpleNamespace())
    assert not res.success and res.retry_after is None


def test_x_web_capacity_reason_classification():
    assert x_web.is_capacity_reason("HTTP 429: Too Many Requests")
    assert x_web.is_capacity_reason("X error 344: daily limit")
    assert x_web.is_capacity_reason("RateLimitReached: slow down")
    assert not x_web.is_capacity_reason("LockedAccount: account locked")
    assert not x_web.is_capacity_reason("HTTP 401: could not authenticate you")
    assert not x_web.is_capacity_reason("X error 326: challenge")


@pytest.mark.asyncio
async def test_x_web_rate_limit_trip_is_deferred(monkeypatch):
    """A 429 from x.com trips the breaker (pauses the session) but the post defers."""
    s = SimpleNamespace(
        X_WEB_FALLBACK_ENABLED=True,
        X_WEB_AUTH_TOKEN="a",
        X_WEB_CT0="c",
        X_WEB_COOKIES_JSON="",
        X_WEB_PROXY="",
        X_WEB_MAX_POSTS_PER_DAY=5,
        X_WEB_MIN_GAP_MINUTES=0,
        X_WEB_MAX_GAP_MINUTES=0,
        X_WEB_BREAKER_HOURS=6.0,
        X_WEB_STATE_FILE="/tmp/x_web_state_defer_test.json",
        REDIS_URL="redis://invalid:1/0",
        SLACK_ALERTS_WEBHOOK_URL="",
    )
    monkeypatch.setattr(x_web, "get_settings", lambda: s)
    guard = x_web.XWebGuard(store=x_web.MemoryGuardStore())
    monkeypatch.setattr(x_web, "_slack_alert", AsyncMock())

    class _TooMany(Exception):
        status_code = 429

    class _C:
        user = SimpleNamespace(username="TBaltzakis")

        async def create_tweet(self, *a, **k):
            raise _TooMany("Too Many Requests")

    async def factory():
        return _C()

    out = await x_web.publish_via_x_web(
        expected_username="TBaltzakis",
        chunks=["hi"],
        media_paths=[],
        guard=guard,
        client_factory=factory,
    )
    assert out.status == "deferred" and out.retry_after is not None
    assert (await guard.breaker_state())[0] is True


# ── worker: bounded deferral ─────────────────────────────────────────────────


def _item(created_at=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        created_at=created_at or datetime.now(UTC),
        status=QueueStatus.PROCESSING,
        scheduled_at=datetime.now(UTC),
        locked_at=datetime.now(UTC),
        locked_by="w",
        attempts=0,
        max_attempts=3,
    )


def test_deferral_reschedules_at_slot_time_and_keeps_target_pending():
    now = datetime.now(UTC)
    slot = now + timedelta(hours=2)
    item, target, post = _item(), SimpleNamespace(status="pending", error_message=None), SimpleNamespace(platform_specific={})
    acct = uuid.uuid4()
    gave_up = wp._apply_capacity_deferral(
        item=item,
        target=target,
        post=post,
        account_id=acct,
        retry_after=slot,
        error="X web daily cap reached",
        now=now,
        max_hours=72,
        max_count=500,
    )
    assert gave_up is None
    assert item.status == QueueStatus.PENDING and item.scheduled_at == slot
    assert item.locked_at is None and item.locked_by is None and item.attempts == 0
    assert target.status == "pending" and "Deferred #1" in target.error_message
    state = post.platform_specific["publish_defer"][str(acct)]
    assert state["count"] == 1 and state["queue_id"] == str(item.id)


def test_deferral_clamped_to_deadline_and_min_one_minute():
    now = datetime.now(UTC)
    item = _item(created_at=now - timedelta(hours=71))
    post = SimpleNamespace(platform_specific={})
    wp._apply_capacity_deferral(
        item=item,
        target=None,
        post=post,
        account_id="a",
        retry_after=now + timedelta(days=20),
        error="credits",
        now=now,
        max_hours=72,
        max_count=500,
    )
    assert item.scheduled_at == item.created_at + timedelta(hours=72)
    item2 = _item()
    wp._apply_capacity_deferral(
        item=item2,
        target=None,
        post=SimpleNamespace(platform_specific={}),
        account_id="a",
        retry_after=now - timedelta(hours=1),
        error="gap",
        now=now,
        max_hours=72,
        max_count=500,
    )
    assert item2.scheduled_at == now + timedelta(minutes=1)


def test_deferral_max_age_gives_up_with_clear_error():
    now = datetime.now(UTC)
    item = _item(created_at=now - timedelta(hours=73))
    gave_up = wp._apply_capacity_deferral(
        item=item,
        target=None,
        post=SimpleNamespace(platform_specific={}),
        account_id="a",
        retry_after=now + timedelta(hours=1),
        error="X web daily cap reached",
        now=now,
        max_hours=72,
        max_count=500,
    )
    assert gave_up and "deferral limit reached" in gave_up and "daily cap" in gave_up
    assert item.status == QueueStatus.PROCESSING  # caller fails it


def test_deferral_max_count_gives_up():
    now = datetime.now(UTC)
    item = _item()
    post = SimpleNamespace(platform_specific={})
    for _ in range(3):
        assert (
            wp._apply_capacity_deferral(
                item=item,
                target=None,
                post=post,
                account_id="a",
                retry_after=now,
                error="gap",
                now=now,
                max_hours=72,
                max_count=3,
            )
            is None
        )
    assert wp._apply_capacity_deferral(
        item=item,
        target=None,
        post=post,
        account_id="a",
        retry_after=now,
        error="gap",
        now=now,
        max_hours=72,
        max_count=3,
    )


def test_deferral_state_is_per_account_and_cleared_on_success():
    now = datetime.now(UTC)
    post = SimpleNamespace(platform_specific={"twitter": {"text": "keep"}})
    wp._apply_capacity_deferral(
        item=_item(),
        target=None,
        post=post,
        account_id="x-acct",
        retry_after=now,
        error="cap",
        now=now,
        max_hours=72,
        max_count=5,
    )
    assert "x-acct" in post.platform_specific["publish_defer"]
    wp._clear_deferral_state(post, "x-acct")
    assert "publish_defer" not in post.platform_specific
    assert post.platform_specific["twitter"] == {"text": "keep"}


# ── sweep classification ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,expected",
    [
        ("X free tier monthly write quota / API credits exhausted. Reconnect will not fix this", True),
        ("X web daily cap reached (5/5 posts in 24h); next slot 2026-10-04", True),
        ("X web circuit breaker open until 2026-10-04 (HTTP 429)", True),
        ("X capacity deferral limit reached — gave up after 3 deferral(s)", False),
        ("LockedAccount — circuit breaker tripped for 6h", False),
        ("Duplicate content: matches published post abcd1234", False),
        ("X post media invalid — not publishing: bad", False),
        ("X browser session is logged in as @other, expected @them", False),
        (None, False),
    ],
)
def test_sweep_capacity_error_classification(text, expected):
    assert wp._is_x_capacity_error(text) is expected
