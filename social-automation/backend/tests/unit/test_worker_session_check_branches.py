"""Remaining branch arms of the LinkedIn and Instagram session-check
workers. They reuse the fakes from the main suites, so only the inputs differ."""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.worker.tasks.instagram_session_check as IG
import app.worker.tasks.linkedin_session_check as LI
from app.services.linkedin_sidecar import LinkedInSidecarError
from tests.unit import test_instagram_session_check as ig_suite
from tests.unit import test_linkedin_session_check as li_suite


class TestLinkedInSessionCheck:
    @pytest.mark.asyncio
    async def test_cookies_without_li_at_are_not_persisted(self, monkeypatch):
        import app.services.secret_store as ss

        store_set = AsyncMock()
        monkeypatch.setattr(ss, "secret_store", SimpleNamespace(set=store_set))
        li_suite._wire(
            monkeypatch,
            health={"status": "ok"},
            refresh={"logged_in": True, "cookies": {"JSESSIONID": "x"}},
            db_results=[li_suite._scalars([])],
        )
        out = await LI._run_check()
        assert out["cookies_refreshed"] is True
        store_set.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_401_active_account_without_owner_still_marked(self, monkeypatch):
        a1 = SimpleNamespace(id=uuid.uuid4(), status="active", team_id=uuid.uuid4())
        a2 = SimpleNamespace(id=uuid.uuid4(), status="active", team_id=uuid.uuid4())
        alert = AsyncMock()
        monkeypatch.setattr(LI, "_send_alert", alert)
        _, db = li_suite._wire(
            monkeypatch,
            health={"status": "ok"},
            refresh=LinkedInSidecarError(401, "expired"),
            # first owner missing → loop continues to the second account
            db_results=[li_suite._scalars([a1, a2]), li_suite._scalar(None),
                        li_suite._scalar(SimpleNamespace(email="o", name="O"))],
        )
        out = await LI._run_check()
        assert out["accounts_marked"] == 2
        assert a1.status == a2.status == "expired"
        alert.assert_awaited_once()
        db.commit.assert_awaited_once()


class TestInstagramSessionCheck:
    @pytest.mark.asyncio
    async def test_already_expired_account_not_recommitted(self, monkeypatch):
        acct = ig_suite._acct(status="expired", meta_data={"private_api_session_id": "s"})
        db = ig_suite._wire_run(monkeypatch, [acct], [ig_suite._scalar(None)], session=False)
        out = await IG._run_check()
        assert out["expired"] == 1
        assert acct.status == "expired"
        db.commit.assert_not_awaited()
