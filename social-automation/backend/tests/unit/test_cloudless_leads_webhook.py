from types import SimpleNamespace
from unittest.mock import patch

import pytest
import uuid

from app.models.lead import Lead, LeadCompanySize, LeadInterest, LeadSource
from app.services import leads as leads_service


class _FakeResponse:
    def __init__(self, status_code: int, body):
        self.status_code = status_code
        self._body = body

    @property
    def text(self) -> str:
        return str(self._body)

    def json(self):
        return self._body


class _FakeAsyncClient:
    def __init__(self, response: _FakeResponse):
        self._response = response
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, headers=None, json=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        return self._response


@pytest.mark.asyncio
async def test_cloudless_leads_webhook_posts_when_url_set():
    settings = SimpleNamespace(
        CLOUDLESS_LEADS_WEBHOOK_URL="https://cloudless.gr/api/webhooks/socialauto-leads",
        CLOUDLESS_LEADS_WEBHOOK_SECRET="test-secret",
    )
    fake = _FakeAsyncClient(_FakeResponse(200, {"ok": True}))

    lead = Lead(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        source=LeadSource.instagram_dm,
        name="Jane Doe",
        email="jane@example.com",
        company_size=LeadCompanySize.s_6_20,
        interest=LeadInterest.audit,
        notes="Interested in a free audit",
        thread_id="ig-thread-123",
        social_account_id=None,
        meta_data={"utm": {"source": "ig"}},
    )

    payload = {"event": "lead.upserted", "operation": "created", "lead": leads_service._lead_payload(lead)}

    with patch.object(leads_service, "get_settings", return_value=settings), patch.object(
        leads_service.httpx, "AsyncClient", return_value=fake
    ):
        await leads_service._post_cloudless_leads_webhook(payload)

    assert len(fake.calls) == 1
    assert fake.calls[0]["url"] == settings.CLOUDLESS_LEADS_WEBHOOK_URL
    assert fake.calls[0]["headers"]["X-SocialAuto-Webhook-Secret"] == settings.CLOUDLESS_LEADS_WEBHOOK_SECRET
    assert fake.calls[0]["json"]["event"] == "lead.upserted"
    assert fake.calls[0]["json"]["operation"] == "created"
    assert fake.calls[0]["json"]["lead"]["email"] == "jane@example.com"


@pytest.mark.asyncio
async def test_cloudless_leads_webhook_skips_when_url_unset():
    settings = SimpleNamespace(
        CLOUDLESS_LEADS_WEBHOOK_URL="",
        CLOUDLESS_LEADS_WEBHOOK_SECRET="test-secret",
    )

    def _should_not_be_called(*args, **kwargs):
        raise AssertionError("httpx.AsyncClient should not be constructed when URL is unset")

    with patch.object(leads_service, "get_settings", return_value=settings), patch.object(
        leads_service.httpx, "AsyncClient", side_effect=_should_not_be_called
    ):
        await leads_service._post_cloudless_leads_webhook({"event": "lead.upserted", "operation": "created", "lead": {}})

