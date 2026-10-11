"""Coverage for pure-function services: brand_footer, brand_assets,
platforms/base drivers, tiktok_xbogus."""
import io
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image

import app.services.brand_assets as BA
import app.services.brand_footer as BF
import app.services.platforms.base as PBASE
import app.services.tiktok_xbogus as XB


def _png(w=800, h=600):
    img = Image.new("RGB", (w, h), (10, 20, 30))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestBrandFooter:
    def test_hex_rgb(self):
        assert BF._hex_rgb(None) is None
        assert BF._hex_rgb("") is None
        assert BF._hex_rgb("#fff") == (255, 255, 255)
        assert BF._hex_rgb("00ff41") == (0, 255, 65)
        assert BF._hex_rgb("#zzzzzz") is None
        assert BF._hex_rgb("#12") is None

    def test_font_fallbacks(self, monkeypatch):
        # real load path
        f = BF._font(20, "bold")
        assert f is not None
        # force failure → fallback font
        from PIL import ImageFont
        calls = []

        sentinel = ImageFont.ImageFont()

        def fake_truetype(path, size, **kw):
            calls.append(path)
            if len(calls) == 1:
                raise OSError("missing")
            return sentinel
        monkeypatch.setattr(BF.ImageFont, "truetype", fake_truetype)
        assert BF._font(20) is sentinel  # fallback font used

        monkeypatch.setattr(BF.ImageFont, "truetype",
                            lambda *a, **kw: (_ for _ in ()).throw(OSError()))
        monkeypatch.setattr(BF.ImageFont, "load_default", lambda **kw: sentinel)
        assert BF._font(20, "semibold") is sentinel

    def test_apply_skips_small(self):
        small = _png(200, 200)
        assert BF.apply_brand_footer(small, {"name": "X"}) == small

    def test_apply_full(self):
        out = BF.apply_brand_footer(
            _png(800, 600),
            {"name": "Cloudless", "domain": "cloudless.gr",
             "accent": (255, 0, 255)})
        img = Image.open(io.BytesIO(out))
        assert img.size == (800, 600)
        # hairline pixel at top of footer bar is accent-colored
        px = img.convert("RGB").getpixel((400, 600 - max(44, round(600 * 0.0625))))
        # accent (255,0,255) composited over dark bg: red+blue dominate
        assert px[0] > 200 and px[2] > 200 and px[1] < 80

    def test_apply_empty_brand_fields(self):
        out = BF.apply_brand_footer(_png(600, 500), {"name": "", "domain": "",
                                                    "accent": None})
        assert Image.open(io.BytesIO(out)).size == (600, 500)

    @pytest.mark.asyncio
    async def test_load_footer_brand(self, monkeypatch):
        assert await BF.load_footer_brand(None, None) is None

        class _Res:
            def __init__(self, brand):
                self._b = brand

            def scalars(self):
                return SimpleNamespace(first=lambda: self._b)

        db = SimpleNamespace(execute=AsyncMock(return_value=_Res(None)))
        assert await BF.load_footer_brand(db, uuid.uuid4()) is None

        brand = SimpleNamespace(
            name="Cloudless", website_url="https://www.cloudless.gr/x",
            tagline="t", visual=SimpleNamespace(accent_color="#ff00ff"))
        db = SimpleNamespace(execute=AsyncMock(return_value=_Res(brand)))
        out = await BF.load_footer_brand(db, uuid.uuid4())
        assert out == {"name": "Cloudless", "domain": "cloudless.gr",
                       "accent": (255, 0, 255)}

        # no website → tagline; no visual → default accent
        brand2 = SimpleNamespace(name="B", website_url="", tagline="tag",
                                 visual=None)
        db = SimpleNamespace(execute=AsyncMock(return_value=_Res(brand2)))
        out = await BF.load_footer_brand(db, uuid.uuid4())
        assert out["domain"] == "tag"
        assert out["accent"] == BF._DEFAULT_ACCENT

    @pytest.mark.asyncio
    async def test_maybe_apply_paths(self, monkeypatch):
        img = _png(600, 500)
        # disabled env
        monkeypatch.setenv("BRAND_FOOTER_DISABLED", "1")
        assert await BF.maybe_apply_brand_footer(None, None, img, "x") == img
        monkeypatch.delenv("BRAND_FOOTER_DISABLED")
        # exempt source
        assert await BF.maybe_apply_brand_footer(
            None, None, img, "carousel") == img
        # no brand
        monkeypatch.setattr(BF, "load_footer_brand",
                            AsyncMock(return_value=None))
        assert await BF.maybe_apply_brand_footer(
            None, uuid.uuid4(), img, "gen") == img
        # brand without name
        monkeypatch.setattr(BF, "load_footer_brand",
                            AsyncMock(return_value={"name": ""}))
        assert await BF.maybe_apply_brand_footer(
            None, uuid.uuid4(), img, "gen") == img
        # happy path
        monkeypatch.setattr(BF, "load_footer_brand", AsyncMock(
            return_value={"name": "C", "domain": "d", "accent": None}))
        out = await BF.maybe_apply_brand_footer(None, uuid.uuid4(), img, "gen")
        assert out != img
        # footer failure → original bytes
        monkeypatch.setattr(BF, "load_footer_brand",
                            AsyncMock(side_effect=RuntimeError()))
        assert await BF.maybe_apply_brand_footer(
            None, uuid.uuid4(), img, "gen") == img


class TestBrandAssets:
    def test_hex_to_rgb(self):
        assert BA._hex_to_rgb("#abc") == (170, 187, 204)
        assert BA._hex_to_rgb("ff00ff") == (255, 0, 255)

    def test_og_image(self):
        out = BA.generate_og_image("Cloudless", tagline="Automate")
        img = Image.open(io.BytesIO(out))
        assert img.size == (1200, 630)
        # accent bar at top
        assert img.convert("RGB").getpixel((600, 3)) == (0, 255, 245)

    def test_og_image_no_tagline(self):
        img = Image.open(io.BytesIO(BA.generate_og_image("C")))
        assert img.size == (1200, 630)

    def test_social_banner_platforms(self):
        for platform, size in [("linkedin", (1584, 396)),
                               ("twitter", (1500, 500)),
                               ("facebook", (820, 312)),
                               ("unknown", (1500, 500))]:
            img = Image.open(io.BytesIO(
                BA.generate_social_banner("B", tagline="t",
                                          platform=platform)))
            assert img.size == size

    def test_font_fallback(self, monkeypatch):
        from PIL import ImageFont
        real_default = ImageFont.load_default()
        monkeypatch.setattr(BA.ImageFont, "truetype",
                            lambda *a, **kw: (_ for _ in ()).throw(OSError()))
        monkeypatch.setattr(BA.ImageFont, "load_default",
                            lambda **kw: real_default)
        assert Image.open(io.BytesIO(
            BA.generate_og_image("C", "t"))).size == (1200, 630)
        assert Image.open(io.BytesIO(
            BA.generate_social_banner("C", "t"))).size == (1584, 396)

    def test_build_brand_image_prompt(self):
        # empty everything → base_prompt returned
        assert BA.build_brand_image_prompt({}, None, "base") == "base."
        assert BA.build_brand_image_prompt({}, None, "") == ""
        # full visual + voice
        visual = {
            "image_style": "minimal", "photography_direction": "moody",
            "primary_color": "#000", "accent_color": "#0ff",
            "neutral_colors": ["#111", "#222"],
        }
        voice = {"tone_dimensions": {
            "formality": 5, "playfulness": 1, "authority": 4,
            "friendliness": 3, "technical": 5}}
        out = BA.build_brand_image_prompt(visual, voice, "draw a cat")
        assert "draw a cat" in out
        assert "Style: minimal" in out
        assert "Photography: moody" in out
        assert "#000" in out and "#111" in out
        assert "casual" in out and "serious" in out
        assert "authoritative" in out and "technical" in out
        # friendliness=3 → excluded
        assert "friendly" not in out and "distant" not in out
        # empty tone → no Mood
        out2 = BA.build_brand_image_prompt(visual, {"tone_dimensions": {}}, "x")
        assert "Mood" not in out2


class TestPlatformDrivers:
    def test_registry_and_names(self):
        assert PBASE.get_driver("twitter").platform == "twitter"
        assert PBASE.get_driver("linkedin").platform == "linkedin"
        assert PBASE.get_driver("facebook").platform == "facebook"
        assert PBASE.get_driver("instagram").platform == "instagram"
        assert PBASE.get_driver("threads").platform == "threads"
        assert PBASE.get_driver("tiktok").platform == "tiktok"
        assert PBASE.get_driver("mastodon") is None
        # runtime protocol check
        assert isinstance(PBASE.get_driver("twitter"), PBASE.PlatformDriver)

    def _acc(self):
        return SimpleNamespace(access_token_enc=b"enc", account_id="pg",
                               id=uuid.uuid4())

    @pytest.mark.asyncio
    async def test_base_publish(self, monkeypatch):
        import app.services.publishing as pub
        monkeypatch.setattr(pub, "publish_to_platform", AsyncMock(return_value="result"))
        acc = self._acc()
        out = await PBASE._BaseDriver().publish(acc, "post", "db")
        assert out == "result"
        pub.publish_to_platform.assert_awaited_once_with(acc, "post", "db")

    @pytest.mark.asyncio
    async def test_base_defaults(self):
        d = PBASE._BaseDriver()
        assert await d.delete(self._acc(), "x") is False
        assert await d.get_follower_count(self._acc()) == 0

    @pytest.mark.asyncio
    async def test_twitter_delete_and_followers(self, monkeypatch):
        import app.api.analytics as an
        import app.core.security as sec
        import app.services.twitter_api as tw
        monkeypatch.setattr(sec, "decrypt_token", lambda b: "tok")
        client = SimpleNamespace(delete_tweet=AsyncMock())
        monkeypatch.setattr(tw, "TwitterAPIClient", lambda **kw: client)
        assert await PBASE.TwitterDriver().delete(self._acc(), "tw1") is True
        client.delete_tweet.assert_awaited_once_with("tw1")
        monkeypatch.setattr(an, "_twitter_follower_count",
                            AsyncMock(return_value=42))
        assert await PBASE.TwitterDriver().get_follower_count(
            self._acc()) == 42

    @pytest.mark.asyncio
    async def test_linkedin_delete_and_followers(self, monkeypatch):
        import app.api.analytics as an
        import app.core.security as sec
        import app.services.linkedin_api as li
        monkeypatch.setattr(sec, "decrypt_token", lambda b: "tok")
        client = SimpleNamespace(delete_post=AsyncMock())
        monkeypatch.setattr(li, "LinkedInAPIClient", lambda **kw: client)
        assert await PBASE.LinkedInDriver().delete(self._acc(), "urn:1") is True
        monkeypatch.setattr(an, "_linkedin_follower_count",
                            AsyncMock(return_value=10))
        assert await PBASE.LinkedInDriver().get_follower_count(
            self._acc()) == 10

    @pytest.mark.asyncio
    async def test_facebook_delete_and_followers(self, monkeypatch):
        import app.api.analytics as an
        import app.core.security as sec
        import app.services.facebook_api as fb
        monkeypatch.setattr(sec, "decrypt_token", lambda b: "tok")
        client = SimpleNamespace(delete_post=AsyncMock())
        created = {}
        monkeypatch.setattr(fb, "FacebookAPIClient",
                            lambda **kw: created.update(kw) or client)
        acc = self._acc()
        assert await PBASE.FacebookDriver().delete(acc, "p1") is True
        assert created["page_id"] == "pg"
        monkeypatch.setattr(an, "_facebook_follower_count",
                            AsyncMock(return_value=7))
        assert await PBASE.FacebookDriver().get_follower_count(acc) == 7

    @pytest.mark.asyncio
    async def test_threads_delete_and_followers(self, monkeypatch):
        import app.api.analytics as an
        import app.core.security as sec
        import app.services.threads_api as th
        monkeypatch.setattr(sec, "decrypt_token", lambda b: "tok")
        client = SimpleNamespace(delete_post=AsyncMock())
        created = {}
        monkeypatch.setattr(th, "ThreadsAPIClient",
                            lambda **kw: created.update(kw) or client)
        acc = self._acc()
        assert await PBASE.ThreadsDriver().delete(acc, "t1") is True
        assert created["user_id"] == "pg"
        monkeypatch.setattr(an, "_threads_follower_count",
                            AsyncMock(return_value=3))
        assert await PBASE.ThreadsDriver().get_follower_count(acc) == 3

    @pytest.mark.asyncio
    async def test_ig_tiktok_followers(self, monkeypatch):
        import app.api.analytics as an
        monkeypatch.setattr(an, "_instagram_follower_count",
                            AsyncMock(return_value=11))
        monkeypatch.setattr(an, "_tiktok_follower_count",
                            AsyncMock(return_value=22))
        acc = self._acc()
        assert await PBASE.InstagramDriver().get_follower_count(acc) == 11
        assert await PBASE.TikTokDriver().get_follower_count(acc) == 22


class TestXBogus:
    def test_rc4_roundtrip(self):
        key = b"key"
        data = b"hello world"
        enc = XB._rc4(key, data)
        assert enc != data
        assert XB._rc4(key, enc) == data

    def test_md5_helpers(self):
        assert XB._md5_hex("abc") == XB._md5_hex(b"abc")
        assert len(XB._double_md5("x")) == 16
        assert len(XB._md5_user_agent("UA")) == 16
        assert XB._hex_to_bytes("ff00") == b"\xff\x00"

    def test_custom_b64(self):
        out = XB._custom_base64_encode(b"abc")
        # uses custom alphabet — differs from standard b64 when bytes map
        assert out == XB._custom_base64_encode(b"abc")

    def test_generate_deterministic(self):
        a = XB.generate_xbogus("a=1&b=2", "UA/1.0", timestamp=1700000000)
        b = XB.generate_xbogus("a=1&b=2", "UA/1.0", timestamp=1700000000)
        assert a == b
        # different timestamp → different signature
        c = XB.generate_xbogus("a=1&b=2", "UA/1.0", timestamp=1700000001)
        assert a != c
        # body affects output
        d = XB.generate_xbogus("a=1&b=2", "UA/1.0", body="x",
                               timestamp=1700000000)
        assert a != d
        # default timestamp → still produces string
        assert isinstance(XB.generate_xbogus("q=1", "UA"), str)

    def test_sign_url(self):
        url = XB.sign_url("https://api.tiktok.com/x?a=1", "UA/1.0")
        assert url.startswith("https://api.tiktok.com/x?a=1&X-Bogus=")
        url2 = XB.sign_url("https://api.tiktok.com/x", "UA/1.0")
        assert "?X-Bogus=" in url2
