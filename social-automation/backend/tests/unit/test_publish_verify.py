"""Unit tests for publish_verify — post-publish reel/story verification."""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.services.instagrapi_client import InstagrapiError
from app.worker.tasks import publish_verify as pv


class _Resp:
    def __init__(self, status_code):
        self.status_code = status_code


class _GraphClient:
    """httpx.AsyncClient stand-in recording GETs."""

    def __init__(self, resp):
        self._resp = resp
        self.gets = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None):
        self.gets.append((url, params))
        return self._resp


class TestVerifyReel:
    @pytest.mark.asyncio
    async def test_live_on_200(self, monkeypatch):
        monkeypatch.setattr(pv, "decrypt_token", lambda enc: "tok")
        client = _GraphClient(_Resp(200))
        monkeypatch.setattr(pv.httpx, "AsyncClient", lambda timeout=15: client)
        assert await pv._verify_reel("reel1", "enc", None) == "live"
        assert client.gets[0][1]["access_token"] == "tok"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("code", [400, 404])
    async def test_failed_on_4xx(self, monkeypatch, code):
        monkeypatch.setattr(pv, "decrypt_token", lambda enc: "tok")
        monkeypatch.setattr(pv.httpx, "AsyncClient", lambda timeout=15: _GraphClient(_Resp(code)))
        assert await pv._verify_reel("reel1", "enc", None) == "failed"

    @pytest.mark.asyncio
    async def test_unknown_on_5xx_and_bad_input(self, monkeypatch):
        monkeypatch.setattr(pv, "decrypt_token", lambda enc: "tok")
        monkeypatch.setattr(pv.httpx, "AsyncClient", lambda timeout=15: _GraphClient(_Resp(500)))
        assert await pv._verify_reel("reel1", "enc", None) == "unknown"
        assert await pv._verify_reel("", "enc", None) == "unknown"
        assert await pv._verify_reel("reel1", "", None) == "unknown"

        def boom(enc):
            raise ValueError("bad ciphertext")

        monkeypatch.setattr(pv, "decrypt_token", boom)
        assert await pv._verify_reel("reel1", "enc", None) == "unknown"


class _StoriesClient:
    def __init__(self, stories=None, exc=None):
        self._stories = stories or []
        self._exc = exc

    async def get_own_stories(self):
        if self._exc:
            raise self._exc
        return self._stories


class TestVerifyStory:
    @pytest.fixture(autouse=True)
    def _env(self, monkeypatch):
        monkeypatch.setenv("INSTAGRAM_USERNAME", "cloudless")
        monkeypatch.setenv("INSTAGRAM_PASSWORD", "pw")

    @pytest.mark.asyncio
    async def test_expired_story_is_unknown(self):
        old = datetime.now(UTC) - timedelta(hours=30)
        assert await pv._verify_story("s1", old, None) == "unknown"

    @pytest.mark.asyncio
    async def test_missing_creds_is_unknown(self, monkeypatch):
        monkeypatch.delenv("INSTAGRAM_USERNAME")
        monkeypatch.delenv("INSTAGRAM_PASSWORD")
        assert await pv._verify_story("s1", datetime.now(UTC), None) == "unknown"
        assert await pv._verify_story("", datetime.now(UTC), None) == "unknown"

    @pytest.mark.asyncio
    async def test_live_when_story_present(self, monkeypatch):
        monkeypatch.setattr(
            pv, "InstagrapiClient",
            lambda **kw: _StoriesClient(stories=[{"pk": 111}, {"id": "story-9"}]),
        )
        assert await pv._verify_story("story-9", datetime.now(UTC), None) == "live"
        assert await pv._verify_story("999", datetime.now(UTC), None) == "failed"

    @pytest.mark.asyncio
    async def test_client_error_is_unknown(self, monkeypatch):
        monkeypatch.setattr(
            pv, "InstagrapiClient",
            lambda **kw: _StoriesClient(exc=InstagrapiError("login wall")),
        )
        assert await pv._verify_story("s1", datetime.now(UTC), None) == "unknown"


class _Exec:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Session:
    def __init__(self, rows):
        self._rows = rows
        self.statements = []
        self.committed = False

    async def execute(self, stmt, params=None):
        self.statements.append(params)
        return _Exec(self._rows)

    async def commit(self):
        self.committed = True


class _SessionCM:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *a):
        return False


class TestRun:
    @pytest.mark.asyncio
    async def test_verifies_rows_and_stamps_verdicts(self, monkeypatch):
        now = datetime.now(UTC)
        pid1, pid2, pid3 = (str(uuid.uuid4()) for _ in range(3))
        rows = [
            (pid1, {"publish_path": "graph_reels"}, now, "facebook", "reel1", "enc"),
            (pid2, {"publish_path": "sidecar_story"}, now, "instagram", "story1", None),
            (pid3, {"publish_path": "graph_reels"}, now, "twitter", "x1", None),
        ]
        session = _Session(rows)
        monkeypatch.setattr(pv, "task_session", lambda: _SessionCM(session))
        monkeypatch.setattr(pv, "_verify_reel", AsyncMock(return_value="live"))
        monkeypatch.setattr(pv, "_verify_story", AsyncMock(return_value="failed"))
        alert = AsyncMock()
        monkeypatch.setattr(pv, "send_telegram_alert", alert)

        out = await pv._run()

        assert out == {"checked": 3, "live": 1, "failed": 1, "unknown": 1}
        assert session.committed
        # live + failed rows got a platform_specific UPDATE; unknown did not
        updates = [s for s in session.statements if s and "ps" in s]
        assert len(updates) == 2
        assert updates[0]["pid"] == pid1 and '"publish_verified": "live"' in updates[0]["ps"]
        assert updates[1]["pid"] == pid2 and '"publish_verified": "failed"' in updates[1]["ps"]
        alert.assert_awaited_once()
        assert "P2" in alert.await_args.args

    @pytest.mark.asyncio
    async def test_no_rows_is_zero_summary(self, monkeypatch):
        session = _Session([])
        monkeypatch.setattr(pv, "task_session", lambda: _SessionCM(session))
        out = await pv._run()
        assert out == {"checked": 0, "live": 0, "failed": 0, "unknown": 0}
        assert session.committed
