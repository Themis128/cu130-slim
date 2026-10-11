"""Unit tests for media AI auto-tagging helpers."""

from __future__ import annotations

import base64
import io
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import app.services.media_ai as MA
from app.services import media_ai


@pytest.mark.asyncio
async def test_caption_image_dmr_success():
    with patch.object(media_ai, "_call_dmr_vision", new=AsyncMock(return_value="A sunset over mountains")):
        caption = await media_ai._caption_image("data:image/png;base64,abc")
    assert caption == "A sunset over mountains"


@pytest.mark.asyncio
async def test_caption_image_dmr_fails_cf_fallback():
    with (
        patch.object(media_ai, "_call_dmr_vision", new=AsyncMock(return_value=None)),
        patch.object(media_ai, "_call_cloudflare_vision", new=AsyncMock(return_value={"description": "A lake in the woods"})),
    ):
        caption = await media_ai._caption_image("data:image/png;base64,abc")
    assert caption == "A lake in the woods"


@pytest.mark.asyncio
async def test_tag_image_cloudflare_success():
    with patch.object(
        media_ai,
        "_call_cloudflare_vision",
        new=AsyncMock(return_value={"description": "sunset, mountains, clouds"}),
    ):
        tags = await media_ai._tag_image("data:image/png;base64,abc")
    assert set(tags) == {"sunset", "mountains", "clouds"}


@pytest.mark.asyncio
async def test_tag_image_filters_instruction_words():
    with patch.object(
        media_ai,
        "_call_cloudflare_vision",
        new=AsyncMock(return_value={"description": "list, image, sunset, mountains, image"}),
    ):
        tags = await media_ai._tag_image("data:image/png;base64,abc")
    assert "list" not in tags
    assert "image" not in tags
    assert "sunset" in tags
    assert "mountains" in tags


def test_resize_for_vision_downscales_large_image():
    """Large images should be resized before sending to vision models."""
    import io

    from PIL import Image

    img = Image.new("RGB", (2000, 1000), color=(255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    resized, mime = media_ai._resize_for_vision(buf.getvalue(), max_edge=768)
    resized_img = Image.open(io.BytesIO(resized))
    assert max(resized_img.size) <= 768
    assert mime == "image/png"


@pytest.mark.asyncio
async def test_extract_caption_prefers_description():
    assert media_ai._extract_caption({"description": "x"}) == "x"
    assert media_ai._extract_caption({"result": {"caption": "y"}}) == "y"
    assert media_ai._extract_caption({"text": "z"}) == "z"


@pytest.mark.asyncio
async def test_extract_caption_llama4_scout_response():
    """llama-4-scout returns result.response (chat completion format)."""
    result = {"result": {"response": "A blue square image", "choices": [{"message": {"content": "A blue square image"}}]}}
    assert media_ai._extract_caption(result) == "A blue square image"


@pytest.mark.asyncio
async def test_extract_caption_moondream_nested_result():
    """moondream returns result.result.caption."""
    result = {"result": {"result": {"caption": "A photo of a cat"}}}
    assert media_ai._extract_caption(result) == "A photo of a cat"


@pytest.mark.asyncio
async def test_extract_query_text_falls_back():
    assert media_ai._extract_query_text({"description": "x"}) == "x"
    assert media_ai._extract_query_text({"result": {"response": "y"}}) == "y"


@pytest.mark.asyncio
async def test_extract_query_text_moondream_answer():
    """moondream query returns result.result.answer."""
    result = {"result": {"result": {"answer": "Yes, there is a person"}}}
    assert media_ai._extract_query_text(result) == "Yes, there is a person"


# ---------------------------------------------------------------------------
# Extended coverage: byte helpers, loaders, CF vision, orchestration
# ---------------------------------------------------------------------------


def test_data_uri_and_raw_b64():
    b = b"hello"
    assert MA._data_uri(b) == "data:image/png;base64," + base64.b64encode(b).decode()
    assert MA._data_uri(b, "image/jpeg").startswith("data:image/jpeg;base64,")
    assert MA._raw_b64(b) == base64.b64encode(b).decode()


class TestLoadImageBytes:
    def _asset(self, backend="r2", path="p/x.png"):
        return SimpleNamespace(id=uuid.uuid4(), storage_backend=backend, storage_path=path)

    @pytest.mark.asyncio
    async def test_r2_hit(self, monkeypatch):
        monkeypatch.setattr(MA.r2_storage, "get_object", AsyncMock(return_value=b"r2data"))
        assert await MA._load_image_bytes(self._asset()) == b"r2data"

    @pytest.mark.asyncio
    async def test_r2_miss_falls_to_local(self, monkeypatch, tmp_path):
        monkeypatch.setattr(MA.r2_storage, "get_object", AsyncMock(return_value=None))
        monkeypatch.setattr(MA, "UPLOAD_DIR", str(tmp_path))
        (tmp_path / "x.png").write_bytes(b"local")
        assert await MA._load_image_bytes(self._asset(path="x.png")) == b"local"

    @pytest.mark.asyncio
    async def test_r2_raises_falls_to_local(self, monkeypatch, tmp_path):
        monkeypatch.setattr(MA.r2_storage, "get_object", AsyncMock(side_effect=OSError("r2 down")))
        monkeypatch.setattr(MA, "UPLOAD_DIR", str(tmp_path))
        (tmp_path / "x.png").write_bytes(b"local")
        assert await MA._load_image_bytes(self._asset(path="x.png")) == b"local"

    @pytest.mark.asyncio
    async def test_minio_hit(self, monkeypatch):
        monkeypatch.setattr(MA.minio_storage, "get_object", AsyncMock(return_value=b"minio"))
        assert await MA._load_image_bytes(self._asset(backend="minio")) == b"minio"

    @pytest.mark.asyncio
    async def test_minio_raises_local_missing(self, monkeypatch, tmp_path):
        monkeypatch.setattr(MA.minio_storage, "get_object", AsyncMock(side_effect=OSError("minio down")))
        monkeypatch.setattr(MA, "UPLOAD_DIR", str(tmp_path))
        assert await MA._load_image_bytes(self._asset(backend="minio", path="nope.png")) is None


class _HTTP:
    def __init__(self, resps):
        self._resps = list(resps)
        self.posts = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **kw):
        self.posts.append((url, kw))
        r = self._resps.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _resp(status, body=None, text=""):
    return SimpleNamespace(status_code=status, text=text, json=lambda: body or {})


def _cf_settings(monkeypatch, **kw):
    s = SimpleNamespace(CLOUDFLARE_ACCOUNT_ID="acc", CLOUDFLARE_AI_API_TOKEN="tok", CLOUDFLARE_API_TOKEN="", DMR_MEDIA_MODEL="", DMR_MID_MODEL="m")
    for k, v in kw.items():
        setattr(s, k, v)
    monkeypatch.setattr(MA, "settings", s)
    return s


class TestCloudflareVision:
    @pytest.mark.asyncio
    async def test_no_credentials(self, monkeypatch):
        _cf_settings(monkeypatch, CLOUDFLARE_ACCOUNT_ID="", CLOUDFLARE_AI_API_TOKEN="", CLOUDFLARE_API_TOKEN="")
        with pytest.raises(RuntimeError, match="credentials"):
            await MA._call_cloudflare_vision("b64", "caption", "p")

    @pytest.mark.asyncio
    async def test_llama4_success(self, monkeypatch):
        _cf_settings(monkeypatch)
        c = _HTTP([_resp(200, {"result": {"response": "cap"}})])
        monkeypatch.setattr(MA.httpx, "AsyncClient", lambda **kw: c)
        out = await MA._call_cloudflare_vision("b64data", "caption", "p")
        assert out["result"]["response"] == "cap"
        body = c.posts[0][1]["json"]
        assert body["messages"][0]["content"][0]["type"] == "text"
        assert "llama-4-scout" in c.posts[0][0]
        # data URI added for raw b64
        assert c.posts[0][1]["json"]["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")

    @pytest.mark.asyncio
    async def test_llama4_non200_falls_to_moondream(self, monkeypatch):
        _cf_settings(monkeypatch)
        c = _HTTP([_resp(500, text="bad"), _resp(200, {"result": {"caption": "moon"}})])
        monkeypatch.setattr(MA.httpx, "AsyncClient", lambda **kw: c)
        out = await MA._call_cloudflare_vision("data:image/png;base64,x", "caption", "p")
        assert out["result"]["caption"] == "moon"
        assert "moondream" in c.posts[1][0]
        assert c.posts[1][1]["json"]["caption_length"] == "normal"

    @pytest.mark.asyncio
    async def test_llama4_exception_falls_to_moondream(self, monkeypatch):
        _cf_settings(monkeypatch)
        c = _HTTP([OSError("net"), _resp(200, {"result": {"answer": "a"}})])
        monkeypatch.setattr(MA.httpx, "AsyncClient", lambda **kw: c)
        out = await MA._call_cloudflare_vision("b64", "query", "what?")
        assert c.posts[1][1]["json"]["question"] == "what?"
        assert out["result"]["answer"] == "a"

    @pytest.mark.asyncio
    async def test_moondream_other_task_prompt(self, monkeypatch):
        _cf_settings(monkeypatch)
        c = _HTTP([_resp(500), _resp(200, {})])
        monkeypatch.setattr(MA.httpx, "AsyncClient", lambda **kw: c)
        await MA._call_cloudflare_vision("b64", "detect", "find cats")
        assert c.posts[1][1]["json"]["prompt"] == "find cats"

    @pytest.mark.asyncio
    async def test_moondream_error_raises(self, monkeypatch):
        _cf_settings(monkeypatch)
        c = _HTTP([_resp(500), _resp(502, text="gateway")])
        monkeypatch.setattr(MA.httpx, "AsyncClient", lambda **kw: c)
        with pytest.raises(RuntimeError, match="502"):
            await MA._call_cloudflare_vision("b64", "caption", "p")


@pytest.mark.asyncio
async def test_call_dmr_vision_delegates(monkeypatch):
    import app.services.dmr as dmr

    fake = AsyncMock(return_value="dmr text")
    monkeypatch.setattr(dmr, "call_dmr_vision", fake)
    assert await MA._call_dmr_vision("b", "p", max_tokens=10) == "dmr text"
    fake.assert_awaited_once_with("b", "p", max_tokens=10)


class TestCaptionAndTagEdges:
    @pytest.mark.asyncio
    async def test_caption_both_fail(self, monkeypatch):
        monkeypatch.setattr(MA, "_call_dmr_vision", AsyncMock(side_effect=OSError("dmr")))
        monkeypatch.setattr(MA, "_call_cloudflare_vision", AsyncMock(side_effect=OSError("cf")))
        assert await MA._caption_image("b") is None

    @pytest.mark.asyncio
    async def test_tag_dmr_path(self, monkeypatch):
        monkeypatch.setattr(MA, "_call_dmr_vision", AsyncMock(return_value="cat, dog , 'bird'"))
        assert await MA._tag_image("b") == ["cat", "dog", "bird"]

    @pytest.mark.asyncio
    async def test_tag_both_fail_empty(self, monkeypatch):
        monkeypatch.setattr(MA, "_call_dmr_vision", AsyncMock(side_effect=OSError("x")))
        monkeypatch.setattr(MA, "_call_cloudflare_vision", AsyncMock(side_effect=OSError("y")))
        assert await MA._tag_image("b") == []

    @pytest.mark.asyncio
    async def test_tag_dmr_empty_cf_returns_none(self, monkeypatch):
        monkeypatch.setattr(MA, "_call_dmr_vision", AsyncMock(return_value=""))
        monkeypatch.setattr(MA, "_call_cloudflare_vision", AsyncMock(return_value={}))
        assert await MA._tag_image("b") == []


class TestResizeVariants:
    def _png(self, size=(100, 100), mode="RGB", fmt="PNG"):
        from PIL import Image

        img = Image.new(mode, size)
        buf = io.BytesIO()
        img.save(buf, format=fmt)
        return buf.getvalue()

    def test_jpeg_mime_override(self):
        out, mime = MA._resize_for_vision(self._png(fmt="JPEG"), mime_type="image/jpeg")
        assert mime == "image/jpeg"

    def test_webp_mime(self):
        out, mime = MA._resize_for_vision(self._png(), mime_type="image/webp")
        assert mime == "image/webp"

    def test_rgba_to_jpeg_converts(self):
        rgba = self._png(mode="RGBA")
        out, mime = MA._resize_for_vision(rgba, mime_type="image/jpeg")
        assert mime == "image/jpeg"
        from PIL import Image

        assert Image.open(io.BytesIO(out)).mode == "RGB"

    def test_palette_to_png_rgba(self):
        p = self._png(mode="P")
        out, mime = MA._resize_for_vision(p)
        from PIL import Image

        assert Image.open(io.BytesIO(out)).mode == "RGBA"

    def test_invalid_bytes_passthrough(self):
        out, mime = MA._resize_for_vision(b"not an image")
        assert out == b"not an image" and mime == "image/png"


class _DB:
    def __init__(self, asset):
        self._asset = asset
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a, **kw):
        return SimpleNamespace(scalar_one_or_none=lambda: self._asset)

    async def commit(self):
        self.commits += 1

    async def refresh(self, a):
        pass


def _asset_obj(**kw):
    d = dict(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        storage_backend="local",
        storage_path="x.png",
        mime_type="image/png",
        ai_caption=None,
        ai_tags=None,
        embedding_id=None,
    )
    d.update(kw)
    return SimpleNamespace(**d)


class TestAutoTagAsset:
    @pytest.mark.asyncio
    async def test_not_found(self, monkeypatch):
        def factory():
            return _DB(None)

        await MA.auto_tag_asset(uuid.uuid4(), session_factory=factory)

    @pytest.mark.asyncio
    async def test_non_image_skipped(self, monkeypatch):
        asset = _asset_obj(mime_type="video/mp4")
        await MA.auto_tag_asset(str(asset.id), session_factory=lambda: _DB(asset))

    @pytest.mark.asyncio
    async def test_no_bytes(self, monkeypatch, tmp_path):
        monkeypatch.setattr(MA, "UPLOAD_DIR", str(tmp_path))
        asset = _asset_obj(storage_path="missing.png")
        await MA.auto_tag_asset(asset.id, session_factory=lambda: _DB(asset))
        assert asset.embedding_id is None

    @pytest.mark.asyncio
    async def test_full_path(self, monkeypatch, tmp_path):
        from PIL import Image

        img = Image.new("RGB", (10, 10))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        (tmp_path / "x.png").write_bytes(buf.getvalue())
        monkeypatch.setattr(MA, "UPLOAD_DIR", str(tmp_path))
        monkeypatch.setattr(MA, "_caption_image", AsyncMock(return_value="A cat"))
        monkeypatch.setattr(MA, "_tag_image", AsyncMock(return_value=["cat", "pet"]))
        monkeypatch.setattr(MA, "correct_text", AsyncMock(side_effect=lambda t: t + "!"))
        monkeypatch.setattr(MA, "correct_tags", AsyncMock(side_effect=lambda t: t + ["fixed"]))
        add = AsyncMock()
        monkeypatch.setattr(MA.chroma_client, "add_content", add)
        asset = _asset_obj(storage_path="x.png")
        db = _DB(asset)
        await MA.auto_tag_asset(asset.id, session_factory=lambda: db)
        assert asset.ai_caption == "A cat!"
        assert asset.ai_tags == ["cat", "pet", "fixed"]
        assert asset.embedding_id == str(asset.id)
        assert db.commits == 1
        add.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_caption_no_tags(self, monkeypatch, tmp_path):
        from PIL import Image

        img = Image.new("RGB", (10, 10))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        (tmp_path / "x.png").write_bytes(buf.getvalue())
        monkeypatch.setattr(MA, "UPLOAD_DIR", str(tmp_path))
        monkeypatch.setattr(MA, "_caption_image", AsyncMock(return_value=None))
        monkeypatch.setattr(MA, "_tag_image", AsyncMock(return_value=[]))
        add = AsyncMock()
        monkeypatch.setattr(MA.chroma_client, "add_content", add)
        asset = _asset_obj(storage_path="x.png")
        await MA.auto_tag_asset(asset.id, session_factory=lambda: _DB(asset))
        add.assert_not_awaited()  # no text to index


class TestGetSimilarAssets:
    @pytest.mark.asyncio
    async def test_not_found(self, monkeypatch):
        monkeypatch.setattr(MA, "async_session_maker", lambda: _DB(None))
        assert await MA.get_similar_assets(uuid.uuid4(), uuid.uuid4()) == []

    @pytest.mark.asyncio
    async def test_no_text(self, monkeypatch):
        asset = _asset_obj()
        monkeypatch.setattr(MA, "async_session_maker", lambda: _DB(asset))
        assert await MA.get_similar_assets(asset.team_id, asset.id) == []

    @pytest.mark.asyncio
    async def test_filters_self(self, monkeypatch):
        asset = _asset_obj(ai_caption="cat", ai_tags=["pet"])
        monkeypatch.setattr(MA, "async_session_maker", lambda: _DB(asset))
        ids = [str(asset.id), "other1", "other2", "", "other3"]
        monkeypatch.setattr(MA.chroma_client, "query_similar", AsyncMock(return_value=ids))
        out = await MA.get_similar_assets(asset.team_id, asset.id, n_results=2)
        assert [d["embedding_id"] for d in out] == ["other1", "other2"]


class TestExpandVisualPrompt:
    @pytest.mark.asyncio
    async def test_empty_prompt(self):
        assert await MA.expand_visual_prompt("") == ""
        assert await MA.expand_visual_prompt("   ") == "   "

    async def _dmr(self, monkeypatch, text):
        import app.services.dmr as dmr

        monkeypatch.setattr(dmr, "call_dmr_chat", AsyncMock(return_value={"text": text}))

    @pytest.mark.asyncio
    async def test_success(self, monkeypatch):
        _cf_settings(monkeypatch)
        await self._dmr(monkeypatch, '  "a detailed scene"  ')
        out = await MA.expand_visual_prompt("a cat")
        assert out == "a detailed scene"

    @pytest.mark.asyncio
    async def test_video_system_prompt(self, monkeypatch):
        _cf_settings(monkeypatch)
        import app.services.dmr as dmr

        fake = AsyncMock(return_value={"text": "expanded"})
        monkeypatch.setattr(dmr, "call_dmr_chat", fake)
        await MA.expand_visual_prompt("p", media_type="video")
        assert "camera motion" in fake.await_args.kwargs["system"]

    @pytest.mark.asyncio
    async def test_guards(self, monkeypatch):
        _cf_settings(monkeypatch)
        for bad in ["", "x" * 1300, "same prompt", "has <markup>"]:
            await self._dmr(monkeypatch, bad)
            assert await MA.expand_visual_prompt("same prompt") == "same prompt"

    @pytest.mark.asyncio
    async def test_exception_returns_prompt(self, monkeypatch):
        _cf_settings(monkeypatch)
        import app.services.dmr as dmr

        monkeypatch.setattr(dmr, "call_dmr_chat", AsyncMock(side_effect=OSError("down")))
        assert await MA.expand_visual_prompt("keep me") == "keep me"


class TestVerifyMediaSemantics:
    @pytest.mark.asyncio
    async def test_vision_unavailable(self, monkeypatch):
        monkeypatch.setattr(MA, "_call_dmr_vision", AsyncMock(return_value=None))
        out = await MA.verify_media_semantics(b"img", "a cat")
        assert out["match"] is True
        assert out["reason"] == "vision unavailable"

    @pytest.mark.asyncio
    async def test_match_yes(self, monkeypatch):
        calls = iter(["A cat on a mat", "YES it matches"])
        monkeypatch.setattr(MA, "_call_dmr_vision", AsyncMock(side_effect=lambda *a, **k: next(calls)))
        out = await MA.verify_media_semantics(b"img", "a cat")
        assert out["match"] is True
        assert out["caption"] == "A cat on a mat"

    @pytest.mark.asyncio
    async def test_match_no(self, monkeypatch):
        calls = iter(["A dog", "NO totally different subject"])
        monkeypatch.setattr(MA, "_call_dmr_vision", AsyncMock(side_effect=lambda *a, **k: next(calls)))
        out = await MA.verify_media_semantics(b"img", "a cat")
        assert out["match"] is False

    @pytest.mark.asyncio
    async def test_exception_match_true(self, monkeypatch):
        monkeypatch.setattr(MA, "_call_dmr_vision", AsyncMock(side_effect=OSError("down")))
        out = await MA.verify_media_semantics(b"img", "a cat")
        assert out["match"] is True
        assert "qa error" in out["reason"]
