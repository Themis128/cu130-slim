from unittest.mock import AsyncMock, patch

import pytest

from app.services import session_healer


def _fake_redis(store=None):
    """Minimal async redis stand-in: set(nx/ex)/get/delete/aclose."""
    store = store if store is not None else {}
    r = AsyncMock()

    async def _set(key, value, nx=False, ex=None):
        if nx and key in store:
            return None
        store[key] = value
        return True

    async def _get(key):
        return store.get(key)

    async def _delete(*keys):
        n = 0
        for k in keys:
            n += store.pop(k, None) is not None
        return n

    r.set = _set
    r.get = _get
    r.delete = _delete
    r.aclose = AsyncMock()
    return r, store


class TestAlertCooldown:
    @pytest.mark.asyncio
    async def test_first_alert_posts_to_slack(self):
        r, _ = _fake_redis()
        with patch.object(session_healer, "_get_redis", AsyncMock(return_value=r)), \
             patch(
                "app.services.slack_notifications.post_alert_to_slack",
                new_callable=AsyncMock,
             ) as slack:
            await session_healer._alert("threads", "needs login")
        slack.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_second_alert_suppressed_by_cooldown(self):
        r, store = _fake_redis()
        store["session_healer:alerted:threads"] = "1"
        with patch.object(session_healer, "_get_redis", AsyncMock(return_value=r)), \
             patch(
                "app.services.slack_notifications.post_alert_to_slack",
                new_callable=AsyncMock,
             ) as slack:
            await session_healer._alert("threads", "needs login")
        slack.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_recovery_notice_only_after_alert(self):
        # No prior alert → no "recovered" noise
        r, _ = _fake_redis()
        with patch.object(session_healer, "_get_redis", AsyncMock(return_value=r)), \
             patch(
                "app.services.slack_notifications.post_alert_to_slack",
                new_callable=AsyncMock,
             ) as slack:
            await session_healer._alert_recovered("threads", "back")
        slack.assert_not_awaited()

        # Prior alert → recovery notice fires and clears the cooldown
        r, store = _fake_redis()
        store["session_healer:alerted:threads"] = "1"
        with patch.object(session_healer, "_get_redis", AsyncMock(return_value=r)), \
             patch(
                "app.services.slack_notifications.post_alert_to_slack",
                new_callable=AsyncMock,
             ) as slack:
            await session_healer._alert_recovered("threads", "back")
        slack.assert_awaited_once()
        assert "session_healer:alerted:threads" not in store


class TestHealLock:
    @pytest.mark.asyncio
    async def test_overlapping_run_skipped(self):
        r, store = _fake_redis()
        store["session_healer:lock"] = "1"
        with patch.object(session_healer, "_get_redis", AsyncMock(return_value=r)):
            result = await session_healer.heal_all_sessions()
        assert result["skipped"] == "another heal run in progress"


class TestBridgePlatformFlow:
    @pytest.mark.asyncio
    async def test_contended_start_skips_recovery(self):
        """A 409 busy-hold must mark the platform contended — never tear
        down another platform's live session to run recovery."""
        stats = {}
        calls = []

        async def fake_call(path, platform, body=None, timeout=60.0):
            calls.append(path)
            if path == "/session/status":
                return 200, {"platform": "facebook", "status": "done",
                             "cookies_found": ["c_user"]}
            if path == "/session/start":
                return 409, {}
            return 200, {}

        with patch.object(session_healer, "_bridge_call", fake_call), \
             patch.object(session_healer, "_alert", AsyncMock()) as alert, \
             patch.object(
                session_healer, "_recover_bridge_platform", AsyncMock()
             ) as recover:
            await session_healer._heal_bridge_platform("twitter", stats)

        assert stats["twitter"]["status"] == "contended"
        recover.assert_not_awaited()
        alert.assert_not_awaited()
        assert "/session/extract" not in calls

    @pytest.mark.asyncio
    async def test_healthy_session_short_circuits(self):
        stats = {}
        calls = []

        async def fake_call(path, platform, body=None, timeout=60.0):
            calls.append(path)
            return 200, {
                "platform": "threads", "status": "done",
                "cookies_found": ["sessionid"],
            }

        with patch.object(session_healer, "_bridge_call", fake_call):
            await session_healer._heal_bridge_platform("threads", stats)

        assert stats["threads"]["status"] == "healthy"
        assert calls == ["/session/status"]

    @pytest.mark.asyncio
    async def test_unrecoverable_alerts_with_cooldown(self):
        stats = {}

        async def fake_call(path, platform, body=None, timeout=60.0):
            if path == "/session/status":
                return 200, {"platform": "twitter", "status": "waiting",
                             "cookies_found": []}
            if path == "/session/start":
                return 200, {"status": "waiting"}
            return 200, {}

        with patch.object(session_healer, "_bridge_call", fake_call), \
             patch.object(
                session_healer, "_bridge_poll_done", AsyncMock(return_value=False)
             ), \
             patch.object(
                session_healer, "_recover_bridge_platform", AsyncMock(return_value=False)
             ), \
             patch.object(
                session_healer, "_bridge_verify_not_login", AsyncMock(return_value=False)
             ), \
             patch.object(session_healer, "_alert", AsyncMock()) as alert:
            await session_healer._heal_bridge_platform("twitter", stats)

        assert stats["twitter"]["status"] == "needs_manual_login"
        alert.assert_awaited_once()
        assert "twitter" in alert.await_args.args[0].lower() or \
            "Twitter" in alert.await_args.args[1]
