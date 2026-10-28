import uuid

import pytest

from app.models.lead import LeadInterest, LeadSource


def test_synthetic_email_for_messenger_psid_is_deterministic():
    from app.services.leads import synthetic_email_for_messenger_psid

    a = synthetic_email_for_messenger_psid("123")
    b = synthetic_email_for_messenger_psid("123")
    c = synthetic_email_for_messenger_psid("456")
    assert a == b
    assert a != c
    assert a.endswith("@messenger.local")


@pytest.mark.asyncio
async def test_upsert_lead_prefers_thread_id_and_overwrites_interest():
    from app.models.lead import Lead
    from app.services.leads import upsert_lead

    team_id = uuid.uuid4()
    existing = Lead(
        id=uuid.uuid4(),
        team_id=team_id,
        source=LeadSource.facebook_messenger,
        social_account_id=None,
        thread_id="psid-1",
        name="Messenger lead 000001",
        email="psid.deadbeef@messenger.local",
        company_size=None,
        interest=LeadInterest.cloud,
        notes=None,
        meta_data={},
    )

    class _Result:
        def __init__(self, lead):
            self._lead = lead

        def scalars(self):
            return self

        def first(self):
            return self._lead

    class _Db:
        def __init__(self):
            self.added = []
            self.commits = 0
            self.last_execute = None

        async def execute(self, stmt):
            self.last_execute = stmt
            return _Result(existing)

        def add(self, obj):
            self.added.append(obj)

        async def commit(self):
            self.commits += 1

    db = _Db()

    lead = await upsert_lead(
        db,  # type: ignore[arg-type]
        team_id=team_id,
        source=LeadSource.facebook_messenger,
        name="Alice",
        email="psid.deadbeef@messenger.local",
        thread_id="psid-1",
        interest=LeadInterest.audit,
        overwrite_interest=True,
        meta_data={"x": 1},
    )

    assert lead is existing
    assert existing.name == "Alice"
    assert existing.interest == LeadInterest.audit
    assert existing.meta_data.get("x") == 1
    assert db.commits == 1


@pytest.mark.asyncio
async def test_upsert_lead_attaches_real_email_over_synthetic_on_same_thread():
    from app.models.lead import Lead
    from app.services.leads import upsert_lead

    team_id = uuid.uuid4()
    existing = Lead(
        id=uuid.uuid4(),
        team_id=team_id,
        source=LeadSource.facebook_messenger,
        social_account_id=None,
        thread_id="psid-2",
        name="Messenger lead 000002",
        email="psid.aaaaaaaaaaaaaaaaaaaaaaaa@messenger.local",
        company_size=None,
        interest=None,
        notes=None,
        meta_data={},
    )

    class _Result:
        def __init__(self, lead):
            self._lead = lead

        def scalars(self):
            return self

        def first(self):
            return self._lead

    class _Db:
        async def execute(self, stmt):
            return _Result(existing)

        def add(self, obj):
            raise AssertionError("should not add")

        async def commit(self):
            return None

    db = _Db()

    lead = await upsert_lead(
        db,  # type: ignore[arg-type]
        team_id=team_id,
        source=LeadSource.facebook_messenger,
        name="Bob",
        email="bob@example.com",
        thread_id="psid-2",
        dedupe_on_thread_id=True,
    )

    assert lead is existing
    assert existing.email == "bob@example.com"
    assert existing.name == "Bob"


@pytest.mark.asyncio
async def test_upsert_icebreaker_lead_maps_payload_and_calls_upsert(monkeypatch):
    from app.api import messenger as messenger_api
    from app.models.social_account import SocialAccount

    team_id = uuid.uuid4()
    acct = SocialAccount(
        id=uuid.uuid4(),
        team_id=team_id,
        platform="facebook",
        account_id="123",
        username=None,
        display_name="Cloudless",
        avatar_url=None,
        account_type="page",
        is_business=True,
        parent_account_id=None,
        access_token_enc=b"x",
        refresh_token_enc=None,
        token_expires_at=None,
        scopes=[],
        status="active",
        meta_data={"page_token": "EAAT-test-token"},
    )

    class _Result:
        def scalar_one_or_none(self):
            return acct

    class _Db:
        async def execute(self, stmt):
            return _Result()

    called = {}

    async def _fake_upsert_lead(*args, **kwargs):
        called.update(kwargs)
        return None

    monkeypatch.setattr(messenger_api, "_get_messenger_client", lambda _a: None)
    # _upsert_icebreaker_lead imports these from app.services.leads at runtime.
    monkeypatch.setattr("app.services.leads.upsert_lead", _fake_upsert_lead)
    monkeypatch.setattr("app.services.leads.synthetic_email_for_messenger_psid", lambda psid: "psid.x@messenger.local")

    await messenger_api._upsert_icebreaker_lead(  # type: ignore[attr-defined]
        _Db(),  # type: ignore[arg-type]
        page_id="123",
        sender_psid="999",
        payload="LEAD_GROWTH",
        message_mid="m",
        timestamp_ms=1,
    )

    assert called["team_id"] == team_id
    assert called["source"] == LeadSource.facebook_messenger
    assert called["thread_id"] == "999"
    assert called["interest"] == LeadInterest.growth

