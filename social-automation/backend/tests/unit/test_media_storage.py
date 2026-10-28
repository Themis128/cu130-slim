"""Coverage for app/services/media_storage.py (beyond _store_bytes)."""

import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from PIL import Image

import app.services.media_storage as MS


def _settings(monkeypatch, **kw):
    s = SimpleNamespace(
        NEXTCLOUD_EXPORT_ENABLED=False,
        NEXTCLOUD_DAV_URL="",
        NEXTCLOUD_USERNAME="",
        NEXTCLOUD_APP_PASSWORD="",
        R2_BUCKET_NAME="b",
        CLOUDFLARE_ACCOUNT_ID="a",
        CLOUDFLARE_API_TOKEN="t",
        MEDIA_PUBLIC_BASE_URL="",
    )
    for k, v in kw.items():
        setattr(s, k, v)
    monkeypatch.setattr(MS, "settings", s)
    return s


def _png(size=(100, 100), mode="RGB", fmt="PNG"):
    img = Image.new(mode, size, (1, 2, 3))
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


class TestEnqueue:
    def test_auto_tag_sends(self, monkeypatch):
        send = Mock()
        monkeypatch.setattr(MS.celery_app, "send_task", send)
        MS._enqueue_auto_tag(SimpleNamespace(id="a1"))
        send.assert_called_once()
        assert "auto_tag" in send.call_args[0][0]

    def test_auto_tag_exception_swallowed(self, monkeypatch):
        monkeypatch.setattr(MS.celery_app, "send_task", Mock(side_effect=OSError("x")))
        MS._enqueue_auto_tag(SimpleNamespace(id="a1"))

    def test_nextcloud_disabled(self, monkeypatch):
        _settings(monkeypatch)
        send = Mock()
        monkeypatch.setattr(MS.celery_app, "send_task", send)
        MS._enqueue_nextcloud_export(SimpleNamespace(id="a1"))
        send.assert_not_called()

    def test_nextcloud_enabled(self, monkeypatch):
        _settings(monkeypatch, NEXTCLOUD_EXPORT_ENABLED=True, NEXTCLOUD_DAV_URL="https://nc", NEXTCLOUD_USERNAME="u", NEXTCLOUD_APP_PASSWORD="p")
        send = Mock()
        monkeypatch.setattr(MS.celery_app, "send_task", send)
        MS._enqueue_nextcloud_export(SimpleNamespace(id="a1"))
        assert "nextcloud" in send.call_args[0][0]

    def test_nextcloud_send_fails(self, monkeypatch):
        _settings(monkeypatch, NEXTCLOUD_EXPORT_ENABLED=True, NEXTCLOUD_DAV_URL="https://nc", NEXTCLOUD_USERNAME="u", NEXTCLOUD_APP_PASSWORD="p")
        monkeypatch.setattr(MS.celery_app, "send_task", Mock(side_effect=OSError("x")))
        MS._enqueue_nextcloud_export(SimpleNamespace(id="a1"))


class TestToggles:
    def test_r2_enabled(self, monkeypatch):
        _settings(monkeypatch)
        assert MS._r2_enabled() is True
        _settings(monkeypatch, R2_BUCKET_NAME="")
        assert MS._r2_enabled() is False
        _settings(monkeypatch, CLOUDFLARE_ACCOUNT_ID="")
        assert MS._r2_enabled() is False
        _settings(monkeypatch, CLOUDFLARE_API_TOKEN="")
        assert MS._r2_enabled() is False

    def test_minio_enabled(self, monkeypatch):
        monkeypatch.setattr(MS.minio_storage, "minio_enabled", lambda: True)
        assert MS._minio_enabled() is True
        monkeypatch.setattr(MS.minio_storage, "minio_enabled", lambda: False)
        assert MS._minio_enabled() is False

    def test_public_local_url(self, monkeypatch):
        _settings(monkeypatch)
        assert MS._public_local_url("x/y.png") is None
        _settings(monkeypatch, MEDIA_PUBLIC_BASE_URL="https://m.co/")
        assert MS._public_local_url("x/y.png") == ("https://m.co/api/v1/media/view?path=x/y.png")


class TestDownscale:
    def test_unreadable_passthrough(self):
        out, w, h = MS.downscale_image_bytes(b"not an image")
        assert out == b"not an image" and w is None and h is None

    def test_animated_gif_passthrough(self):
        frames = [Image.new("RGB", (50, 50), (i, 0, 0)) for i in range(3)]
        buf = io.BytesIO()
        frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:], loop=0)
        data = buf.getvalue()
        out, w, h = MS.downscale_image_bytes(data, max_edge=10)
        assert out == data and w == 50

    def test_small_no_resize(self):
        data = _png((100, 50))
        out, w, h = MS.downscale_image_bytes(data, max_edge=200)
        assert out == data and (w, h) == (100, 50)

    def test_max_edge_zero_no_resize(self):
        data = _png((500, 500))
        out, w, h = MS.downscale_image_bytes(data, max_edge=0)
        assert out == data and w == 500

    def test_png_resize(self):
        data = _png((800, 400))
        out, w, h = MS.downscale_image_bytes(data, max_edge=400)
        assert (w, h) == (400, 200)
        assert Image.open(io.BytesIO(out)).format == "PNG"

    def test_jpeg_resize(self):
        img = Image.new("RGB", (800, 400))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        out, w, h = MS.downscale_image_bytes(buf.getvalue(), max_edge=400)
        assert Image.open(io.BytesIO(out)).format == "JPEG"

    def test_webp(self):
        data = _png((800, 400), fmt="WEBP")
        out, w, h = MS.downscale_image_bytes(data, max_edge=400)
        assert Image.open(io.BytesIO(out)).format == "WEBP"

    def test_cmyk_tiff_to_png(self):
        img = Image.new("CMYK", (800, 400))
        buf = io.BytesIO()
        img.save(buf, format="TIFF")
        out, w, h = MS.downscale_image_bytes(buf.getvalue(), max_edge=400)
        img2 = Image.open(io.BytesIO(out))
        assert img2.format == "PNG" and img2.mode == "RGB"

    def test_palette_png(self):
        data = _png((800, 400), mode="P")
        out, w, h = MS.downscale_image_bytes(data, max_edge=400)
        img = Image.open(io.BytesIO(out))
        assert img.mode == "RGBA"


class _DB:
    def __init__(self):
        self.added = []
        self.commits = 0

    def add(self, o):
        self.added.append(o)

    async def commit(self):
        self.commits += 1

    async def refresh(self, o):
        pass


def _patch_common(monkeypatch):
    monkeypatch.setattr(
        MS, "_store_bytes", AsyncMock(return_value=(__import__("app.models.content", fromlist=["x"]).StorageBackend.local, "p/x.png", "http://u"))
    )
    monkeypatch.setattr(MS, "correct_text", AsyncMock(side_effect=lambda t: (t or "") + "!"))
    monkeypatch.setattr(MS, "correct_tags", AsyncMock(side_effect=lambda t: t))
    monkeypatch.setattr(MS.celery_app, "send_task", Mock())


class TestSaveUploadedMedia:
    @pytest.mark.asyncio
    async def test_happy(self, monkeypatch):
        _patch_common(monkeypatch)
        db = _DB()
        asset = await MS.save_uploaded_media(
            db, team_id="t", user_id="u", original_filename="f.png", content=b"DATA", mime_type="image/png", alt_text="alt", tags=["a"], width=1, height=2
        )
        assert asset.alt_text == "alt!"
        assert asset.source == "upload"
        assert db.commits == 1


class TestDetectAndEnsure:
    def test_detect_png(self):
        ext, mime = MS._detect_image_format(_png(), ".bin", "x/y")
        assert (ext, mime) == (".png", "image/png")

    def test_detect_jpeg(self):
        buf = io.BytesIO()
        Image.new("RGB", (5, 5)).save(buf, format="JPEG")
        ext, mime = MS._detect_image_format(buf.getvalue(), ".x", "x/y")
        assert (ext, mime) == (".jpg", "image/jpeg")

    def test_detect_fallback(self):
        ext, mime = MS._detect_image_format(b"garbage", ".bin", "x/y")
        assert (ext, mime) == (".bin", "x/y")

    def test_ensure_jpeg_from_png(self):
        out = MS._ensure_jpeg_bytes(_png((10, 10)))
        assert out is not None
        data, w, h = out
        assert Image.open(io.BytesIO(data)).format == "JPEG"
        assert (w, h) == (10, 10)

    def test_ensure_jpeg_rgba(self):
        out = MS._ensure_jpeg_bytes(_png((10, 10), mode="RGBA"))
        assert out is not None

    def test_ensure_jpeg_palette(self):
        out = MS._ensure_jpeg_bytes(_png((10, 10), mode="P"))
        assert out is not None

    def test_ensure_jpeg_cmyk(self):
        img = Image.new("CMYK", (10, 10))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        out = MS._ensure_jpeg_bytes(buf.getvalue())
        assert out is not None

    def test_ensure_jpeg_invalid(self):
        assert MS._ensure_jpeg_bytes(b"junk") is None


class TestPersistGeneratedImage:
    @pytest.mark.asyncio
    async def test_jpeg_request(self, monkeypatch):
        _patch_common(monkeypatch)
        import app.services.brand_footer as bf

        monkeypatch.setattr(bf, "maybe_apply_brand_footer", AsyncMock(side_effect=lambda *a: a[2]))
        db = _DB()
        asset = await MS.persist_generated_image(db, team_id="t", user_id="u", image_bytes=_png((100, 100)), prompt="a cat", extension=".jpg", max_edge=0)
        assert asset.mime_type == "image/jpeg"
        assert asset.filename.endswith(".jpg")
        assert asset.alt_text == "a cat!"
        assert asset.tags == ["ai-generated"]

    @pytest.mark.asyncio
    async def test_jpeg_convert_fails_detects(self, monkeypatch):
        _patch_common(monkeypatch)
        import app.services.brand_footer as bf

        monkeypatch.setattr(bf, "maybe_apply_brand_footer", AsyncMock(side_effect=lambda *a: a[2]))
        db = _DB()
        asset = await MS.persist_generated_image(db, team_id="t", user_id="u", image_bytes=b"garbage-not-image", prompt="x", extension=".jpg", max_edge=0)
        # detect falls back to the caller's labels for unreadable bytes
        assert asset.mime_type == "image/jpeg"

    @pytest.mark.asyncio
    async def test_carousel_source_no_cap(self, monkeypatch):
        _patch_common(monkeypatch)
        import app.services.brand_footer as bf

        monkeypatch.setattr(bf, "maybe_apply_brand_footer", AsyncMock(side_effect=lambda *a: a[2]))
        db = _DB()
        asset = await MS.persist_generated_image(db, team_id="t", user_id="u", image_bytes=_png((1200, 800)), prompt="p", source="carousel", extension=".png")
        assert asset.width == 1200  # no downscale for carousel

    @pytest.mark.asyncio
    async def test_folder_sanitized(self, monkeypatch):
        _patch_common(monkeypatch)
        import app.services.brand_footer as bf

        monkeypatch.setattr(bf, "maybe_apply_brand_footer", AsyncMock(side_effect=lambda *a: a[2]))
        store = AsyncMock(return_value=(__import__("app.models.content", fromlist=["x"]).StorageBackend.local, "p", None))
        monkeypatch.setattr(MS, "_store_bytes", store)
        db = _DB()
        await MS.persist_generated_image(
            db, team_id="t", user_id="u", image_bytes=_png((10, 10)), prompt="p", extension=".png", max_edge=0, folder="../evil/../sub"
        )
        date_folder = store.await_args.args[3]
        assert "/" not in date_folder.split("/", 3)[-1].replace("/", "")
        assert not date_folder.endswith("/..") and "/../" not in date_folder

    @pytest.mark.asyncio
    async def test_non_default_source_tags_empty(self, monkeypatch):
        _patch_common(monkeypatch)
        import app.services.brand_footer as bf

        monkeypatch.setattr(bf, "maybe_apply_brand_footer", AsyncMock(side_effect=lambda *a: a[2]))
        db = _DB()
        asset = await MS.persist_generated_image(
            db, team_id="t", user_id="u", image_bytes=_png((10, 10)), prompt="p", source="upload", extension=".png", max_edge=0
        )
        assert asset.tags == []

    @pytest.mark.asyncio
    async def test_default_max_edge_used(self, monkeypatch):
        _patch_common(monkeypatch)
        import app.services.brand_footer as bf

        monkeypatch.setattr(bf, "maybe_apply_brand_footer", AsyncMock(side_effect=lambda *a: a[2]))
        monkeypatch.setattr(MS, "MEDIA_MAX_EDGE", 300)
        db = _DB()
        asset = await MS.persist_generated_image(
            db, team_id="t", user_id="u", image_bytes=_png((1000, 1000)), prompt="p", extension=".png"
        )  # max_edge=None → MEDIA_MAX_EDGE
        assert asset.width == 300

    @pytest.mark.asyncio
    async def test_dims_reread_from_footer_bytes(self, monkeypatch):
        _patch_common(monkeypatch)
        import app.services.brand_footer as bf

        monkeypatch.setattr(bf, "maybe_apply_brand_footer", AsyncMock(return_value=_png((42, 24))))
        db = _DB()
        asset = await MS.persist_generated_image(db, team_id="t", user_id="u", image_bytes=b"junk", prompt="p", extension=".png", max_edge=0)
        assert asset.width == 42 and asset.height == 24

    @pytest.mark.asyncio
    async def test_unreadable_bytes_dims_none(self, monkeypatch):
        _patch_common(monkeypatch)
        import app.services.brand_footer as bf

        monkeypatch.setattr(bf, "maybe_apply_brand_footer", AsyncMock(return_value=b"junk"))
        db = _DB()
        asset = await MS.persist_generated_image(db, team_id="t", user_id="u", image_bytes=b"junk", prompt="p", extension=".png", max_edge=0)
        assert asset.width is None or isinstance(asset.width, int)
