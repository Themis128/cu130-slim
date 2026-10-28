"""Polar.sh usage and revenue digest for Slack (same #paddle channel)."""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.core.config import get_settings
from app.services.polar_api import PolarError, _request

logger = logging.getLogger(__name__)


def _money(cents: int | None, currency: str = "USD") -> str:
    if cents is None:
        return "N/A"
    return f"{currency} {cents / 100:,.2f}"


async def _list_polar(path: str, params: dict | None = None, per_page: int = 100) -> list[dict]:
    settings = get_settings()
    if not settings.POLAR_ACCESS_TOKEN:
        logger.warning("Polar %s skipped: no POLAR_ACCESS_TOKEN", path)
        return []
    try:
        body = await _request(
            "GET",
            path,
            params={"limit": str(per_page), **(params or {})},
        )
    except PolarError as exc:
        logger.warning("Polar %s list failed: %s", path, exc)
        return []
    # Polar list endpoints return {items: [...], pagination: {...}}
    return body.get("items") or body.get("data") or []


async def build_polar_digest() -> str:
    """Fetch customers, subscriptions and orders from Polar, then build a Slack report."""
    settings = get_settings()
    env = settings.POLAR_ENVIRONMENT or "sandbox"
    now = datetime.now(UTC)
    today = now.date().isoformat()

    customers = await _list_polar("/customers/")
    subscriptions = await _list_polar("/subscriptions/")
    orders = await _list_polar("/orders/")

    active_subs = [s for s in subscriptions if s.get("status") == "active"]
    trialing = [s for s in subscriptions if s.get("status") == "trialing"]
    past_due = [s for s in subscriptions if s.get("status") == "past_due"]
    canceled = [s for s in subscriptions if s.get("status") in ("canceled", "unpaid")]

    paid_orders = [o for o in orders if o.get("paid") or o.get("status") == "paid"]
    revenue_cents = 0
    paid_today = []
    revenue_today = 0
    for o in paid_orders:
        amount = o.get("total_amount") or 0
        revenue_cents += amount
        billed = o.get("created_at") or ""
        if billed.startswith(today):
            paid_today.append(o)
            revenue_today += amount

    # Rough MRR: monthly-equivalent of active subscription amounts
    recurring_cents = 0
    for s in active_subs:
        amount = s.get("amount") or 0
        interval = s.get("recurring_interval") or "month"
        interval_count = s.get("recurring_interval_count") or 1
        if interval == "year":
            recurring_cents += amount // (12 * interval_count)
        elif interval == "week":
            recurring_cents += int(amount * 4.33 / interval_count)
        else:
            recurring_cents += amount // interval_count

    tier_counts: dict[str, int] = {}
    for s in active_subs:
        product_id = s.get("product_id") or (s.get("product") or {}).get("id")
        if not product_id:
            continue
        tier = settings.polar_product_tiers.get(product_id, "unknown")
        tier_counts[tier] = tier_counts.get(tier, 0) + 1

    lines = [
        "*Clear skies. Zero friction.*",
        f"*Polar usage report* · {env.upper()} · {now.strftime('%a %d %b %Y %H:%M %Z')}",
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
        "*Revenue (paid orders)*",
        f"• All-time paid: *{len(paid_orders)}* orders · *{_money(revenue_cents)}*",
        f"• Today: *{len(paid_today)}* orders · *{_money(revenue_today)}*",
        f"• Recurring MRR (approx): *{_money(recurring_cents)}*",
        "",
        "*Payouts (money you earn)*",
        "• Payouts run via Stripe Connect — check the Polar dashboard → Finance.",
    ]

    return "\n".join(lines)
