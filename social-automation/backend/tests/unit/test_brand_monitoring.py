"""Coverage for app/services/brand_monitoring.py."""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.brand_monitoring as BM
from app.models.brand_monitoring import BrandMention, CompetitorSnapshot


class _Resp:
    def __init__(self, status=200, body=None, text=""):
        self.status_code = status
        self._body = body if body is not None else {}
        self.text = text

    def json(self):
        return self._body


class _HTTP:
    def __init__(self, resp=None, exc=None):
        self._resp = resp
        self._exc = exc
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def get(self, url, **kw):
        self.calls.append((url, kw))
        if self._exc:
            raise self._exc
        if callable(self._resp):
            return self._resp(url)
        return self._resp or _Resp()


def _wire(monkeypatch, client):
    monkeypatch.setattr(BM.httpx, "AsyncClient", lambda **kw: client)


class TestTwitterMentions:
    @pytest.mark.asyncio
    async def test_no_token(self):
        assert await BM.search_twitter_mentions("x") == []

    @pytest.mark.asyncio
    async def test_non200(self, monkeypatch):
        _wire(monkeypatch, _HTTP(_Resp(429)))
        assert await BM.search_twitter_mentions("x", "tok") == []

    @pytest.mark.asyncio
    async def test_happy(self, monkeypatch):
        body = {"data": [{
            "id": "t1", "author_id": "u1", "text": "love cloudless",
            "created_at": "2026-01-01T00:00:00Z",
            "public_metrics": {"like_count": 3, "retweet_count": 2},
        }]}
        c = _HTTP(_Resp(200, body))
        _wire(monkeypatch, c)
        out = await BM.search_twitter_mentions("cloudless", "tok")
        assert len(out) == 1
        m = out[0]
        assert m["platform"] == "twitter"
        assert m["engagement"] == 5
        assert m["url"].endswith("/t1")
        assert c.calls[0][1]["headers"]["Authorization"] == "Bearer tok"

    @pytest.mark.asyncio
    async def test_exception(self, monkeypatch):
        _wire(monkeypatch, _HTTP(exc=TimeoutError()))
        assert await BM.search_twitter_mentions("x", "tok") == []


class TestRedditMentions:
    @pytest.mark.asyncio
    async def test_happy(self, monkeypatch):
        body = {"data": {"children": [{
            "data": {"author": "u", "title": "T", "selftext": "body",
                     "permalink": "/r/x/1", "score": 9,
                     "created_utc": 1700000000, "subreddit": "s"},
        }, {"data": {"author": "v", "title": "T2"}}]}}
        _wire(monkeypatch, _HTTP(_Resp(200, body)))
        out = await BM.search_reddit_mentions("cloudless")
        assert len(out) == 2
        assert out[0]["platform"] == "reddit"
        assert out[0]["engagement"] == 9
        assert out[0]["mentioned_at"] is not None
        assert out[1]["mentioned_at"] is None
        assert out[1]["extra_data"]["subreddit"] == ""

    @pytest.mark.asyncio
    async def test_non200_and_exception(self, monkeypatch):
        _wire(monkeypatch, _HTTP(_Resp(403)))
        assert await BM.search_reddit_mentions("x") == []
        _wire(monkeypatch, _HTTP(exc=OSError()))
        assert await BM.search_reddit_mentions("x") == []


class TestGoogleNewsMentions:
    RSS = """<?xml version="1.0"?><rss><channel>
    <item><title>Cloudless launches</title><link>http://l/1</link>
    <source>TechSite</source><pubDate>Mon, 01 Jan 2026</pubDate></item>
    <item><title>Other</title><link>http://l/2</link></item>
    </channel></rss>"""

    @pytest.mark.asyncio
    async def test_happy(self, monkeypatch):
        _wire(monkeypatch, _HTTP(_Resp(200, text=self.RSS)))
        out = await BM.search_google_news_mentions("cloudless")
        assert len(out) == 2
        assert out[0]["platform"] == "google_news"
        assert out[0]["author"] == "TechSite"
        assert out[0]["engagement"] == 0

    @pytest.mark.asyncio
    async def test_non200_bad_xml_exception(self, monkeypatch):
        _wire(monkeypatch, _HTTP(_Resp(503)))
        assert await BM.search_google_news_mentions("x") == []
        _wire(monkeypatch, _HTTP(_Resp(200, text="<not-xml")))
        assert await BM.search_google_news_mentions("x") == []
        _wire(monkeypatch, _HTTP(exc=TimeoutError()))
        assert await BM.search_google_news_mentions("x") == []


class TestAnalyzeSentiment:
    @pytest.mark.asyncio
    async def test_llm_json_string(self, monkeypatch):
        import app.services.inference as inf
        monkeypatch.setattr(inf, "call_inference", AsyncMock(
            return_value={"response": '{"sentiment": "positive", "score": 0.8}'}))
        out = await BM.analyze_sentiment("great product")
        assert out == {"sentiment": "positive", "score": 0.8}

    @pytest.mark.asyncio
    async def test_llm_dict_response(self, monkeypatch):
        import app.services.inference as inf
        monkeypatch.setattr(inf, "call_inference", AsyncMock(
            return_value={"response": {"sentiment": "negative", "score": -0.4}}))
        out = await BM.analyze_sentiment("awful")
        assert out["sentiment"] == "negative"

    @pytest.mark.asyncio
    async def test_llm_failure_keyword_fallback(self, monkeypatch):
        import app.services.inference as inf
        monkeypatch.setattr(inf, "call_inference",
                            AsyncMock(side_effect=RuntimeError()))
        assert (await BM.analyze_sentiment("good great awesome"))["sentiment"] == "positive"
        assert (await BM.analyze_sentiment("bad terrible"))["sentiment"] == "negative"
        assert (await BM.analyze_sentiment("the sky"))["sentiment"] == "neutral"
        # tie goes neutral
        assert (await BM.analyze_sentiment("good bad"))["sentiment"] == "neutral"


class _DB:
    def __init__(self, results=None):
        self.added = []
        self.commits = 0
        self._q = list(results or [])

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def execute(self, stmt, *a):
        return self._q.pop(0) if self._q else SimpleNamespace(
            scalar_one_or_none=lambda: None)


def _brand():
    return SimpleNamespace(id=uuid.uuid4(), team_id=uuid.uuid4(),
                           name="cloudless")


class TestCollectMentions:
    @pytest.mark.asyncio
    async def test_collect(self, monkeypatch):
        monkeypatch.setattr(BM, "search_twitter_mentions",
                            AsyncMock(return_value=[{
                                "platform": "twitter", "author": "a",
                                "content": "great stuff", "url": "http://u",
                                "engagement": 4,
                                "mentioned_at": "2026-01-01T00:00:00Z",
                                "extra_data": {}}]))
        monkeypatch.setattr(BM, "search_reddit_mentions",
                            AsyncMock(return_value=[{
                                "platform": "reddit", "author": "b",
                                "content": "bad stuff", "url": "http://v",
                                "engagement": 1,
                                "mentioned_at": "not-a-date"}]))
        monkeypatch.setattr(BM, "search_google_news_mentions",
                            AsyncMock(side_effect=RuntimeError("rss")))
        monkeypatch.setattr(BM, "analyze_sentiment",
                            AsyncMock(side_effect=[
                                {"sentiment": "positive", "score": 0.5},
                                {"sentiment": "negative", "score": -0.5}]))
        db = _DB()
        mentions = await BM.collect_mentions(db, _brand())
        assert len(mentions) == 2
        assert all(isinstance(m, BrandMention) for m in mentions)
        assert mentions[0].sentiment == "positive"
        assert mentions[0].mentioned_at is not None
        assert db.commits == 1
        assert len(db.added) == 2

    @pytest.mark.asyncio
    async def test_empty_sources(self, monkeypatch):
        for fn in ("search_twitter_mentions", "search_reddit_mentions",
                   "search_google_news_mentions"):
            monkeypatch.setattr(BM, fn, AsyncMock(return_value=[]))
        db = _DB()
        assert await BM.collect_mentions(db, _brand()) == []


class TestTwitterAccessToken:
    @pytest.mark.asyncio
    async def test_no_row_and_decrypt(self, monkeypatch):
        db = _DB([SimpleNamespace(scalar_one_or_none=lambda: None)])
        assert await BM._twitter_access_token(db, _brand()) is None

        db = _DB([SimpleNamespace(scalar_one_or_none=lambda: b"enc")])
        monkeypatch.setattr(BM, "decrypt_token", lambda b: "plain")
        assert await BM._twitter_access_token(db, _brand()) == "plain"

        db = _DB([SimpleNamespace(scalar_one_or_none=lambda: "enc-str")])
        assert await BM._twitter_access_token(db, _brand()) == "plain"

        monkeypatch.setattr(BM, "decrypt_token",
                            lambda b: (_ for _ in ()).throw(ValueError()))
        db = _DB([SimpleNamespace(scalar_one_or_none=lambda: b"enc")])
        assert await BM._twitter_access_token(db, _brand()) is None


class TestFetchTwitterCompetitor:
    @pytest.mark.asyncio
    async def test_no_token(self, monkeypatch):
        monkeypatch.setattr(BM, "_twitter_access_token",
                            AsyncMock(return_value=None))
        assert await BM._fetch_twitter_competitor(None, _brand(), "x") is None

    @pytest.mark.asyncio
    async def test_bad_username(self, monkeypatch):
        monkeypatch.setattr(BM, "_twitter_access_token",
                            AsyncMock(return_value="tok"))
        out = await BM._fetch_twitter_competitor(None, _brand(), "bad/handle!")
        assert out is None

    @pytest.mark.asyncio
    async def test_user_lookup_fails(self, monkeypatch):
        monkeypatch.setattr(BM, "_twitter_access_token",
                            AsyncMock(return_value="tok"))
        _wire(monkeypatch, _HTTP(_Resp(404)))
        out = await BM._fetch_twitter_competitor(None, _brand(), "@valid_name")
        assert out is None

    @pytest.mark.asyncio
    async def test_happy_with_tweets(self, monkeypatch):
        monkeypatch.setattr(BM, "_twitter_access_token",
                            AsyncMock(return_value="tok"))

        def route(url):
            if "users/by/username" in url:
                return _Resp(200, {"data": {
                    "id": "42",
                    "public_metrics": {"followers_count": 100,
                                       "tweet_count": 50}}})
            return _Resp(200, {"data": [
                {"text": "top", "public_metrics": {
                    "like_count": 10, "retweet_count": 5,
                    "reply_count": 3, "quote_count": 2}},
                {"text": "low", "public_metrics": {"like_count": 1}},
            ]})

        _wire(monkeypatch, _HTTP(route))
        out = await BM._fetch_twitter_competitor(None, _brand(), "valid_name")
        assert out["follower_count"] == 100
        assert out["post_count"] == 50
        assert out["top_post_content"] == "top"
        assert out["top_post_engagement"] == 20
        assert out["engagement_rate"] > 0

    @pytest.mark.asyncio
    async def test_tweets_fail_and_no_id(self, monkeypatch):
        monkeypatch.setattr(BM, "_twitter_access_token",
                            AsyncMock(return_value="tok"))
        # user has no id → skip tweets call entirely
        _wire(monkeypatch, _HTTP(_Resp(200, {"data": {
            "public_metrics": {"followers_count": 0}}})))
        out = await BM._fetch_twitter_competitor(None, _brand(), "x")
        assert out["engagement_rate"] == 0.0
        assert out["top_post_content"] is None

        # user id but tweets call non-200
        def route(url):
            if "tweets" in url:
                return _Resp(500)
            return _Resp(200, {"data": {"id": "9", "public_metrics": {}}})
        _wire(monkeypatch, _HTTP(route))
        out = await BM._fetch_twitter_competitor(None, _brand(), "x")
        assert out["top_post_engagement"] == 0

    @pytest.mark.asyncio
    async def test_exception(self, monkeypatch):
        monkeypatch.setattr(BM, "_twitter_access_token",
                            AsyncMock(return_value="tok"))
        _wire(monkeypatch, _HTTP(exc=RuntimeError()))
        assert await BM._fetch_twitter_competitor(None, _brand(), "x") is None


class TestSnapshotCompetitor:
    @pytest.mark.asyncio
    async def test_twitter_with_real(self, monkeypatch):
        monkeypatch.setattr(BM, "_fetch_twitter_competitor", AsyncMock(
            return_value={"follower_count": 99, "engagement_rate": 1.5}))
        db = _DB()
        snap = await BM.snapshot_competitor(db, _brand(), "rival")
        assert isinstance(snap, CompetitorSnapshot)
        assert snap.follower_count == 99
        assert snap.competitor_name == "rival"
        assert db.commits == 1

    @pytest.mark.asyncio
    async def test_other_platform_defaults(self, monkeypatch):
        db = _DB()
        snap = await BM.snapshot_competitor(db, _brand(), "rival",
                                            platform="instagram")
        assert snap.follower_count == 0
        assert snap.platform == "instagram"


class TestHealthScore:
    def _mention(self, score=0.0, eng=0):
        return SimpleNamespace(sentiment_score=score, engagement=eng)

    def _snap(self, eng=0):
        return SimpleNamespace(top_post_engagement=eng)

    def test_empty_mentions_defaults(self):
        out = BM.calculate_health_score([], [])
        assert out["sentiment"] == 50.0
        assert out["reach"] == 0
        assert out["share_of_voice"] == 50.0
        assert out["avg_sentiment"] == 0

    def test_full_inputs(self):
        mentions = [self._mention(0.5, 10), self._mention(-0.5, 30)]
        snaps = [self._snap(20)]
        out = BM.calculate_health_score(mentions, snaps,
                                        post_count_30d=30,
                                        avg_engagement_rate=5.0)
        assert out["sentiment"] == 50.0  # avg 0 → 50
        assert out["reach"] == min(2 * 5 + 40 * 0.5, 100)  # 30
        # sov: 40/(40+20)*100
        assert abs(out["share_of_voice"] - 66.7) < 0.1
        assert out["engagement"] == 50.0
        assert out["consistency"] == 100
        assert out["mention_count"] == 2
        assert out["total_engagement"] == 40

    def test_zero_engagement_sov(self):
        out = BM.calculate_health_score([self._mention(1.0, 0)],
                                        [self._snap(0)])
        assert out["share_of_voice"] == 50.0
        assert out["sentiment"] == 100.0
