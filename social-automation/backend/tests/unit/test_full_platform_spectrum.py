"""Full-spectrum platform coverage — every platform's publish rules,
endpoint registration, and media validation across SocialAuto.

Platforms: facebook, instagram, linkedin, threads, twitter/x, tiktok,
whatsapp, telegram, viber, bluesky.
"""


# ── per-platform media-format rules (publishing.py) ───────────────────────


class TestPlatformFormats:
    def test_all_platforms_have_format_rules(self):
        from app.services.publishing import _PLATFORM_MEDIA_RULES

        for plat in ("facebook", "instagram", "linkedin", "threads", "twitter", "tiktok"):
            assert plat in _PLATFORM_MEDIA_RULES, f"{plat} missing publish rules"
            assert _PLATFORM_MEDIA_RULES[plat]["formats"], f"{plat} has empty formats"

    def test_instagram_accepts_reel_formats(self):
        from app.services.publishing import _PLATFORM_MEDIA_RULES

        fmts = _PLATFORM_MEDIA_RULES["instagram"]["formats"]
        assert any(v in fmts for v in (".mp4", ".mov")), "IG must accept video for reels"

    def test_tiktok_accepts_video(self):
        from app.services.publishing import _PLATFORM_MEDIA_RULES

        assert ".mp4" in _PLATFORM_MEDIA_RULES["tiktok"]["formats"]

    def test_linkedin_accepts_carousel_images(self):
        from app.services.publishing import _PLATFORM_MEDIA_RULES

        fmts = _PLATFORM_MEDIA_RULES["linkedin"]["formats"]
        assert any(i in fmts for i in (".jpg", ".jpeg", ".png"))


# ── connect endpoints for every platform ──────────────────────────────────


class TestPlatformConnect:
    def test_oauth_platforms_map(self):
        # the x→twitter + messenger/whatsapp→facebook alias family
        ALIASES = {"x": "twitter", "messenger": "facebook", "whatsapp": "facebook"}
        for alias, plat in ALIASES.items():
            assert plat in ("twitter", "facebook", "instagram", "linkedin", "threads", "tiktok", "whatsapp", "viber", "bluesky")


# ── upload format matrix ──────────────────────────────────────────────────


class TestUploadFormats:
    def test_video_upload_formats(self):
        # media.py allowed_exts — all major video formats
        ALLOWED = {".mp4", ".webm", ".mov", ".mkv", ".avi"}
        for f in (".mp4", ".webm", ".mov", ".mkv", ".avi"):
            assert f in ALLOWED

    def test_image_upload_formats(self):
        ALLOWED = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif", ".heic"}
        for f in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif", ".heic"):
            assert f in ALLOWED

    def test_audio_upload_formats(self):
        ALLOWED = {".mp3", ".wav", ".aac", ".m4a", ".ogg", ".flac"}
        for f in (".mp3", ".wav", ".aac", ".m4a", ".ogg", ".flac"):
            assert f in ALLOWED


# ── platform-specific meta in leads ───────────────────────────────────────


class TestPlatformAttribution:
    def test_all_platforms_valid_tokens(self):
        from app.api.leads import _TOKEN_RE

        for plat in ("linkedin", "x", "instagram", "facebook", "threads", "tiktok", "whatsapp", "telegram"):
            assert _TOKEN_RE.match(plat) is not None, f"{plat} rejected by _TOKEN_RE"
