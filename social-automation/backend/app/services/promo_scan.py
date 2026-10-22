"""Daily free-promotion scanner.

Collects *real* promotion signals for every connected surface and emails a
report on what Cloudless can exploit for free:

1. **Mailbox scan** — ad-credit / coupon / promo-offer emails landing in the
   digest mailbox are how LinkedIn, Meta, TikTok, X, Google et al. actually
   deliver credits. Headers are scanned for every platform sender + promo
   keyword; matching messages get a small body excerpt fetched for amount /
   expiry extraction. Header+excerpt only — full bodies are never pulled.
2. **LinkedIn ad account** — live Marketing API state (account-wide spend vs
   the promo credit, campaign statuses) via ``linkedin_ads_report``.
3. **Creator-program gates** — the existing ``monetization`` evaluator flags
   programs that are already unlocked or close enough to push for.
4. **Console-only surfaces** — promo surfaces with no API (Meta Ads Manager
   coupon banner, TikTok Promotions tab, LinkedIn CM billing credits, X Ads)
   are listed as manual-check pointers so nothing is silently skipped.

Deliberately read-only: the scan never claims a coupon, spends budget, or
accepts terms — it reports. Exploitation actions go through the normal
publish/ads paths with the usual approvals.
"""
from __future__ import annotations

import asyncio
import imaplib
import logging
import re
import ssl
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from typing import Any

from sqlalchemy import select

from app.core.config import get_settings
from app.models.social_account import SocialAccount
from app.models.user import Team

logger = logging.getLogger(__name__)

# ── promo-signal matching ─────────────────────────────────────────────────

# Sender domains → platform. Promo offers almost always come from the
# platform's own mail domain (LinkedIn credits, facebookmail offers, …).
_PLATFORM_SENDERS: dict[str, tuple[str, ...]] = {
    "linkedin": ("linkedin.com",),
    "meta": ("facebookmail.com", "meta.com", "facebook.com", "instagram.com"),
    "tiktok": ("tiktok.com", "tiktokforbusiness.com"),
    "x": ("x.com", "twitter.com"),
    "google": ("google.com",),
    "pinterest": ("pinterest.com",),
    "snapchat": ("snapchat.com", "snap.com"),
    "reddit": ("reddit.com", "redditmail.com"),
    "microsoft": ("microsoft.com", "bingads.microsoft.com"),
}

# Subject/body keywords that mark a genuine promotion — ad credits, coupons,
# vouchers, spend-match offers, bonuses, grants. Kept tight on purpose: a
# generic "grow your business" newsletter is not a promotion.
_PROMO_RE = re.compile(
    r"ad\s?credit|advertising credit|coupon|voucher|promo\s?code|"
    r"promotional (credit|offer)|spend.{0,20}get|match.{0,15}spend|"
    r"free.{0,10}(trial|ads|credit|advertis)|claim your|"
    r"bonus (offer|credit)|ad grant|on us\b|gift (credit|card)|"
    r"boost.{0,25}(on us|free|credit|for free)",
    re.IGNORECASE,
)

_MONEY_RE = re.compile(r"(?:€|\$|£)\s?\d[\d,]*(?:\.\d{1,2})?|\d[\d,]*(?:\.\d{1,2})?\s?(?:EUR|USD|GBP|euros?|dollars?)")
_EXPIRY_RE = re.compile(
    r"(?:expires?|expiry|valid until|redeem by|ends?|by)\s+([A-Z][a-z]+\s+\d{1,2},?\s+\d{4}|\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})",
    re.IGNORECASE,
)

# Console surfaces that carry promotions but expose no API — reported as
# manual-check pointers so the scan covers every platform honestly.
_MANUAL_CHECKS: list[tuple[str, str, str]] = [
    (
        "linkedin",
        "Campaign Manager → Account settings → Billing → Credits and promotions",
        "New coupons land here AND via email; one Spend-Get roughly every 90d.",
    ),
    (
        "meta",
        "Ads Manager → Billing → Payment settings → Ad credits / yellow banner",
        "Meta publishes no public coupon page — banner/email offers only.",
    ),
    (
        "tiktok",
        "TikTok Ads Manager → Billing → Promotions tab",
        "New-advertiser spend-match attaches automatically once billing exists.",
    ),
    (
        "x",
        "ads.x.com → Billing → Promotions",
        "Spend-match offers ($500 for $500) appear per-account, ~60d window.",
    ),
]


@dataclass
class PromoOpportunity:
    platform: str
    kind: str  # email_offer | ad_credit_state | program_gate | manual_check
    title: str
    detail: str
    action: str
    value: str = ""
    expires: str = ""
    source: str = ""
    automatable: bool = False


@dataclass
class PromoReport:
    generated_at: datetime
    opportunities: list[PromoOpportunity] = field(default_factory=list)
    scan_errors: list[str] = field(default_factory=list)
    playbook: list[str] = field(default_factory=list)  # LLM exploit notes
    llm_used: bool = False
    emailed: bool = False
    email_error: str = ""

    def subject(self) -> str:
        n = len([o for o in self.opportunities if o.kind != "manual_check"])
        return f"Free-promo scan — {n} live signal{'s' if n != 1 else ''} · {self.generated_at:%b %d}"

    def to_text(self) -> str:
        lines = [
            f"Free promotions scan — {self.generated_at:%Y-%m-%d %H:%M} UTC",
            "=" * 56,
            "",
        ]
        order = ["email_offer", "ad_credit_state", "program_gate", "manual_check"]
        labels = {
            "email_offer": "PROMO EMAILS IN THE INBOX",
            "ad_credit_state": "AD-CREDIT STATE (live API)",
            "program_gate": "FREE MONETIZATION PROGRAMS",
            "manual_check": "CONSOLE-ONLY CHECKS (no API — look yourself)",
        }
        for kind in order:
            group = [o for o in self.opportunities if o.kind == kind]
            if not group:
                continue
            lines.append(f"{labels[kind]} ({len(group)})")
            lines.append("-" * 56)
            for o in group:
                value = f" [{o.value}]" if o.value else ""
                exp = f" — expires {o.expires}" if o.expires else ""
                lines.append(f"• [{o.platform}] {o.title}{value}{exp}")
                if o.detail:
                    lines.append(f"    {o.detail}")
                lines.append(f"    → {o.action}")
                if o.source:
                    lines.append(f"    source: {o.source}")
            lines.append("")
        if not self.opportunities:
            lines.append("No promotion signals found today.")
            lines.append("")
        if self.playbook:
            lines.append("HOW TO EXPLOIT")
            lines.append("-" * 56)
            for p in self.playbook:
                lines.append(f"• {p}")
            lines.append("")
        if self.scan_errors:
            lines.append("SCAN WARNINGS")
            lines.append("-" * 56)
            for e in self.scan_errors:
                lines.append(f"• {e}")
        return "\n".join(lines)

    def to_html(self) -> str:
        from app.services.email_brand import render_branded_html

        return render_branded_html(
            self.to_text(),
            subject=self.subject(),
            from_name="Cloudless Ops",
            preheader="Daily scan for ad credits, coupons and free programs.",
        )


# ── mailbox scan ───────────────────────────────────────────────────────────


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:  # noqa: BLE001 — a malformed header still yields text
        return value


def _sender_platform(from_header: str) -> str:
    low = from_header.lower()
    for platform, domains in _PLATFORM_SENDERS.items():
        if any(d in low for d in domains):
            return platform
    return ""


def _extract_offer(subject: str, excerpt: str) -> tuple[str, str]:
    """Best-effort amount / expiry pulled from the subject + body excerpt."""
    blob = f"{subject}\n{excerpt}"
    amount = ""
    expiry = ""
    if m := _MONEY_RE.search(blob):
        amount = m.group(0)
    if m := _EXPIRY_RE.search(blob):
        expiry = m.group(1)
    return amount, expiry


def _imap_search(mail: imaplib.IMAP4_SSL, since: str) -> list[bytes]:
    status, data = mail.search(None, "SINCE", since)
    if status != "OK" or not data or not data[0]:
        return []
    return data[0].split()


def scan_promo_emails(lookback_days: int = 2) -> tuple[list[PromoOpportunity], list[str]]:
    """IMAP-sweep the digest mailbox for promo-offer mail.

    Stage 1 fetches FROM/SUBJECT/DATE headers only; stage 2 pulls a 2KB body
    excerpt for promo-keyword hits to recover the amount/expiry. Credentials
    reuse the SMTP_* mailbox identity (dovecot SASL = one account).
    """
    settings = get_settings()
    host = settings.PROMO_SCAN_IMAP_HOST or settings.SMTP_HOST
    user = settings.PROMO_SCAN_MAILBOX or settings.SMTP_USER
    password = settings.PROMO_SCAN_IMAP_PASS or settings.SMTP_PASSWORD
    port = settings.PROMO_SCAN_IMAP_PORT
    if not (host and user and password):
        return [], ["IMAP credentials not configured (SMTP_USER/SMTP_PASSWORD)"]

    opportunities: list[PromoOpportunity] = []
    errors: list[str] = []
    try:
        if settings.SMTP_SSL_VERIFY:
            ctx = ssl.create_default_context()
        else:
            ctx = ssl._create_unverified_context()  # noqa: SLF001 — self-hosted dovecot, matching SMTP path
        since = (datetime.now(UTC) - timedelta(days=lookback_days)).strftime("%d-%b-%Y")
        with imaplib.IMAP4_SSL(host, port, ssl_context=ctx) as mail:
            mail.login(user, password)
            mail.select("INBOX", readonly=True)
            ids = _imap_search(mail, since)
            candidates: list[tuple[bytes, str, str, str]] = []
            for msg_id in ids[-400:]:  # cap: a busy mailbox still scans newest 400
                status, data = mail.fetch(
                    msg_id, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])"
                )
                if status != "OK" or not data or not isinstance(data[0], tuple):
                    continue
                header = data[0][1].decode("utf-8", "replace")
                frm_m = re.search(r"(?im)^From:\s*(.+)$", header)
                subj_m = re.search(r"(?im)^Subject:\s*(.+)$", header)
                frm = _decode(frm_m.group(1) if frm_m else "")
                subj = _decode(subj_m.group(1) if subj_m else "")
                date_h = re.search(r"(?im)^Date:\s*(.+)$", header)
                date_s = date_h.group(1).strip() if date_h else ""
                if _PROMO_RE.search(subj):
                    candidates.append((msg_id, frm, subj, date_s))

            for msg_id, frm, subj, date_s in candidates[-40:]:  # excerpt-fetch cap
                excerpt = ""
                status, data = mail.fetch(msg_id, "(BODY.PEEK[TEXT]<0.2048>)")
                if status == "OK" and data and isinstance(data[0], tuple):
                    excerpt = data[0][1].decode("utf-8", "replace")
                if not (_PROMO_RE.search(excerpt) or _PROMO_RE.search(subj)):
                    continue  # pragma: no cover — candidates are pre-filtered on subj
                platform = _sender_platform(frm) or "unknown"
                amount, expiry = _extract_offer(subj, excerpt)
                try:
                    when = parsedate_to_datetime(date_s).strftime("%b %d") if date_s else ""
                except Exception:  # noqa: BLE001
                    when = ""
                opportunities.append(
                    PromoOpportunity(
                        platform=platform,
                        kind="email_offer",
                        title=subj[:140] or "(no subject)",
                        detail=f"from {frm[:80]}" + (f" · {when}" if when else ""),
                        value=amount,
                        expires=expiry,
                        action="Open the email and follow the claim link — promo links are single-use and tied to this mailbox's accounts.",
                        source="imap:INBOX",
                    )
                )
    except Exception as exc:  # noqa: BLE001 — report still goes out
        logger.exception("Promo mailbox scan failed")
        errors.append(f"mailbox scan: {type(exc).__name__}: {str(exc)[:200]}")
    return opportunities, errors


# ── LinkedIn ad-credit live state ──────────────────────────────────────────


async def collect_linkedin_credit(db) -> tuple[list[PromoOpportunity], list[str]]:
    """Live ad-account state → remaining-credit / overspend opportunity rows."""
    settings = get_settings()
    if not settings.LINKEDIN_AD_ACCOUNT_ID:
        return [], []
    try:
        from app.services.linkedin_ads_report import collect_metrics

        m = await collect_metrics(db)
    except Exception as exc:  # noqa: BLE001
        logger.exception("LinkedIn ads metrics collection failed")
        return [], [f"linkedin ads api: {type(exc).__name__}: {str(exc)[:200]}"]

    credit = settings.LINKEDIN_ADS_CREDIT_EUR
    out: list[PromoOpportunity] = []
    if credit > 0 and m.account_spend_eur:
        remaining = round(credit - m.account_spend_eur, 2)
        active = m.ad_set_statuses.get("active", 0)
        if remaining > 0:
            ceiling = round(remaining / 1.2, 2)  # LinkedIn can over-deliver ~20%
            out.append(
                PromoOpportunity(
                    platform="linkedin",
                    kind="ad_credit_state",
                    title=f"€{remaining:.2f} promo credit unspent",
                    detail=(
                        f"account spend €{m.account_spend_eur:.2f} of €{credit:.2f} credit; "
                        f"{active} campaign(s) active"
                    ),
                    value=f"€{remaining:.2f}",
                    action=(
                        f"Relaunch a campaign capped ≤€{ceiling:.2f} lifetime with a hard "
                        "end date (÷1.2 covers ~20% over-delivery). Check CM → Billing → "
                        "Credits for expiry first."
                    ),
                    source="linkedin-marketing-api",
                    automatable=True,
                )
            )
        else:
            out.append(
                PromoOpportunity(
                    platform="linkedin",
                    kind="ad_credit_state",
                    title=f"Promo credit exhausted ({remaining:+.2f})",
                    detail=(
                        f"account spend €{m.account_spend_eur:.2f} vs €{credit:.2f} credit — "
                        "overage bills the card on file"
                    ),
                    value=f"{remaining:+.2f}",
                    action="Keep all campaigns stopped; every further euro hits the Visa.",
                    source="linkedin-marketing-api",
                )
            )
    return out, []


# ── creator-program gates ──────────────────────────────────────────────────


async def collect_program_opportunities(db, team_id) -> list[PromoOpportunity]:
    """Near/unlocked monetization gates = free revenue programs to exploit."""
    from app.services.monetization_report import _gather_gates

    out: list[PromoOpportunity] = []
    try:
        gates = await _gather_gates(db, team_id)
    except Exception:  # noqa: BLE001
        logger.exception("Gate evaluation failed")
        return out

    for g in gates:
        if not g.program:
            continue
        unmet = [c for c in g.program.criteria if c.met is False]
        pending = [c for c in g.program.criteria if c.met is None]
        if g.program.monetizable is True:
            out.append(
                PromoOpportunity(
                    platform=g.platform,
                    kind="program_gate",
                    title=f"{g.program.program} — eligible NOW ({g.handle})",
                    detail="All numeric gates met; only manual enrolment remains.",
                    action="Enrol in the platform's monetization dashboard today.",
                    source="program-evaluator",
                    automatable=False,
                )
            )
        elif unmet and g.eta_days is not None and g.eta_days <= 60:
            gap = ", ".join(
                f"{c.name} {c.current}/{c.target}" for c in unmet[:3]
            )
            out.append(
                PromoOpportunity(
                    platform=g.platform,
                    kind="program_gate",
                    title=f"{g.program.program} — ~{g.eta_days}d to unlock ({g.handle})",
                    detail=f"unmet: {gap}"
                    + (f"; manual pending: {', '.join(c.name for c in pending[:2])}" if pending else ""),
                    value="free payout program",
                    action="Prioritise growth content on this account — the daily monetization report already schedules it.",
                    source="program-evaluator",
                )
            )
    return out


# ── console-only pointers ─────────────────────────────────────────────────


async def collect_manual_checks(db, team_id) -> list[PromoOpportunity]:
    """One manual-check row per console surface for platforms we actually use."""
    platforms = set(
        (
            await db.execute(
                select(SocialAccount.platform).where(
                    SocialAccount.team_id == team_id,
                    SocialAccount.status == "active",
                )
            )
        ).scalars()
    )
    platforms.add("google")  # ads account may exist outside SocialAuto
    return [
        PromoOpportunity(
            platform=platform,
            kind="manual_check",
            title=f"Check {surface}",
            detail=note,
            action="2-minute console look — no API exists for this surface.",
            source="console-only",
        )
        for platform, surface, note in _MANUAL_CHECKS
        if platform in platforms or platform == "google"
    ]


# ── LLM playbook ───────────────────────────────────────────────────────────

_PLAYBOOK_SCHEMA = {
    "type": "object",
    "properties": {
        "playbook": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["playbook"],
}


async def _llm_playbook(
    db, team_id, opportunities: list[PromoOpportunity]
) -> list[str]:
    """DMR → CF inference turns the findings into an exploit playbook.

    Returns [] on any failure — the report still ships without it."""
    from app.services.inference import call_inference

    findings = [
        {
            "platform": o.platform,
            "kind": o.kind,
            "title": o.title,
            "value": o.value,
            "expires": o.expires,
            "action": o.action,
        }
        for o in opportunities
    ]
    prompt = f"""You are the growth operator for cloudless.gr (managed cloud hosting /
social-automation SaaS, Polar billing at social.cloudless.gr/pricing).

TODAY'S FREE-PROMOTION FINDINGS (real scan output):
{findings}

Write 3-5 playbook bullets on how to exploit them TODAY. Rules:
- Spend-match offers (spend €X get €Y) require real spend FIRST — flag the cost
  honestly; never recommend claiming one without noting it bills the card.
- The standing rule is "never spend money" — a €500-for-€500 match is NOT free.
- Email offers: say exactly which mailbox/console to open.
- Program gates: name the concrete next post/action that closes the gap.
- If a finding has an expiry, the bullet starts with the deadline.
Each bullet ≤ 25 words, imperative voice, no fluff.

Return ONLY {{"playbook": ["...", "..."]}}"""

    try:
        result = await call_inference(
            prompt,
            provider_name="dmr",
            db=db,
            team_id=team_id,
            schema=_PLAYBOOK_SCHEMA,
            platform="report",
        )
        items = result.get("playbook") or []
        return [str(i).strip() for i in items if str(i).strip()][:6]
    except Exception:  # noqa: BLE001
        logger.exception("Promo playbook inference failed")
        return []


# ── entry point ────────────────────────────────────────────────────────────


async def build_promo_report(db) -> PromoReport:
    """Collect every source once, merge across teams, assemble the report."""
    settings = get_settings()
    report = PromoReport(generated_at=datetime.now(UTC))

    email_opps, email_errors = await asyncio.to_thread(
        scan_promo_emails, settings.PROMO_SCAN_LOOKBACK_DAYS
    )
    report.opportunities.extend(email_opps)
    report.scan_errors.extend(email_errors)

    li_opps, li_errors = await collect_linkedin_credit(db)
    report.opportunities.extend(li_opps)
    report.scan_errors.extend(li_errors)

    teams = (await db.execute(select(Team))).scalars().all()
    for team in teams:
        report.opportunities.extend(await collect_program_opportunities(db, team.id))
        report.opportunities.extend(await collect_manual_checks(db, team.id))
        if not report.playbook:
            report.playbook = await _llm_playbook(
                db, team.id, report.opportunities
            )
            report.llm_used = bool(report.playbook)
    return report


async def email_promo_report(report: PromoReport) -> PromoReport:
    from app.services.email_digest import send_email

    settings = get_settings()
    to = (settings.PROMO_SCAN_EMAIL_TO or settings.DIGEST_EMAIL_TO or "").strip()
    if not to:
        report.email_error = "PROMO_SCAN_EMAIL_TO / DIGEST_EMAIL_TO not set"
        return report
    try:
        await send_email(
            subject=report.subject(),
            text_body=report.to_text(),
            html_body=report.to_html(),
            to_addrs=[to],
        )
        report.emailed = True
    except Exception as exc:  # noqa: BLE001
        report.email_error = str(exc) or repr(exc)
        logger.exception("Failed to email promo report")
    return report


async def run_promo_scan(db, *, send: bool = True) -> dict[str, Any]:
    report = await build_promo_report(db)
    if send:
        report = await email_promo_report(report)
    return {
        "opportunities": len(report.opportunities),
        "email_offers": sum(1 for o in report.opportunities if o.kind == "email_offer"),
        "llm_used": report.llm_used,
        "emailed": report.emailed,
        "email_error": report.email_error,
        "scan_errors": report.scan_errors,
    }
