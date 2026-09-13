import os
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models.lead import Lead, LeadInterest, LeadSource
from app.models.social_account import SocialAccount
from app.models.user import Team, User


@pytest.mark.asyncio
async def test_messenger_icebreaker_postback_creates_lead(client, db):
    owner = User(email="owner@example.com", password_hash="x")
    db.add(owner)
    await db.commit()
    await db.refresh(owner)

    team = Team(name="Test Team", owner_id=owner.id)
    db.add(team)
    await db.commit()
    await db.refresh(team)

    page_id = "123"
    sender_psid = "999000111222"
    acct = SocialAccount(
        team_id=team.id,
        platform="facebook",
        account_id=page_id,
        username=None,
        display_name="Cloudless",
        avatar_url=None,
        account_type="page",
        is_business=True,
        parent_account_id=None,
        access_token_enc=b"token",
        refresh_token_enc=None,
        token_expires_at=None,
        scopes=[],
        status="active",
        meta_data={"page_token": "EAAT-test-token"},
    )
    db.add(acct)
    await db.commit()

    body = {
        "object": "page",
        "entry": [
            {
                "id": page_id,
                "messaging": [
                    {
                        "sender": {"id": sender_psid},
                        "recipient": {"id": page_id},
                        "timestamp": 1690000000000,
                        "postback": {"payload": "LEAD_CLOUD"},
                    }
                ],
            }
        ],
    }

    resp = await client.post("/api/v1/messenger/webhook", json=body)
    assert resp.status_code == 200

    lead = (
        await db.execute(
            select(Lead).where(
                Lead.team_id == team.id,
                Lead.source == LeadSource.facebook_messenger,
                Lead.thread_id == sender_psid,
            )
        )
    ).scalars().first()
    assert lead is not None
    assert lead.interest == LeadInterest.cloud


@pytest.mark.asyncio
async def test_messenger_icebreaker_postback_updates_existing_lead_interest(client, db):
    owner = User(email="owner2@example.com", password_hash="x")
    db.add(owner)
    await db.commit()
    await db.refresh(owner)

    team = Team(name="Test Team 2", owner_id=owner.id)
    db.add(team)
    await db.commit()
    await db.refresh(team)

    page_id = "123"
    sender_psid = "999000111222"
    acct = SocialAccount(
        team_id=team.id,
        platform="facebook",
        account_id=page_id,
        username=None,
        display_name="Cloudless",
        avatar_url=None,
        account_type="page",
        is_business=True,
        parent_account_id=None,
        access_token_enc=b"token",
        refresh_token_enc=None,
        token_expires_at=None,
        scopes=[],
        status="active",
        meta_data={"page_token": "EAAT-test-token"},
    )
    db.add(acct)
    await db.commit()

    def _body(payload: str):
        return {
            "object": "page",
            "entry": [
                {
                    "id": page_id,
                    "messaging": [
                        {
                            "sender": {"id": sender_psid},
                            "recipient": {"id": page_id},
                            "timestamp": 1690000000000,
                            "postback": {"payload": payload},
                        }
                    ],
                }
            ],
        }

    resp1 = await client.post("/api/v1/messenger/webhook", json=_body("LEAD_GROWTH"))
    assert resp1.status_code == 200
    resp2 = await client.post("/api/v1/messenger/webhook", json=_body("LEAD_AUDIT"))
    assert resp2.status_code == 200

    leads = (
        await db.execute(
            select(Lead).where(
                Lead.team_id == team.id,
                Lead.source == LeadSource.facebook_messenger,
                Lead.thread_id == sender_psid,
            )
        )
    ).scalars().all()
    assert len(leads) == 1
    assert leads[0].interest == LeadInterest.audit


@pytest.mark.asyncio
async def test_sidecar_dispatch_includes_postback_payload(client, db):
    owner = User(email="owner3@example.com", password_hash="x")
    db.add(owner)
    await db.commit()
    await db.refresh(owner)

    team = Team(name="Test Team 3", owner_id=owner.id)
    db.add(team)
    await db.commit()
    await db.refresh(team)

    page_id = "123"
    sender_psid = "999000111222"
    acct = SocialAccount(
        team_id=team.id,
        platform="facebook",
        account_id=page_id,
        username=None,
        display_name="Cloudless",
        avatar_url=None,
        account_type="page",
        is_business=True,
        parent_account_id=None,
        access_token_enc=b"token",
        refresh_token_enc=None,
        token_expires_at=None,
        scopes=[],
        status="active",
        meta_data={"page_token": "EAAT-test-token"},
    )
    db.add(acct)
    await db.commit()

    body = {
        "object": "page",
        "entry": [
            {
                "id": page_id,
                "messaging": [
                    {
                        "sender": {"id": sender_psid},
                        "recipient": {"id": page_id},
                        "timestamp": 1690000000000,
                        "postback": {"payload": "LEAD_CLOUD"},
                    }
                ],
            }
        ],
    }

    calls: list[dict] = []

    class _FakeResp:
        status_code = 200

        def json(self):
            return {"status": "ok"}

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None, **kw):
            calls.append({"url": url, "json": json})
            return _FakeResp()

    with patch.dict(os.environ, {"MESSENGER_SIDECAR_URL": "http://messenger-sidecar:9230"}):
        with patch("httpx.AsyncClient", _FakeAsyncClient):
            resp = await client.post("/api/v1/messenger/webhook", json=body)
            assert resp.status_code == 200

    assert calls, "Expected webhook to dispatch to sidecar"
    assert calls[0]["json"]["postback_payload"] == "LEAD_CLOUD"

