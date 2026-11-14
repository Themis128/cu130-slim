import pytest


class TestBlueskySync:
    @pytest.mark.asyncio
    async def test_follower_count_and_sync(self, db_session=None):
        import uuid
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        from app.api import analytics
        from app.services import analytics_sync

        acc = SimpleNamespace(
            id=uuid.uuid4(),
            team_id=uuid.uuid4(),
            platform="bluesky",
            username="u.bsky.social",
            account_id="did:x",
            access_token_enc="t",
            meta_data={},
            status="active",
        )
        profile = {"followersCount": 42}
        client = SimpleNamespace(create_session=AsyncMock(return_value={}), get_profile=AsyncMock(return_value=profile))
        with (
            patch("app.services.bluesky_api.BlueskyClient", return_value=client),
            patch("app.services.analytics_sync.decrypt_token", return_value="pw"),
            patch("app.api.analytics.decrypt_token", return_value="pw"),
        ):
            assert await analytics._bluesky_follower_count(acc) == 42
            r = await analytics_sync.sync_bluesky_account(None, acc)
            assert r.synced == 1 and not r.errors

    @pytest.mark.asyncio
    async def test_failure_paths(self):
        import uuid
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        from app.api import analytics
        from app.services import analytics_sync

        acc = SimpleNamespace(
            id=uuid.uuid4(), team_id=uuid.uuid4(), platform="bluesky", username="u", account_id="d", access_token_enc="t", meta_data={}, status="active"
        )
        client = SimpleNamespace(create_session=AsyncMock(side_effect=RuntimeError("401")), get_profile=AsyncMock())
        with (
            patch("app.services.bluesky_api.BlueskyClient", return_value=client),
            patch("app.services.analytics_sync.decrypt_token", return_value="pw"),
            patch("app.api.analytics.decrypt_token", return_value="pw"),
        ):
            assert await analytics._bluesky_follower_count(acc) == -1
            r = await analytics_sync.sync_bluesky_account(None, acc)
            assert r.skipped == 1 and r.errors
        wrong = SimpleNamespace(platform="other", username="x")
        assert await analytics._bluesky_follower_count(wrong) == 0

    @pytest.mark.asyncio
    async def test_skip_during_429_backoff(self):
        import uuid
        from types import SimpleNamespace
        from unittest.mock import patch

        from app.services import analytics_sync
        from app.services.bluesky_api import BACKOFF_KEY

        acc = SimpleNamespace(
            id=uuid.uuid4(),
            team_id=uuid.uuid4(),
            platform="bluesky",
            username="u",
            account_id="d",
            access_token_enc="t",
            meta_data={BACKOFF_KEY: "2099-01-01T00:00:00Z"},
            status="active",
        )
        with patch("app.services.analytics_sync.decrypt_token", return_value="pw"):
            r = await analytics_sync.sync_bluesky_account(None, acc)
        assert r.skipped == 1
        assert any("backoff" in e for e in r.errors)

    @pytest.mark.asyncio
    async def test_429_sets_backoff(self):
        import uuid
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        from app.services import analytics_sync
        from app.services.bluesky_api import BACKOFF_KEY, BlueskyAPIError

        acc = SimpleNamespace(
            id=uuid.uuid4(),
            team_id=uuid.uuid4(),
            platform="bluesky",
            username="u",
            account_id="d",
            access_token_enc="t",
            meta_data={},
            status="active",
        )
        client = SimpleNamespace(
            create_session=AsyncMock(side_effect=BlueskyAPIError(429, "rate limited", "u")),
            get_profile=AsyncMock(),
        )
        with (
            patch("app.services.bluesky_api.BlueskyClient", return_value=client),
            patch("app.services.analytics_sync.decrypt_token", return_value="pw"),
            patch("sqlalchemy.orm.attributes.flag_modified") as fm,
        ):
            r = await analytics_sync.sync_bluesky_account(None, acc)
        assert r.skipped == 1
        assert BACKOFF_KEY in acc.meta_data
        fm.assert_called_once()
