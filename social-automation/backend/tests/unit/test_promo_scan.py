"""Tests for app/services/promo_scan.py — the daily free-promotion scan."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import app.services.promo_scan as P


def _settings(**over):
    base = dict(
        SMTP_HOST="imap.test",
        SMTP_PORT=587,
        SMTP_USER="ops@test.gr",
        SMTP_PASSWORD="pw",
        SMTP_SSL_VERIFY=False,
        LINKEDIN_AD_ACCOUNT_ID="512642510",
        LINKEDIN_ADS_CREDIT_EUR=136.75,
        PROMO_SCAN_IMAP_HOST="",
        PROMO_SCAN_IMAP_PORT=993,
        PROMO_SCAN_MAILBOX="",
        PROMO_SCAN_IMAP_PASS="",
        PROMO_SCAN_LOOKBACK_DAYS=2,
        PROMO_SCAN_EMAIL_TO="",
        DIGEST_EMAIL_TO="ops@test.gr",
    )
    base.update(over)
    return SimpleNamespace(**base)


class _FakeIMAP:
    """Drives scan_promo_emails: canned header/body fetch responses."""

    def __init__(self, messages: dict[bytes, dict[str, bytes]]):
        self.messages = messages
        self.selected = None

    def __call__(self, host, port, ssl_context=None):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, user, password):
        self.auth = (user, password)

    def select(self, mbox, readonly=False):
        self.selected = mbox

    def search(self, charset, criterion, value):
        return "OK", [b" ".join(self.messages)]

    def fetch(self, msg_id, spec):
        msg = self.messages.get(msg_id)
        if msg is None:
            return "NO", [None]
        if "HEADER.FIELDS" in spec:
            return "OK", [(msg_id, msg["header"])]
        return "OK", [(msg_id, msg["text"])]


def _msg(frm: str, subj: str, body: bytes = b"") -> dict[str, bytes]:
    return {
        "header": f"From: {frm}\r\nSubject: {subj}\r\nDate: Wed, 15 Oct 2026 09:00:00 +0000\r\n".encode(),
        "text": body,
    }


# ── pure helpers ───────────────────────────────────────────────────────────


def test_sender_platform_known_domains():
    assert P._sender_platform("LinkedIn Ads <messages-noreply@linkedin.com>") == "linkedin"
    assert P._sender_platform("Meta for Business <noreply@facebookmail.com>") == "meta"
    assert P._sender_platform("ads@tiktok.com") == "tiktok"


def test_sender_platform_unknown():
    assert P._sender_platform("newsletter@random-blog.io") == ""


def test_extract_offer_amount_and_expiry():
    amount, expiry = P._extract_offer(
        "Your €500 ad credit", "Redeem by December 31, 2026 at linkedin.com"
    )
    assert amount == "€500"
    assert "December 31, 2026" in expiry


# ── mailbox scan ───────────────────────────────────────────────────────────


def test_scan_promo_emails_flags_credit_offer(monkeypatch):
    messages = {
        b"1": _msg(
            "LinkedIn Ads <ads@linkedin.com>",
            "You've received a €500 ad credit",
            b"Claim your EUR 500 credit. Expires November 30, 2026.",
        ),
        b"2": _msg("newsletter@blog.io", "Weekly digest", b"no promos here"),
        b"3": _msg("Meta <noreply@facebookmail.com>", "Boost a post on us", b"coupon inside"),
    }
    monkeypatch.setattr(P.imaplib, "IMAP4_SSL", _FakeIMAP(messages))
    monkeypatch.setattr(P, "get_settings", lambda: _settings())

    opps, errors = P.scan_promo_emails(lookback_days=2)

    assert errors == []
    platforms = {o.platform for o in opps}
    assert "linkedin" in platforms
    assert "meta" in platforms
    li = next(o for o in opps if o.platform == "linkedin")
    assert li.kind == "email_offer"
    assert "500" in li.value
    assert "November 30, 2026" in li.expires
    assert li.source == "imap:INBOX"


def test_scan_promo_emails_ignores_non_promo(monkeypatch):
    messages = {b"1": _msg("LinkedIn <a@linkedin.com>", "Your weekly analytics", b"views up")}
    monkeypatch.setattr(P.imaplib, "IMAP4_SSL", _FakeIMAP(messages))
    monkeypatch.setattr(P, "get_settings", lambda: _settings())
    opps, _ = P.scan_promo_emails()
    assert opps == []


def test_scan_promo_emails_no_credentials(monkeypatch):
    monkeypatch.setattr(P, "get_settings", lambda: _settings(SMTP_PASSWORD=""))
    opps, errors = P.scan_promo_emails()
    assert opps == []
    assert errors and "credentials" in errors[0]


def test_scan_promo_emails_imap_failure_is_reported(monkeypatch):
    def _boom(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(P.imaplib, "IMAP4_SSL", _boom)
    monkeypatch.setattr(P, "get_settings", lambda: _settings())
    opps, errors = P.scan_promo_emails()
    assert opps == []
    assert errors and "mailbox scan" in errors[0]


# ── LinkedIn credit state ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_linkedin_credit_remaining_is_opportunity(monkeypatch):
    metrics = SimpleNamespace(
        account_spend_eur=99.51,
        ad_set_statuses={"active": 0},
    )
    monkeypatch.setattr(P, "get_settings", lambda: _settings())

    async def _fake_collect(db):
        return metrics

    import app.services.linkedin_ads_report as L

    monkeypatch.setattr(L, "collect_metrics", _fake_collect)
    opps, errors = await P.collect_linkedin_credit(db=None)
    assert errors == []
    assert len(opps) == 1
    o = opps[0]
    assert o.kind == "ad_credit_state"
    assert "37.24" in o.value  # 136.75 − 99.51
    assert o.automatable is True
    assert "1.2" in o.action or "31.03" in o.action  # ÷1.2 ceiling ≈ €31


@pytest.mark.asyncio
async def test_linkedin_credit_exhausted_warns(monkeypatch):
    metrics = SimpleNamespace(
        account_spend_eur=143.03,
        ad_set_statuses={"active": 0},
    )
    monkeypatch.setattr(P, "get_settings", lambda: _settings())

    async def _fake_collect(db):
        return metrics

    import app.services.linkedin_ads_report as L

    monkeypatch.setattr(L, "collect_metrics", _fake_collect)
    opps, _ = await P.collect_linkedin_credit(db=None)
    assert "exhausted" in opps[0].title.lower()
    assert opps[0].automatable is False


@pytest.mark.asyncio
async def test_linkedin_credit_api_failure_is_reported(monkeypatch):
    monkeypatch.setattr(P, "get_settings", lambda: _settings())

    async def _boom(db):
        raise RuntimeError("token expired")

    import app.services.linkedin_ads_report as L

    monkeypatch.setattr(L, "collect_metrics", _boom)
    opps, errors = await P.collect_linkedin_credit(db=None)
    assert opps == []
    assert errors and "linkedin ads api" in errors[0]


# ── program gates ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_program_gate_near_unlock(monkeypatch):
    import app.services.monetization_report as R
    from app.services.monetization import evaluate_program

    gate = R.AccountGate(
        platform="facebook",
        handle="@cloudless.gr",
        account_type="user",
        followers=480,
        gain_per_day=2.0,
        followers_live=True,
        program=evaluate_program(
            "facebook", {"followers": 480, "days_held": 0}, account_type="user"
        ),
        funnel_note="",
        eta_days=10,
    )

    async def _gates(db, team_id):
        return [gate]

    monkeypatch.setattr(R, "_gather_gates", _gates)
    opps = await P.collect_program_opportunities(db=None, team_id="t")
    assert len(opps) == 1
    assert "Stars" in opps[0].title
    assert "~10d" in opps[0].title


@pytest.mark.asyncio
async def test_program_gate_eligible_now(monkeypatch):
    import app.services.monetization_report as R
    from app.services.monetization import Criterion, ProgramReport

    # A program whose every gate is met → monetizable True → "eligible NOW".
    prog = ProgramReport(
        platform="tiktok",
        program="Creator Rewards",
        criteria=[
            Criterion("10,000 followers", 12_000, 10_000, True, ""),
            Criterion("100k views/30d", 200_000, 100_000, True, ""),
        ],
        monetizable=True,
    )

    gate = R.AccountGate(
        platform="tiktok", handle="@cloudless.gr", account_type=None,
        followers=12_000, gain_per_day=10.0, followers_live=True,
        program=prog, funnel_note="", eta_days=0,
    )

    async def _gates(db, team_id):
        return [gate]

    monkeypatch.setattr(R, "_gather_gates", _gates)
    opps = await P.collect_program_opportunities(db=None, team_id="t")
    assert any("eligible NOW" in o.title for o in opps)


# ── report shape + email ───────────────────────────────────────────────────


def test_report_text_sections_and_subject():
    r = P.PromoReport(generated_at=datetime.now(UTC))
    r.opportunities.append(
        P.PromoOpportunity(
            platform="linkedin", kind="email_offer",
            title="You've received a €500 ad credit", detail="from ads@linkedin.com",
            value="€500", expires="November 30, 2026",
            action="Claim via the email link.", source="imap:INBOX",
        )
    )
    text = r.to_text()
    assert "PROMO EMAILS" in text
    assert "€500" in text
    assert "expires November 30, 2026" in text
    assert "1 live signal" in r.subject()


def test_report_html_renders_branded():
    r = P.PromoReport(generated_at=datetime.now(UTC))
    r.opportunities.append(
        P.PromoOpportunity(
            platform="meta", kind="manual_check",
            title="Check Ads Manager", detail="banner offers only",
            action="look", source="console-only",
        )
    )
    html = r.to_html()
    assert "cloudless" in html.lower()
    assert "Check Ads Manager" in html


@pytest.mark.asyncio
async def test_email_promo_report_sends(monkeypatch):
    sent = {}

    async def _send(**kw):
        sent.update(kw)

    import app.services.email_digest as E

    monkeypatch.setattr(E, "send_email", _send)
    monkeypatch.setattr(P, "get_settings", lambda: _settings())

    r = P.PromoReport(generated_at=datetime.now(UTC))
    r = await P.email_promo_report(r)
    assert r.emailed is True
    assert sent["to_addrs"] == ["ops@test.gr"]
    assert "text_body" in sent and "html_body" in sent


@pytest.mark.asyncio
async def test_email_promo_report_no_recipient(monkeypatch):
    monkeypatch.setattr(P, "get_settings", lambda: _settings(DIGEST_EMAIL_TO=""))
    r = P.PromoReport(generated_at=datetime.now(UTC))
    r = await P.email_promo_report(r)
    assert r.emailed is False
    assert "not set" in r.email_error
