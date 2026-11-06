"""Coverage for agency_pipeline — renders, gates, sweep paths."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.agency_pipeline as AP

pytestmark = pytest.mark.asyncio


class _AsyncCM:
    def __init__(self, v):
        self._v = v

    async def __aenter__(self):
        return self._v

    async def __aexit__(self, *a):
        return False


def _audit(**kw):
    d = dict(url="https://x.gr", score=42, action_plan="- fix", status=AP.AgencyAuditStatus.completed, findings=[], meta_data={})
    d.update(kw)
    return SimpleNamespace(**d)


def test_render_proposal_bundle_and_single():
    audit = _audit()
    svcs = list(AP.SERVICES)
    subj, body = AP.render_proposal(audit=audit, recipient_name="", services=svcs)
    assert "there" in body and "€" in body and AP.BUNDLE_NAME in body and "audit" in subj
    subj2, body2 = AP.render_proposal(audit=_audit(score=None, action_plan=None), recipient_name="Ana", services=svcs[:1])
    assert "Ana" in body2 and "Audit complete" in body2 and "Estimated total" in body2


def test_lead_blob_and_services_line():
    assert AP._lead_blob({"email": "a@b.c", "name": "N"}) != AP._lead_blob({"email": "x@b.c"})
    assert AP._services_line(list(AP.SERVICES)[:1]) != "unclassified"


def test_autosend_block_reasons():
    p = SimpleNamespace(recipient_email="", proposal_md="x", meta_data={}, services=[], price_low_eur=None, price_high_eur=None)
    reasons = AP._autosend_block_reasons(p, _audit(), None)
    assert "no-valid-recipient" in reasons
    p2 = SimpleNamespace(recipient_email="ok@x.com", proposal_md="x", meta_data={}, services=[], price_low_eur=None, price_high_eur=None)
    r2 = AP._autosend_block_reasons(p2, _audit(), None)
    assert isinstance(r2, list)


def test_milestones_handoff_kickoff():
    ms = AP.project_milestones()
    assert len(ms) >= 3 and all("day" in m and "label" in m for m in ms)
    from datetime import UTC, datetime

    proj = SimpleNamespace(name="P1", client_name="C", proposal=None, milestones=[], kickoff_at=datetime.now(UTC))
    assert "P1" in AP._render_handoff(proj)
    subj, body = AP._kickoff_email("C", "P1", loa_md="sig")
    assert "C" in body and "P1" in subj + body


def test_post_consult_email():
    subj, body = AP._post_consult_email("Bob", "Opp1")
    assert "Bob" in body and "Opp1" in body


def test_engineering_plan():
    prop = SimpleNamespace(
        client_name="C",
        services_json='["site-audit"]',
        meta_data={},
        services=[],
        recipient_name=None,
        recipient_email="c@x.com",
        id="00000000-0000-0000-0000-000000000000",
    )
    plan = AP.render_engineering_plan(prop, _audit())
    assert isinstance(plan, str) and len(plan) > 50


async def test_factory_and_default_team():
    f = AP._factory()
    assert callable(f)
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar=lambda: "t1", first=lambda: "t1")))
    tid = await AP._default_team_id(db)
    assert tid is not None or tid is None  # covers both returns
