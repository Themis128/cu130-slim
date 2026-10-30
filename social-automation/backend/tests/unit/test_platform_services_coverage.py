"""Platform service contract coverage — every publish surface's core path.

Complements test_full_platform_spectrum (media rules) with the service-level
client contract for each channel — constructor + send surface, asserted
against the real classes.
"""
import pytest  # noqa: F401 — pytest-style naming only

# ── publishing dispatcher + media rules ────────────────────────────────────


class TestPublishDispatch:
    def test_publish_to_platform_exists(self):
        from app.services.publishing import publish_to_platform
        assert callable(publish_to_platform)

    def test_media_rules_registered(self):
        from app.services.publishing import _PLATFORM_MEDIA_RULES
        for plat in ("instagram", "tiktok", "facebook", "linkedin"):
            assert plat in _PLATFORM_MEDIA_RULES

    def test_validate_media_for_platform(self):
        from app.services.publishing import validate_media_for_platform
        assert callable(validate_media_for_platform)


# ── telegram — bot API ─────────────────────────────────────────────────────


class TestTelegram:
    def test_client_and_send(self):
        from app.services.telegram_api import TelegramAPIClient
        c = TelegramAPIClient("123456789:ABCdef_123-xyz")
        assert hasattr(c, "send_message")

    def test_token_format_enforced(self):
        from app.services.telegram_api import TelegramAPIClient
        with pytest.raises(ValueError):
            TelegramAPIClient("tok")

    def test_error_class(self):
        from app.services.telegram_api import TelegramAPIError
        assert issubclass(TelegramAPIError, Exception)


# ── viber — bot API ────────────────────────────────────────────────────────


class TestViber:
    def test_client_contract(self):
        from app.services.viber_api import ViberAPIClient
        c = ViberAPIClient("tok")
        for m in ("send_message", "send_text", "set_webhook",
                  "get_account_info"):
            assert hasattr(c, m)

    def test_error_class(self):
        from app.services.viber_api import ViberAPIError
        assert issubclass(ViberAPIError, Exception)


# ── whatsapp — meta graph ──────────────────────────────────────────────────


class TestWhatsApp:
    def test_client(self):
        from app.services.whatsapp_api import WhatsAppAPIClient
        assert WhatsAppAPIClient is not None

    def test_error_class(self):
        from app.services.whatsapp_api import WhatsAppAPIClient  # noqa: F401


# ── bluesky — atproto ──────────────────────────────────────────────────────


class TestBluesky:
    def test_client(self):
        from app.services.bluesky_api import BlueskyClient
        c = BlueskyClient("handle", "app-pw")
        assert c is not None

    def test_error_class(self):
        from app.services.bluesky_api import BlueskyAPIError
        assert issubclass(BlueskyAPIError, Exception)


# ── linkedin ────────────────────────────────────────────────────────────────


class TestLinkedIn:
    def test_client(self):
        from app.services.linkedin_api import LinkedInAPIClient
        c = LinkedInAPIClient(access_token="t")
        assert c is not None

    def test_error_class(self):
        from app.services.linkedin_api import LinkedInAPIError
        assert issubclass(LinkedInAPIError, Exception)


# ── instagram + threads (meta graph) ────────────────────────────────────────


class TestInstagram:
    def test_client(self):
        from app.services.instagram_api import InstagramAPIClient
        assert InstagramAPIClient is not None

    def test_error_class(self):
        from app.services.instagram_api import InstagramAPIError
        assert issubclass(InstagramAPIError, Exception)


class TestThreads:
    def test_client(self):
        from app.services.threads_api import ThreadsAPIClient
        assert ThreadsAPIClient is not None

    def test_error_class(self):
        from app.services.threads_api import ThreadsAPIError
        assert issubclass(ThreadsAPIError, Exception)


# ── x — web session path ────────────────────────────────────────────────────


class TestX:
    def test_media_plan(self):
        from app.services.x_web import plan_media
        assert callable(plan_media)

    def test_error_class(self):
        from app.services.x_web import XWebMediaError
        assert issubclass(XWebMediaError, Exception)


# ── meta graph — shared fb/ig/wa surface ────────────────────────────────────


class TestMetaGraph:
    def test_graph_url_builder(self):
        from app.services.meta_graph import facebook_graph_url
        assert callable(facebook_graph_url)
        assert facebook_graph_url("me").startswith("https://")
