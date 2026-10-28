"""Dodo Payments usage and revenue digest for Slack (same #paddle channel)."""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.core.config import get_settings
from app.services.dodo_api import DodoError, _request

logger = logging.getLogger(__name__)


def _money(cents: int | None, currency: str = "USD") -> str:
    if cents is None:
        return "N/A"
    return f"{currency} {cents / 100:,.2f}"


async def _list_dodo(path: str, params: dict | None = None, per_page: int = 100) -> list[dict]:
    settings = get_settings()
    if not settings.DODO_PAYMENTS_API_KEY:
        logger.warning("Dodo %s skipped: no DODO_PAYMENTS_API_KEY", path)
        return []
    try:
        body = await _request(
            "GET",
            path,
            params={"page_size": str(per_page), "page_number": "0", **(params or {})},
        )
    except DodoError as exc:
        logger.warning("Dodo %s list failed: %s", path, exc)
        return []
    return body.get("items") or body.get("data") or []


async def build_dodo_digest() -> str:
    """Fetch customers, subscriptions and payments from Dodo → Slack report."""
    settings = get_settings()
    env = settings.DODO_ENVIRONMENT or "test_mode"
    now = datetime.now(UTC)
    today = now.date().isoformat()

    customers = await _list_dodo("/customers")
    subscriptions = await _list_dodo("/subscriptions")
    payments = await _list_dodo("/payments")

    active_subs = [s for s in subscriptions if s.get("status") == "active"]
    on_hold = [s for s in subscriptions if s.get("status") == "on_hold"]
    paused = [s for s in subscriptions if s.get("status") == "paused"]
    cancelled = [s for s in subscriptions if s.get("status") in ("cancelled", "expired")]

    paid_payments = [p for p in payments if p.get("status") == "succeeded"]
    revenue_cents = 0
    paid_today = []
    revenue_today = 0
    for p in paid_payments:
        amount = p.get("total_amount") or 0
        revenue_cents += amount
        billed = p.get("created_at") or ""
        if billed.startswith(today):
            paid_today.append(p)
            revenue_today += amount

    # Rough MRR: monthly-equivalent of active subscription amounts
    recurring_cents = 0
    for s in active_subs:
        amount = s.get("recurring_pre_tax_amount") or 0
        interval = s.get("payment_frequency_interval") or "Month"
        count = s.get("payment_frequency_count") or 1
        if interval.lower() == "year":
            recurring_cents += amount // (12 * count)
        elif interval.lower() == "week":
            recurring_cents += int(amount * 4.33 / count)
        else:
            recurring_cents += amount // count

    tier_counts: dict[str, int] = {}
    for s in active_subs:
        product_id = s.get("product_id")
        if not product_id:
            continue
        tier = settings.dodo_product_tiers.get(product_id, "unknown")
        tier_counts[tier] = tier_counts.get(tier, 0) + 1

    lines = [
        "*Clear skies. Zero friction.*",
        f"*Dodo usage report* · {env.upper().replace('_', ' ')} · {now.strftime('%a %d %b %Y %H:%M %Z')}",
        "",
        "*Customers & subscriptions*",
        f"• Customers: *{len(customers)}*",
        f"• Active subscriptions: *{len(active_subs)}*",
        f"• On hold: *{len(on_hold)}* · Paused: *{len(paused)}* · Cancelled: *{len(cancelled)}*",
    ]
    if tier_counts:
        lines.append("")
        lines.append("*Active subs by tier*")
        for tier, count in sorted(tier_counts.items()):
            lines.append(f"  - {tier}: *{count}*")

    lines += [
        "",
        "*Revenue (succeeded payments)*",
        f"• All-time: *{len(paid_payments)}* payments · *{_money(revenue_cents)}*",
        f"• Today: *{len(paid_today)}* payments · *{_money(revenue_today)}*",
        f"• Recurring MRR (approx): *{_money(recurring_cents)}*",
        "",
        "*Payouts (money you earn)*",
        "• Dodo settles directly to your bank — check Dashboard → Payouts.",
    ]

    return "\n".join(lines)
