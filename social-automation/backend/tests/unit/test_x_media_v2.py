"""X (Twitter) API v2 media upload, alt text, OAuth scopes and 402 handling.

Pinned to the current docs.x.com contract:
- simple upload ``POST /2/media/upload`` (multipart media + media_category)
- chunked ``POST /2/media/upload/initialize`` → ``/{id}/append`` →
  ``/{id}/finalize`` → ``GET /2/media/upload?command=STATUS``
- alt text ``POST /2/media/metadata`` (alt_text.text ≤ 1000 chars)
- OAuth 2.0 PKCE scopes incl. ``media.write`` + ``offline.access``
"""

import urllib.parse
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services import publishing as pub
from app.services import twitter_api as api


class _Resp:
    def __init__(self, status_code, body, headers=None):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}
        self.text = str(body)

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

    def _next(self):
        return self.responses.pop(0) if self.responses else _Resp(500, "out of responses")

    async def post(self, url, headers=None, json=None, files=None, data=None, **kw):
        self.calls.append({"method": "POST", "url": url, "headers": headers, "json": json, "files": files, "data": data})
        return self._next()

    async def get(self, url, headers=None, params=None, **kw):
        self.calls.append({"method": "GET", "url": url, "headers": headers, "params": params})
        return self._next()


def _patched(fake):
    return patch("app.services.twitter_api.httpx.AsyncClient", new=lambda timeout=30.0: fake)


# ── TwitterAPIClient: v2 endpoints ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_simple_upload_uses_v2_endpoint_and_data_id():
    fake = _Client([_Resp(200, {"data": {"id": "1880028106020515840", "media_key": "3_188"}})])
    with _patched(fake):
        mid = await api.TwitterAPIClient("tok").upload_media(b"img", mime_type="image/png", filename="a.png")
    assert mid == "1880028106020515840"
    call = fake.calls[0]
    assert call["url"] == "https://api.x.com/2/media/upload"
    assert "upload.twitter.com" not in call["url"]
    assert call["headers"]["Authorization"] == "Bearer tok"
    assert call["files"]["media"] == ("a.png", b"img", "image/png")
    assert call["data"] == {"media_category": "tweet_image"}


@pytest.mark.asyncio
async def test_chunked_upload_initialize_append_finalize_status(monkeypatch):
    sleeps = []

    async def _sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(api.asyncio, "sleep", _sleep)
    mid = "1880028106020515840"
    fake = _Client([
        _Resp(200, {"data": {"id": mid, "expires_after_secs": 86400}}),
        _Resp(204, ""),
        _Resp(204, ""),
        _Resp(204, ""),
        _Resp(200, {"data": {"id": mid, "processing_info": {"state": "pending", "check_after_secs": 2}}}),
        _Resp(200, {"data": {"id": mid, "processing_info": {"state": "in_progress", "check_after_secs": 1}}}),
        _Resp(200, {"data": {"id": mid, "processing_info": {"state": "succeeded"}}}),
    ])
    data = b"x" * 10
    with _patched(fake):
        out = await api.TwitterAPIClient("tok").upload_media_chunked(data, "video/mp4", "tweet_video", chunk_size=4)
    assert out == mid
    urls = [c["url"] for c in fake.calls]
    assert urls[0] == "https://api.x.com/2/media/upload/initialize"
    assert fake.calls[0]["json"] == {"media_type": "video/mp4", "total_bytes": 10, "media_category": "tweet_video"}
    assert urls[1:4] == [f"https://api.x.com/2/media/upload/{mid}/append"] * 3
    assert [c["data"]["segment_index"] for c in fake.calls[1:4]] == ["0", "1", "2"]
    assert b"".join(c["files"]["media"][1] for c in fake.calls[1:4]) == data
    assert urls[4] == f"https://api.x.com/2/media/upload/{mid}/finalize"
    assert fake.calls[5]["method"] == "GET" and urls[5] == "https://api.x.com/2/media/upload"
    assert fake.calls[5]["params"] == {"command": "STATUS", "media_id": mid}
    # never the legacy command-style INIT/APPEND/FINALIZE protocol
    assert all("command" not in (c.get("data") or {}) for c in fake.calls if c["method"] == "POST")
    assert sleeps == [2.0, 1.0]


@pytest.mark.asyncio
async def test_chunked_upload_processing_failure_raises(monkeypatch):
    monkeypatch.setattr(api.asyncio, "sleep", AsyncMock())
    mid = "123"
    fake = _Client([
        _Resp(200, {"data": {"id": mid}}),
        _Resp(204, ""),
        _Resp(200, {"data": {"id": mid, "processing_info": {"state": "failed", "error": {"message": "InvalidMedia"}}}}),
    ])
    with _patched(fake), pytest.raises(api.TwitterAPIError) as exc:
        await api.TwitterAPIClient("tok").upload_media_chunked(b"gif", "image/gif", "tweet_gif")
    assert "InvalidMedia" in str(exc.value)


@pytest.mark.asyncio
async def test_alt_text_metadata_payload_truncated_to_1000():
    fake = _Client([_Resp(200, {"data": {"id": "77"}})])
    with _patched(fake):
        await api.TwitterAPIClient("tok").set_media_alt_text("77", "a" * 1500)
    call = fake.calls[0]
    assert call["url"] == "https://api.x.com/2/media/metadata"
    assert call["json"]["id"] == "77"
    assert len(call["json"]["metadata"]["alt_text"]["text"]) == 1000


@pytest.mark.asyncio
async def test_media_signer_replaces_bearer_on_media_endpoints():
    seen = []

    def signer(method, url, params=None):
        seen.append((method, url, params))
        return 'OAuth oauth_signature="sig"'

    fake = _Client([_Resp(200, {"data": {"id": "5"}})])
    with _patched(fake):
        await api.TwitterAPIClient("tok", media_signer=signer).upload_media(b"i")
    assert fake.calls[0]["headers"]["Authorization"].startswith("OAuth ")
    assert seen == [("POST", "https://api.x.com/2/media/upload", None)]


@pytest.mark.asyncio
async def test_402_credits_depleted_message_is_accurate():
    fake = _Client([_Resp(402, {"title": "CreditsDepleted", "detail": "Your account has no credits"})])
    with _patched(fake), pytest.raises(api.TwitterAPIError) as exc:
        await api.TwitterAPIClient("tok").create_tweet("hi")
    msg = str(exc.value)
    assert exc.value.status_code == 402
    assert "POST /2/tweets" in msg and "still works" not in msg


# ── publishing: media routing + auth selection ───────────────────────────────


class _MediaClient:
    def __init__(self, *a, media_signer=None, **k):
        self.media_signer = media_signer
        self.simple = AsyncMock(return_value="11")
        self.chunked = AsyncMock(return_value="22")
        self.alt = AsyncMock()
        _MediaClient.last = self

    async def upload_media(self, *a, **k):
        return await self.simple(*a, **k)

    async def upload_media_chunked(self, *a, **k):
        return await self.chunked(*a, **k)

    async def set_media_alt_text(self, *a, **k):
        return await self.alt(*a, **k)


def _file(tmp_path, name, data=b"data"):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "kind", "media_type", "category"),
    [
        ("a.jpg", "simple", "image/jpeg", "tweet_image"),
        ("a.gif", "chunked", "image/gif", "tweet_gif"),
        ("v.mp4", "chunked", "video/mp4", "tweet_video"),
        ("v.mov", "chunked", "video/quicktime", "tweet_video"),
    ],
)
async def test_upload_routes_by_media_type(tmp_path, monkeypatch, name, kind, media_type, category):
    monkeypatch.setattr(pub, "TwitterAPIClient", _MediaClient)
    account = SimpleNamespace(scopes=["tweet.write", "media.write"])
    mid = await pub._twitter_upload_media(_file(tmp_path, name), access_token="t", account=account, alt_text="desc")
    c = _MediaClient.last
    if kind == "simple":
        assert mid == "11"
        assert c.simple.await_args.kwargs["media_category"] == category
        assert c.simple.await_args.kwargs["mime_type"] == media_type
        c.alt.assert_awaited_once_with("11", "desc")
    else:
        assert mid == "22"
        assert c.chunked.await_args.args[1:] == (media_type, category)
    assert c.media_signer is None  # media.write granted → OAuth 2.0 bearer


@pytest.mark.asyncio
async def test_upload_uses_oauth1_when_grant_lacks_media_write(tmp_path, monkeypatch):
    monkeypatch.setattr(pub, "TwitterAPIClient", _MediaClient)
    signer = lambda m, u, p=None: "OAuth x"  # noqa: E731
    monkeypatch.setattr(pub, "_x_oauth1_media_signer", lambda: signer)
    account = SimpleNamespace(scopes=["tweet.read", "tweet.write", "users.read", "offline.access"])
    await pub._twitter_upload_media(_file(tmp_path, "a.png"), access_token="t", account=account)
    assert _MediaClient.last.media_signer is signer


@pytest.mark.asyncio
async def test_upload_without_media_write_or_oauth1_fails_clearly(tmp_path, monkeypatch):
    monkeypatch.setattr(pub, "_x_oauth1_media_signer", lambda: None)
    account = SimpleNamespace(scopes=["tweet.write"])
    with pytest.raises(pub.TwitterAPIError) as exc:
        await pub._twitter_upload_media(_file(tmp_path, "a.png"), access_token="t", account=account)
    assert "media.write" in str(exc.value)


@pytest.mark.asyncio
async def test_alt_text_failure_does_not_fail_upload(tmp_path, monkeypatch):
    monkeypatch.setattr(pub, "TwitterAPIClient", _MediaClient)
    orig_init = _MediaClient.__init__

    def init(self, *a, **k):
        orig_init(self, *a, **k)
        self.alt = AsyncMock(side_effect=pub.TwitterAPIError(402, "credits", "metadata"))

    monkeypatch.setattr(_MediaClient, "__init__", init)
    mid = await pub._twitter_upload_media(
        _file(tmp_path, "a.jpg"), access_token="t", account=SimpleNamespace(scopes=["media.write"]), alt_text="x",
    )
    assert mid == "11"


@pytest.mark.asyncio
async def test_media_upload_401_refreshes_token_once(tmp_path, monkeypatch):
    from app.services import x_web

    jpeg = (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    )
    img = _file(tmp_path, "a.jpg", jpeg)
    monkeypatch.setattr(x_web, "plan_media", lambda paths: x_web.MediaPlan(kind="images", paths=paths))
    monkeypatch.setattr(x_web, "prepare_media", lambda plan, d: list(plan.paths))
    tokens = []

    async def upload(path, *, access_token, account=None, alt_text=None):
        tokens.append(access_token)
        if access_token == "old":
            raise pub.TwitterAPIError(401, "Unauthorized", "https://api.x.com/2/media/upload")
        return "99"

    monkeypatch.setattr(pub, "_twitter_upload_media", upload)
    monkeypatch.setattr(pub, "_refresh_oauth2_token", AsyncMock(return_value="new"))
    fake = _Client([_Resp(201, {"data": {"id": "500"}})])
    account = SimpleNamespace(account_id="42", username="TBaltzakis", scopes=["media.write"])
    with _patched(fake):
        res = await pub._publish_twitter("old", "hi", account, SimpleNamespace(), [img], db=object())
    assert res.success and res.platform_post_id == "500"
    assert tokens == ["old", "new"]
    assert fake.calls[0]["headers"]["Authorization"] == "Bearer new"
    assert fake.calls[0]["json"]["media"] == {"media_ids": ["99"]}


@pytest.mark.asyncio
async def test_video_goes_through_official_api_when_available(tmp_path, monkeypatch):
    from app.services import x_web

    vid = _file(tmp_path, "v.mp4")
    monkeypatch.setattr(x_web, "plan_media", lambda paths: x_web.MediaPlan(kind="video", paths=paths))
    monkeypatch.setattr(x_web, "prepare_media", lambda plan, d: list(plan.paths))
    up = AsyncMock(return_value="321")
    monkeypatch.setattr(pub, "_twitter_upload_media", up)
    web = AsyncMock()
    monkeypatch.setattr(x_web, "publish_via_x_web", web)
    fake = _Client([_Resp(201, {"data": {"id": "600"}})])
    account = SimpleNamespace(account_id="42", username="TBaltzakis", scopes=["media.write"])
    with _patched(fake):
        res = await pub._publish_twitter("tok", "clip", account, SimpleNamespace(), [vid])
    assert res.success and res.platform_post_id == "600"
    assert fake.calls[0]["json"]["media"] == {"media_ids": ["321"]}
    web.assert_not_awaited()


# ── OAuth 2.0 scopes / authorize URL ─────────────────────────────────────────


def test_twitter_scopes_canonical_and_include_media_write():
    import app.api.auth as auth

    for s in ("tweet.read", "tweet.write", "users.read", "media.write", "offline.access"):
        assert s in auth.TWITTER_SCOPES
    assert auth.PLATFORM_SCOPES["twitter"] is auth.TWITTER_SCOPES
    assert set(auth.twitter_client.base_scopes) == set(auth.TWITTER_SCOPES)


def test_granted_scopes_parses_token_scope_field():
    import app.api.auth as auth

    tok = {"scope": "tweet.write users.read tweet.read offline.access"}
    assert auth._granted_scopes(tok, ["x"]) == ["offline.access", "tweet.read", "tweet.write", "users.read"]
    assert auth._granted_scopes({}, ["a", "b"]) == ["a", "b"]


@pytest.mark.asyncio
async def test_twitter_authorize_url_pkce_s256_and_scopes(monkeypatch):
    import app.api.auth as auth

    monkeypatch.setattr(auth.twitter_client, "client_id", "cid")
    out = await auth.oauth_authorize("twitter", team_id=uuid.uuid4(), current_user=None)
    url = urllib.parse.urlparse(out["authorization_url"])
    q = urllib.parse.parse_qs(url.query)
    assert f"{url.scheme}://{url.netloc}{url.path}" == "https://x.com/i/oauth2/authorize"
    assert q["response_type"] == ["code"]
    assert q["client_id"] == ["cid"]
    assert q["code_challenge_method"] == ["S256"]
    assert 43 <= len(q["code_challenge"][0]) <= 128
    assert q["redirect_uri"] == [auth.settings.TWITTER_REDIRECT_URI]
    assert set(q["scope"][0].split(" ")) == set(auth.TWITTER_SCOPES)
    assert len(q["state"][0]) <= 500  # X caps state at 500 chars
