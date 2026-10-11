"""Coverage for app/services/leads.py."""
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.leads as L
from app.models.lead import Lead, LeadCompanySize, LeadInterest, LeadSource


def _lead(**kw):
    d = dict(id=uuid.uuid4(), team_id=uuid.uuid4(),
             source=LeadSource.website, name="N", email="a@b.co",
             interest=None, company_size=None, notes=None,
             thread_id=None, social_account_id=None, meta_data={},
             created_at=datetime.now(UTC), updated_at=datetime.now(UTC))
    d.update(kw)
    return SimpleNamespace(**d)


class _Res:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return SimpleNamespace(first=lambda: self._items[0]
                               if self._items else None)

    def scalar_one_or_none(self):
        return self._items[0] if self._items else None


class _DB:
    def __init__(self, results=None):
        self._q = list(results or [])
        self.added = []
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def execute(self, stmt, *a):
        return self._q.pop(0) if self._q else _Res([])

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


class TestPureHelpers:
    def test_norm_and_synthetic(self):
        assert L._norm_email("  A@B.CO ") == "a@b.co"
        assert L._is_synthetic_email("x@messenger.local")
        assert L._is_synthetic_email("y@instagram.local")
        assert not L._is_synthetic_email("a@b.co")
        assert not L._is_synthetic_email("noatsign")
        e1 = L.synthetic_email_for_messenger_psid("123")
        e2 = L.synthetic_email_for_messenger_psid("123")
        assert e1 == e2 and e1.endswith("@messenger.local")
        e3 = L.synthetic_email_for_messenger_psid("")
        assert e3.endswith("@messenger.local")

    def test_is_valid_email(self):
        assert L.is_valid_email("a@b.co")
        for bad in ["", "  ", "a b@c.co", "a@b", "@b.co", "a@",
                    "a@@b.co", "a@.co", "a@b.", "a@b..co", "x" * 321 + "@b.co",
                    None]:
            assert not L.is_valid_email(bad), bad

    def test_coerce(self):
        assert L.coerce_company_size(None) is None
        assert L.coerce_company_size("nope") is None
        assert L.coerce_company_size("2-5") == LeadCompanySize.s_2_5
        assert L.coerce_interest(None) is None
        assert L.coerce_interest("NOPE") is None
        assert L.coerce_interest(" Cloud ") == LeadInterest.cloud

    def test_lead_payload(self):
        lead = _lead(interest=LeadInterest.growth,
                     company_size=LeadCompanySize.solo,
                     social_account_id=uuid.uuid4())
        p = L._lead_payload(lead)
        assert p["source"] == "website"
        assert p["interest"] == "growth"
        assert p["company_size"] == "solo"
        assert p["social_account_id"] is not None
        # missing timestamps tolerated
        lead2 = _lead()
        del lead2.created_at
        lead2.created_at = None
        p2 = L._lead_payload(lead2)
        assert p2["created_at"] is None

    @pytest.mark.asyncio
    async def test_fire_and_forget(self):
        ran = []

        async def ok():
            ran.append(1)

        async def bad():
            raise RuntimeError("boom")

        L._fire_and_forget(ok(), label="t1")
        L._fire_and_forget(bad(), label="t2")
        import asyncio
        await asyncio.sleep(0.05)
        assert ran == [1]

        # no running loop → create_task raises RuntimeError, coro closed
        import unittest.mock as um
        with um.patch.object(asyncio, "create_task",
                             side_effect=RuntimeError()):
            c = ok()
            L._fire_and_forget(c, label="t3")
            c.close()


class TestWebhook:
    @pytest.mark.asyncio
    async def test_no_url_returns(self, monkeypatch):
        monkeypatch.setattr(L, "get_settings",
                            lambda: SimpleNamespace(
                                CLOUDLESS_LEADS_WEBHOOK_URL=""))
        await L._post_cloudless_leads_webhook({})  # early return

    @pytest.mark.asyncio
    async def test_posts_with_secret_and_records(self, monkeypatch):
        monkeypatch.setattr(L, "get_settings",
                            lambda: SimpleNamespace(
                                CLOUDLESS_LEADS_WEBHOOK_URL="http://wh",
                                CLOUDLESS_LEADS_WEBHOOK_SECRET="s3"))
        posts = []

        class _C:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            async def post(self, url, **kw):
                posts.append((url, kw))
                return SimpleNamespace(
                    status_code=200,
                    json=lambda: {"results": [
                        {"ok": True, "espocrm_lead_id": "espo-9"}]})
        monkeypatch.setattr(L.httpx, "AsyncClient", lambda **kw: _C())
        rec = AsyncMock()
        monkeypatch.setattr(L, "_record_espo_lead_id", rec)
        lead_id = str(uuid.uuid4())
        await L._post_cloudless_leads_webhook({"lead": {"id": lead_id}})
        assert posts[0][0] == "http://wh"
        assert posts[0][1]["headers"]["X-SocialAuto-Webhook-Secret"] == "s3"
        rec.assert_awaited_once_with(lead_id, "espo-9")

    @pytest.mark.asyncio
    async def test_http_error_and_bad_resp(self, monkeypatch):
        monkeypatch.setattr(L, "get_settings",
                            lambda: SimpleNamespace(
                                CLOUDLESS_LEADS_WEBHOOK_URL="http://wh",
                                CLOUDLESS_LEADS_WEBHOOK_SECRET=""))
        rec = AsyncMock()
        monkeypatch.setattr(L, "_record_espo_lead_id", rec)

        class _C:
            def __init__(self, resp):
                self._r = resp

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            async def post(self, url, **kw):
                return self._r

        # 400 → no record
        monkeypatch.setattr(L.httpx, "AsyncClient",
                            lambda **kw: _C(SimpleNamespace(
                                status_code=400, text="bad")))
        await L._post_cloudless_leads_webhook({"lead": {"id": "x"}})
        rec.assert_not_awaited()
        # json parse fail
        monkeypatch.setattr(L.httpx, "AsyncClient",
                            lambda **kw: _C(SimpleNamespace(
                                status_code=200,
                                json=lambda: (_ for _ in ()).throw(
                                    ValueError()))))
        await L._post_cloudless_leads_webhook({"lead": {"id": "x"}})
        # results without ok
        monkeypatch.setattr(L.httpx, "AsyncClient",
                            lambda **kw: _C(SimpleNamespace(
                                status_code=200,
                                json=lambda: {"results": [
                                    {"ok": False}, "str"]})))
        await L._post_cloudless_leads_webhook({"lead": {"id": "x"}})
        rec.assert_not_awaited()
        # httpx exception swallowed
        class _Err:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            async def post(self, *a, **kw):
                raise TimeoutError()
        monkeypatch.setattr(L.httpx, "AsyncClient", lambda **kw: _Err())
        await L._post_cloudless_leads_webhook({})


class TestRecordEspoId:
    @pytest.mark.asyncio
    async def test_bad_uuid(self):
        await L._record_espo_lead_id("not-uuid", "e1")

    @pytest.mark.asyncio
    async def test_updates_metadata(self, monkeypatch):
        import app.db.session as dbs
        lead = _lead(meta_data={"a": 1})
        db = _DB([_Res([lead])])
        def maker():
            return db
        monkeypatch.setattr(dbs, "async_session_maker", maker)
        await L._record_espo_lead_id(str(uuid.uuid4()), "espo-1")
        assert lead.meta_data["espocrm_lead_id"] == "espo-1"
        assert db.commits == 1
        # same id → no commit
        lead2 = _lead(meta_data={"espocrm_lead_id": "espo-1"})
        db2 = _DB([_Res([lead2])])
        monkeypatch.setattr(dbs, "async_session_maker", lambda: db2)
        await L._record_espo_lead_id(str(uuid.uuid4()), "espo-1")
        assert db2.commits == 0
        # missing lead → no commit
        db3 = _DB([_Res([])])
        monkeypatch.setattr(dbs, "async_session_maker", lambda: db3)
        await L._record_espo_lead_id(str(uuid.uuid4()), "espo-2")
        assert db3.commits == 0
        # db error swallowed
        bad = SimpleNamespace()
        bad.execute = AsyncMock(side_effect=RuntimeError())
        monkeypatch.setattr(dbs, "async_session_maker", lambda: bad)
        await L._record_espo_lead_id(str(uuid.uuid4()), "e")


class TestUpsertLead:
    def _kwargs(self, **kw):
        d = dict(team_id=uuid.uuid4(), source=LeadSource.facebook_messenger,
                 name="Ann", email="ann@x.co", thread_id="t1")
        d.update(kw)
        return d

    @pytest.mark.asyncio
    async def test_validation(self):
        db = _DB()
        with pytest.raises(ValueError, match="Invalid email"):
            await L.upsert_lead(db, **self._kwargs(email="bad"))
        with pytest.raises(ValueError, match="Name is required"):
            await L.upsert_lead(db, **self._kwargs(name="  "))

    @pytest.mark.asyncio
    async def test_thread_dedupe_update(self, monkeypatch):
        monkeypatch.setattr(L, "_fire_and_forget", lambda c, *, label: c.close())
        existing = _lead(name="Messenger User", email="p@messenger.local",
                         thread_id="t1")
        db = _DB([_Res([existing])])
        out = await L.upsert_lead(
            db, **self._kwargs(
                email="real@x.co", name="Real Name",
                company_size=LeadCompanySize.solo,
                interest=LeadInterest.cloud, notes="n",
                social_account_id=uuid.uuid4(), meta_data={"k": "v"}))
        assert out is existing
        assert existing.email == "real@x.co"  # synthetic → real
        assert existing.name == "Real Name"
        assert existing.company_size == LeadCompanySize.solo
        assert existing.interest == LeadInterest.cloud
        assert existing.notes == "n"
        assert existing.meta_data["k"] == "v"
        assert db.commits == 1

    @pytest.mark.asyncio
    async def test_thread_dedupe_no_changes(self, monkeypatch):
        monkeypatch.setattr(L, "_fire_and_forget", lambda c, *, label: c.close())
        existing = _lead(name="Ann", email="real@x.co", thread_id="t1",
                         interest=LeadInterest.growth,
                         company_size=LeadCompanySize.solo,
                         notes="n", social_account_id=uuid.uuid4(),
                         meta_data={"k": "v"})
        db = _DB([_Res([existing])])
        out = await L.upsert_lead(
            db, **self._kwargs(
                email="other@x.co",  # real stays (existing not synthetic)
                interest=LeadInterest.audit))
        assert out is existing
        assert existing.email == "real@x.co"
        assert existing.interest == LeadInterest.growth  # no overwrite
        assert db.commits == 0

    @pytest.mark.asyncio
    async def test_thread_dedupe_overwrite_interest(self, monkeypatch):
        monkeypatch.setattr(L, "_fire_and_forget", lambda c, *, label: c.close())
        existing = _lead(thread_id="t1", interest=LeadInterest.cloud)
        db = _DB([_Res([existing])])
        await L.upsert_lead(db, **self._kwargs(
            interest=LeadInterest.audit, overwrite_interest=True))
        assert existing.interest == LeadInterest.audit

    @pytest.mark.asyncio
    async def test_email_dedupe_update(self, monkeypatch):
        monkeypatch.setattr(L, "_fire_and_forget", lambda c, *, label: c.close())
        existing = _lead(name="", email="ann@x.co", thread_id=None)
        db = _DB([_Res([existing])])  # email match
        out = await L.upsert_lead(
            db, **self._kwargs(
                thread_id=None,  # skip thread dedupe
                name="Ann",
                company_size=LeadCompanySize.solo,
                interest=LeadInterest.audit, notes="n",
                social_account_id=uuid.uuid4(), meta_data={"m": 1},
            dedupe_on_thread_id=True))
        assert out is existing
        assert existing.name == "Ann"
        assert db.commits == 1

    @pytest.mark.asyncio
    async def test_email_dedupe_attaches_thread(self, monkeypatch):
        monkeypatch.setattr(L, "_fire_and_forget", lambda c, *, label: c.close())
        existing = _lead(name="Ann", email="ann@x.co", thread_id=None)
        db = _DB([_Res([existing])])
        # thread_id given but dedupe_on_thread_id=False → straight to email
        out = await L.upsert_lead(db, **self._kwargs(
            dedupe_on_thread_id=False, thread_id="new-t"))
        assert out is existing
        assert existing.thread_id == "new-t"

    @pytest.mark.asyncio
    async def test_creates_new(self, monkeypatch):
        monkeypatch.setattr(L, "_fire_and_forget", lambda c, *, label: c.close())
        db = _DB([_Res([]), _Res([])])
        out = await L.upsert_lead(db, **self._kwargs())
        assert db.added and isinstance(db.added[0], Lead)
        assert out.email == "ann@x.co"
        assert db.commits == 1

    @pytest.mark.asyncio
    async def test_create_notify_error_nonfatal(self, monkeypatch):
        def boom(c, *, label):
            if "notifications" in label:
                raise RuntimeError("no loop")
        monkeypatch.setattr(L, "_fire_and_forget", boom)
        monkeypatch.setattr(L, "notify_lead_created",
                            lambda lead: object())
        monkeypatch.setattr(L, "_post_cloudless_leads_webhook",
                            lambda payload: object())
        db = _DB([_Res([]), _Res([])])
        out = await L.upsert_lead(db, **self._kwargs())
        assert isinstance(out, Lead)


class TestCreateLead:
    @pytest.mark.asyncio
    async def test_validation(self):
        with pytest.raises(ValueError, match="Invalid email"):
            await L.create_lead(_DB(), team_id=uuid.uuid4(),
                                source=LeadSource.website, name="n",
                                email="bad")
        with pytest.raises(ValueError, match="Name is required"):
            await L.create_lead(_DB(), team_id=uuid.uuid4(),
                                source=LeadSource.website, name="",
                                email="a@b.co")

    @pytest.mark.asyncio
    async def test_dedupe_enriches(self, monkeypatch):
        monkeypatch.setattr(L, "_fire_and_forget", lambda c, *, label: c.close())
        existing = _lead(name="", email="a@b.co")
        db = _DB([_Res([existing])])
        out = await L.create_lead(
            db, team_id=uuid.uuid4(), source=LeadSource.website,
            name="New", email="a@b.co", notes="note",
            interest=LeadInterest.cloud,
            company_size=LeadCompanySize.solo,
            social_account_id=uuid.uuid4(), thread_id="t9",
            meta_data={"x": 1})
        assert out is existing
        assert existing.name == "New"
        assert existing.thread_id == "t9"
        assert db.commits == 1

    @pytest.mark.asyncio
    async def test_dedupe_no_change_and_new(self, monkeypatch):
        monkeypatch.setattr(L, "_fire_and_forget", lambda c, *, label: c.close())
        existing = _lead(name="Full", email="a@b.co",
                         interest=LeadInterest.cloud,
                         company_size=LeadCompanySize.solo, notes="n",
                         social_account_id=uuid.uuid4(), thread_id="t")
        db = _DB([_Res([existing])])
        await L.create_lead(db, team_id=uuid.uuid4(),
                            source=LeadSource.website, name="X",
                            email="a@b.co")
        assert db.commits == 0
        # new lead
        db2 = _DB([_Res([])])
        out = await L.create_lead(db2, team_id=uuid.uuid4(),
                                  source=LeadSource.website, name="New",
                                  email="new@b.co", dedupe_on_email=False)
        assert isinstance(out, Lead)
        assert db2.added

    @pytest.mark.asyncio
    async def test_notify_dispatch_error_nonfatal(self, monkeypatch):
        # notify dispatch wrapped in try/except — webhook dispatch is not
        def boom(c, *, label):
            if "notifications" in label:
                raise RuntimeError("no loop")
        monkeypatch.setattr(L, "_fire_and_forget", boom)
        monkeypatch.setattr(L, "notify_lead_created",
                            lambda lead: object())
        monkeypatch.setattr(L, "_post_cloudless_leads_webhook",
                            lambda payload: object())
        db = _DB([_Res([])])
        out = await L.create_lead(db, team_id=uuid.uuid4(),
                                  source=LeadSource.website, name="N",
                                  email="n@b.co", dedupe_on_email=False)
        assert isinstance(out, Lead)


class TestNotify:
    def _settings(self, **kw):
        d = dict(SLACK_LEADS_WEBHOOK_URL="", SLACK_WEBHOOK_URL="",
                 SLACK_BOT_TOKEN="", SLACK_ACCESS_TOKEN="",
                 SLACK_LEADS_CHANNEL_ID="", SLACK_CHANNEL_ID="",
                 DIGEST_EMAIL_TO="", LEAD_CREATED_WEBHOOK_URL="")
        d.update(kw)
        return SimpleNamespace(**d)

    @pytest.mark.asyncio
    async def test_no_channels_noop(self, monkeypatch):
        monkeypatch.setattr(L, "get_settings", lambda: self._settings())
        await L.notify_lead_created(_lead())

    @pytest.mark.asyncio
    async def test_slack_email_webhook(self, monkeypatch):
        monkeypatch.setattr(L, "get_settings", lambda: self._settings(
            SLACK_LEADS_WEBHOOK_URL="http://sl",
            DIGEST_EMAIL_TO="ops@x.co",
            LEAD_CREATED_WEBHOOK_URL="http://wh"))
        post_slack = AsyncMock()
        import app.services.slack_notifications as sn
        monkeypatch.setattr(sn, "_post_slack_text", post_slack)
        send_email = AsyncMock()
        import app.services.email_digest as ed
        monkeypatch.setattr(ed, "send_email", send_email)
        posts = []

        class _C:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            async def post(self, url, **kw):
                posts.append(url)
        monkeypatch.setattr(L.httpx, "AsyncClient", lambda **kw: _C())
        lead = _lead(interest=LeadInterest.growth,
                     company_size=LeadCompanySize.solo, notes="hi")
        await L.notify_lead_created(lead)
        post_slack.assert_awaited_once()
        assert "growth" in post_slack.await_args.kwargs["text"]
        send_email.assert_awaited_once()
        assert posts == ["http://wh"]

    @pytest.mark.asyncio
    async def test_failures_nonfatal(self, monkeypatch):
        monkeypatch.setattr(L, "get_settings", lambda: self._settings(
            SLACK_BOT_TOKEN="tok", DIGEST_EMAIL_TO="ops@x.co",
            LEAD_CREATED_WEBHOOK_URL="http://wh"))
        import app.services.slack_notifications as sn
        monkeypatch.setattr(sn, "_post_slack_text",
                            AsyncMock(side_effect=RuntimeError()))
        import app.services.email_digest as ed
        monkeypatch.setattr(ed, "send_email",
                            AsyncMock(side_effect=RuntimeError()))

        class _C:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            async def post(self, *a, **kw):
                raise TimeoutError()
        monkeypatch.setattr(L.httpx, "AsyncClient", lambda **kw: _C())
        await L.notify_lead_created(_lead(notes="n"))  # no raise
