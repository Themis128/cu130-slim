"""Paddle Billing usage and revenue digest for Slack (#paddle)."""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.core.config import get_settings
from app.services.paddle_api import PaddleError, _request

logger = logging.getLogger(__name__)


def _money(cents: int | None) -> str:
    if cents is None:
        return "N/A"
    return f"${cents / 100:,.2f}"


async def _list_paddle(path: str, params: dict | None = None, per_page: int = 100) -> list[dict]:
    settings = get_settings()
    if not settings.PADDLE_API_KEY:
        logger.warning("Paddle %s skipped: no PADDLE_API_KEY", path)
        return []
    try:
        body = await _request(
            "GET",
            path,
            params={"per_page": str(per_page), **(params or {})},
        )
    except PaddleError as exc:
        logger.warning("Paddle %s list failed: %s", path, exc)
        return []
    return body.get("data") or []


async def build_paddle_digest() -> str:
    """Fetch customers, subscriptions and transactions from Paddle, then build a Slack report."""
    settings = get_settings()
    env = settings.PADDLE_ENVIRONMENT or "sandbox"
    now = datetime.now(UTC)
    today = now.date().isoformat()

    customers = await _list_paddle("/customers")
    subscriptions = await _list_paddle(
        "/subscriptions",
        {"status": "active,trialing,past_due,canceled,paused"},
    )
    transactions = await _list_paddle(
        "/transactions",
        {"status": "ready,billed,paid,refunded,canceled"},
    )

    active_subs = [s for s in subscriptions if s.get("status") == "active"]
    trialing = [s for s in subscriptions if s.get("status") == "trialing"]
    past_due = [s for s in subscriptions if s.get("status") == "past_due"]
    canceled = [s for s in subscriptions if s.get("status") == "canceled"]

    paid_txns = [t for t in transactions if t.get("status") == "paid"]
    revenue_cents = 0
    paid_today = []
    revenue_today = 0
    for t in paid_txns:
        totals = (t.get("details") or {}).get("totals") or {}
        amount = totals.get("total") or 0
        revenue_cents += amount
        billed = t.get("billed_at") or t.get("created_at") or ""
        if billed.startswith(today):
            paid_today.append(t)
            revenue_today += amount

    # Rough recurring MRR estimate (active sub items only)
    recurring_cents = 0
    for s in active_subs:
        for item in s.get("items", []):
            price = (item.get("price") or {}).get("unit_price") or {}
            qty = item.get("quantity") or 1
            recurring_cents += (price.get("amount") or 0) * qty

    tier_counts: dict[str, int] = {}
    for s in active_subs:
        for item in s.get("items", []):
            price_id = (item.get("price") or {}).get("id")
            if not price_id:
                continue
            tier = settings.paddle_price_tiers.get(price_id, "unknown")
            tier_counts[tier] = tier_counts.get(tier, 0) + 1

    lines = [
        "*Clear skies. Zero friction.*",
        f"*Paddle usage report* · {env.upper()} · {now.strftime('%a %d %b %Y %H:%M %Z')}",
        "",
        "*Customers & subscriptions*",
        f"• Customers: *{len(customers)}*",
        f"• Active subscriptions: *{len(active_subs)}*",
        f"• Trialing: *{len(trialing)}* · Past due: *{len(past_due)}* · Canceled: *{len(canceled)}*",
    ]
    if tier_counts:
        lines.append("")
        lines.append("*Active subs by tier*")
        for tier, count in sorted(tier_counts.items()):
            lines.append(f"  - {tier}: *{count}*")

    lines += [
        "",
        "*Revenue (paid transactions)*",
        f"• All-time paid: *{len(paid_txns)}* transactions · *{_money(revenue_cents)}*",
        f"• Today: *{len(paid_today)}* transactions · *{_money(revenue_today)}*",
        f"• Recurring MRR (approx): *{_money(recurring_cents)}*",
    ]

    return "\n".join(lines)


async def send_paddle_digest_to_slack(*, post_to_slack: bool = True) -> dict:
    """Build the Paddle digest and optionally post it to the configured Slack channel."""
    text = await build_paddle_digest()
    result = {"text": text, "posted": False, "error": None}
    if post_to_slack:
        from app.services.slack_notifications import post_paddle_digest_to_slack

        ok, err = await post_paddle_digest_to_slack(text)
        result["posted"] = ok
        result["error"] = err
    return result
