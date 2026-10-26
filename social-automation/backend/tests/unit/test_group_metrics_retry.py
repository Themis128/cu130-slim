"""Group targeting, agency metrics, publish-retry — the session's new surfaces."""
import asyncio


class TestLinkedInGroupTargeting:
    def test_post_payload_container(self):
        from app.services.linkedin_api import LinkedInAPIClient
        c = LinkedInAPIClient(access_token="t")
        p = c._post_payload("urn:li:person:x", "text",
                            container_urn="urn:li:group:12345")
        assert p["container"] == "urn:li:group:12345"

    def test_no_container_when_unset(self):
        from app.services.linkedin_api import LinkedInAPIClient
        c = LinkedInAPIClient(access_token="t")
        p = c._post_payload("urn:li:person:x", "text")
        assert "container" not in p

    def test_create_post_group_param(self):
        # create_post accepts container_urn kwarg
        import inspect

        from app.services.linkedin_api import LinkedInAPIClient
        sig = inspect.signature(LinkedInAPIClient.create_post)
        assert "container_urn" in sig.parameters

    def test_get_member_groups_fails_open(self):
        # pre-CMAPI-approval the method returns [] not a crash
        from app.services.linkedin_api import LinkedInAPIClient
        c = LinkedInAPIClient(access_token="t")
        out = asyncio.run(c.get_member_groups())
        assert isinstance(out, list)


class TestAgencyMetrics:
    def test_metrics_route_registered(self):
        from app.api.agency import router
        paths = [r.path for r in router.routes]
        assert any("metrics" in p for p in paths)


class TestPublishRetry:
    def test_retry_sweep_skips_non_retryable(self):
        # non-retryable error strings stay failed
        from app.worker.tasks.publish_retry import _NON_RETRYABLE
        for err in ("does not accept", "Unsupported platform",
                    "account not connected", "Token decrypt failed"):
            assert any(t in err for t in _NON_RETRYABLE)
