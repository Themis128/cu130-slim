"""Unit tests for app.api.analytics helpers — engagement math, feed-card
chrome stripping, snapshot text/URL extraction, org URN, per-platform
follower-count fetchers, and the follower-series builder.

Helpers are called directly; httpx, LinkedInAPIClient, and the Facebook
sidecar client are monkeypatched. No network, no database.
"""
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.api import analytics
from app.core.security import encrypt_token
from app.models.social_account import SocialAccount

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

def _account(platform, **kw) -> SocialAccount:
    return SocialAccount(
        id=kw.get("id", uuid.uuid4()),
        team_id=kw.get("team_id", uuid.uuid4()),
        platform=platform,
        account_id=kw.get("account_id", "12345"),
        username=kw.get("username", "acct"),
        status="active", account_type=kw.get("account_type", "person"),
        access_token_enc=kw.get("access_token_enc", encrypt_token("tok")),
        scopes=[], meta_data=kw.get("meta_data", {}),
    )


class _Resp:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self._data = data or {}

    def json(self):
        return self._data


class _HTTP:
    """Configurable fake httpx.AsyncClient — queue of responses by index."""
    response = _Resp()
    error = None
    requests = []

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kw):
        type(self).requests.append(url)
        if type(self).error:
            raise type(self).error
        return type(self).response


@pytest.fixture
def http(monkeypatch):
    import httpx
    _HTTP.response = _Resp()
    _HTTP.error = None
    _HTTP.requests = []
    monkeypatch.setattr(httpx, "AsyncClient", _HTTP)
    return _HTTP


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

class TestEngagementMath:
    def test_sum_only_engagement_types(self):
        counts = {"like": 5, "comment": 2, "share": 1, "click": 3,
                  "impression": 999, "view": 50}
        assert analytics._engagement_sum(counts) == 11

    def test_sum_empty(self):
        assert analytics._engagement_sum({}) == 0

    def test_rate_impressions_denominator(self):
        assert analytics._engagement_rate(10, 100) == 0.1

    def test_rate_reach_fallback(self):
        # impressions==0 → reach used (X free tier has no impressions)
        assert analytics._engagement_rate(9, 0, reach=30) == pytest.approx(0.3)

    def test_rate_zero_denominator(self):
        assert analytics._engagement_rate(5, 0, 0) == 0.0
        assert analytics._engagement_rate(0, 0) == 0.0


class TestFeedCardChrome:
    def test_strips_chrome_before_time_line(self):
        blob = (
            "Feed post number 3\n"
            "Themis reposted this\n"
            "Cloudless\n"
            "   • 3rd+\n"
            "Founder\n"
            "6mo • Edited •\n"
            "Follow\n"
            "We built the thing. It works."
        )
        out = analytics._strip_feed_card_chrome(blob)
        assert out == "We built the thing. It works."

    def test_follow_line_optional(self):
        blob = "5mo •\nActual body text"
        assert analytics._strip_feed_card_chrome(blob) == "Actual body text"

    def test_no_time_line_drops_chrome_lines(self):
        blob = "Feed post number 2\nfollow\n1,234 followers\nBody stays"
        out = analytics._strip_feed_card_chrome(blob)
        assert "Body stays" in out and "followers" not in out

    def test_empty_body_returns_empty(self):
        assert analytics._strip_feed_card_chrome("\n\n") == ""

    def test_plain_text_without_chrome_survives(self):
        assert analytics._strip_feed_card_chrome("Just a post body") == "Just a post body"


class TestSnapshotContentText:
    def test_discovery_commentary(self):
        raw = {"discovery": {"commentary": "  post text "}}
        assert analytics._snapshot_content_text(raw) == "post text"

    def test_nested_post_commentary(self):
        raw = {"discovery": {"post": {"commentary": "nested"}}}
        assert analytics._snapshot_content_text(raw) == "nested"

    def test_caption_and_text_fallbacks(self):
        assert analytics._snapshot_content_text(
            {"discovery": {"caption": "ig cap"}}) == "ig cap"
        assert analytics._snapshot_content_text({"text": "tweet"}) == "tweet"

    def test_scrape_text_chrome_stripped(self):
        raw = {"scrape": {"text": "2mo •\nReal post"}}
        assert analytics._snapshot_content_text(raw) == "Real post"

    def test_empty(self):
        assert analytics._snapshot_content_text(None) == ""
        assert analytics._snapshot_content_text({}) == ""


class TestSnapshotExternalUrl:
    def test_share_url_wins(self):
        raw = {"share_url": "https://x.com/p/1",
               "discovery": {"permalink": "https://li.com/p"}}
        assert analytics._snapshot_external_url("twitter", raw, "1") == "https://x.com/p/1"

    def test_permalink_fallback(self):
        raw = {"discovery": {"permalink": "https://linkedin.com/post"}}
        assert analytics._snapshot_external_url("linkedin", raw, None) == "https://linkedin.com/post"

    def test_linkedin_urn_to_feed_url(self):
        raw = {"discovery": {"post": {"id": "urn:li:share:99"}}}
        out = analytics._snapshot_external_url("linkedin", raw, None)
        assert out == "https://www.linkedin.com/feed/update/urn:li:share:99"

    def test_twitter_status_url(self):
        out = analytics._snapshot_external_url("twitter", {"id": "777"}, None)
        assert out == "https://x.com/i/status/777"
        out = analytics._snapshot_external_url("x", {}, "888")
        assert out == "https://x.com/i/status/888"

    def test_facebook_page_post(self):
        out = analytics._snapshot_external_url("facebook", {}, "111_222")
        assert out == "https://www.facebook.com/111/posts/222"

    def test_none(self):
        assert analytics._snapshot_external_url("tiktok", {}, "x") is None


class TestOrgUrn:
    def test_author_urn_preferred(self):
        a = _account("linkedin", meta_data={"author_urn": "urn:li:organization:42"})
        assert analytics._org_urn(a) == "urn:li:organization:42"

    def test_non_org_author_urn_ignored(self):
        a = _account("linkedin", account_id="777",
                     meta_data={"author_urn": "urn:li:person:abc"})
        assert analytics._org_urn(a) == "urn:li:organization:777"


# ---------------------------------------------------------------------------
# Follower-count fetchers
# ---------------------------------------------------------------------------

class TestLinkedInFollowers:
    pytestmark = pytest.mark.asyncio

    async def test_member_urn_returns_minus1(self):
        # member accounts have no org follower-statistics endpoint
        a = _account("linkedin", account_id="person-abc",
                     meta_data={"author_urn": "urn:li:person:abc"})
        # _org_urn produces organization:person-abc — non-digit suffix → -1
        assert await analytics._linkedin_follower_count(a) == -1

    async def test_wrong_platform_zero(self):
        assert await analytics._linkedin_follower_count(_account("twitter")) == 0

    async def test_org_count(self, monkeypatch):
        class C:
            def __init__(self, **kw):
                pass

            async def get_follower_count(self, urn):
                assert urn == "urn:li:organization:42"
                return 1234

        monkeypatch.setattr(analytics, "LinkedInAPIClient", C)
        a = _account("linkedin", account_id="42")
        assert await analytics._linkedin_follower_count(a) == 1234

    async def test_client_error_minus1(self, monkeypatch):
        class C:
            def __init__(self, **kw):
                pass

            async def get_follower_count(self, urn):
                raise RuntimeError("api")

        monkeypatch.setattr(analytics, "LinkedInAPIClient", C)
        a = _account("linkedin", account_id="42")
        assert await analytics._linkedin_follower_count(a) == -1


class TestTwitterFollowers:
    pytestmark = pytest.mark.asyncio

    async def test_ok(self, http):
        _HTTP.response = _Resp(200, {"data": {"public_metrics": {"followers_count": 555}}})
        a = _account("twitter", account_id="uid1")
        assert await analytics._twitter_follower_count(a) == 555
        assert "api.x.com/2/users/uid1" in _HTTP.requests[0]

    async def test_non200_minus1(self, http):
        _HTTP.response = _Resp(402, {})
        assert await analytics._twitter_follower_count(_account("twitter")) == -1

    async def test_error_minus1(self, http):
        _HTTP.error = ConnectionError("down")
        assert await analytics._twitter_follower_count(_account("twitter")) == -1

    async def test_wrong_platform_zero(self):
        assert await analytics._twitter_follower_count(_account("threads")) == 0


class TestFacebookFollowers:
    pytestmark = pytest.mark.asyncio

    async def test_user_type_uses_sidecar(self, monkeypatch):
        class SC:
            def __init__(self, **kw):
                pass

            async def get_profile_stats(self, expected_name=None):
                return {"followers": 66}

        monkeypatch.setattr("app.services.facebook_sidecar.FacebookSidecarClient", SC)
        a = _account("facebook", meta_data={"account_type": "user"})
        assert await analytics._facebook_follower_count(a) == 66

    async def test_user_type_sidecar_fail_minus1(self, monkeypatch):
        class SC:
            def __init__(self, **kw):
                pass

            async def get_profile_stats(self, expected_name=None):
                raise RuntimeError("sidecar down")

        monkeypatch.setattr("app.services.facebook_sidecar.FacebookSidecarClient", SC)
        a = _account("facebook", meta_data={"account_type": "user"})
        assert await analytics._facebook_follower_count(a) == -1

    async def test_page_followers_count(self, http):
        _HTTP.response = _Resp(200, {"followers_count": 321})
        a = _account("facebook", account_id="pg9", meta_data={})
        assert await analytics._facebook_follower_count(a) == 321

    async def test_page_fan_count_fallback(self, http):
        _HTTP.response = _Resp(200, {"fan_count": 88})
        a = _account("facebook", meta_data={})
        assert await analytics._facebook_follower_count(a) == 88

    async def test_page_error_minus1(self, http):
        _HTTP.response = _Resp(500, {})
        assert await analytics._facebook_follower_count(_account("facebook")) == -1


class TestInstagramFollowers:
    pytestmark = pytest.mark.asyncio

    async def test_fb_login_token_uses_graph_facebook(self, http, monkeypatch):
        _HTTP.response = _Resp(200, {"followers_count": 77})
        a = _account("instagram", account_id="ig1")
        assert await analytics._instagram_follower_count(a) == 77

    async def test_ig_login_token_uses_graph_instagram(self, http, monkeypatch):
        ig_token = encrypt_token("IGAAUxyz")
        _HTTP.response = _Resp(200, {"followers_count": 12})
        a = _account("instagram", account_id="ig1", access_token_enc=ig_token)
        assert await analytics._instagram_follower_count(a) == 12
        assert "graph.instagram.com" in _HTTP.requests[0]

    async def test_error_minus1(self, http):
        _HTTP.error = RuntimeError("x")
        assert await analytics._instagram_follower_count(_account("instagram")) == -1


class TestThreadsFollowers:
    pytestmark = pytest.mark.asyncio

    async def test_total_value(self, http):
        _HTTP.response = _Resp(200, {"data": [
            {"name": "views", "total_value": {"value": 999}},
            {"name": "followers_count", "total_value": {"value": 42}},
        ]})
        assert await analytics._threads_follower_count(_account("threads")) == 42

    async def test_values_fallback(self, http):
        _HTTP.response = _Resp(200, {"data": [
            {"name": "followers_count", "values": [{"value": 17}]},
        ]})
        assert await analytics._threads_follower_count(_account("threads")) == 17

    async def test_missing_metric_minus1(self, http):
        _HTTP.response = _Resp(200, {"data": [{"name": "other"}]})
        assert await analytics._threads_follower_count(_account("threads")) == -1

    async def test_wrong_platform(self):
        assert await analytics._threads_follower_count(_account("twitter")) == 0


class TestTikTokFollowers:
    pytestmark = pytest.mark.asyncio

    async def test_api_ok(self, http):
        _HTTP.response = _Resp(200, {"data": {"user": {"follower_count": 88}}})
        assert await analytics._tiktok_follower_count(_account("tiktok")) == 88

    async def test_sidecar_fallback(self, monkeypatch):
        calls = []

        class SeqHTTP:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url, **kw):
                calls.append(url)
                if "user/info" in url:
                    return _Resp(403, {})
                return _Resp(200, {"stats": {"followers": 55}})

        import httpx
        monkeypatch.setattr(httpx, "AsyncClient", SeqHTTP)
        a = _account("tiktok", username="@cloudless.gr")
        assert await analytics._tiktok_follower_count(a) == 55
        assert any("profile/videos" in u for u in calls)

    async def test_both_fail_minus1(self, http):
        _HTTP.response = _Resp(500, {})
        a = _account("tiktok", username="@x")
        assert await analytics._tiktok_follower_count(a) == -1

    async def test_no_username_minus1(self, http):
        _HTTP.response = _Resp(500, {})
        a = _account("tiktok", username=None)
        assert await analytics._tiktok_follower_count(a) == -1


class TestFollowerDispatch:
    pytestmark = pytest.mark.asyncio

    async def test_unknown_platform_zero(self):
        assert await analytics._follower_count(_account("viber")) == 0

    async def test_dispatches(self, monkeypatch):
        async def fn(account):
            return 4321

        monkeypatch.setattr(analytics, "_tiktok_follower_count", fn)
        assert await analytics._follower_count(_account("tiktok")) == 4321


# ---------------------------------------------------------------------------
# Follower series builder
# ---------------------------------------------------------------------------

class TestFollowerSeries:
    def _ts(self, days_ago):
        return datetime.now(UTC) - timedelta(days=days_ago)

    def test_no_snapshots_none(self):
        since = self._ts(14)
        out = analytics._build_follower_series([], None, since, datetime.now(UTC))
        assert out is None

    def test_baseline_and_snaps(self):
        since = self._ts(14)
        now = datetime.now(UTC)
        snaps = [(self._ts(7), 100), (self._ts(1), 110)]
        current, change, series = analytics._build_follower_series(snaps, 90, since, now)
        assert current == 110 and change == 20  # vs baseline 90
        assert series[0].followers == 90  # baseline seeded at `since`

    def test_carries_forward_to_today(self):
        since = self._ts(14)
        now = datetime.now(UTC)
        snaps = [(self._ts(10), 50)]
        current, change, series = analytics._build_follower_series(snaps, None, since, now)
        assert current == 50 and change == 0
        # last point is today's carried-forward count
        assert series[-1].date == now.strftime("%Y-%m-%d") and series[-1].followers == 50

    def test_naive_ts_treated_utc(self):
        since = datetime.now(UTC) - timedelta(days=14)
        now = datetime.now(UTC)
        naive = datetime.now()  # naive
        current, change, series = analytics._build_follower_series(
            [(naive, 7)], None, since, now,
        )
        assert series[0].date is not None


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------

class TestTeamForUser:
    pytestmark = pytest.mark.asyncio

    async def test_delegates_to_db_get(self):
        class DB:
            async def get(self, model, key):
                return ("team", model, key)

        out = await analytics._team_for_user(DB(), uuid.uuid4())
        assert out[0] == "team"
