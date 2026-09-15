"""Paddle Billing endpoints — plans, checkout, portal, subscription, webhooks.

Public/env-driven; all mutating endpoints except ``/webhook`` require team
OWNER role. The webhook verifies ``Paddle-Signature`` (HMAC-SHA256) and is
idempotent via the ``billing_events`` table.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import DbSession, TeamId, require_team_owner
from app.core.config import get_settings
from app.core.quotas import PLAN_LIMITS
from app.models.billing import BillingEvent
from app.models.user import Team, User
from app.services import paddle_api

logger = logging.getLogger(__name__)
router = APIRouter()

# Tiers that may be purchased through Paddle checkout.
PURCHASABLE_TIERS = ("pro", "business", "enterprise")


class CheckoutRequest(BaseModel):
    tier: str  # pro | business | enterprise


class CheckoutResponse(BaseModel):
    transaction_id: str
    checkout_url: str | None


def _tier_price_id(tier: str) -> str | None:
    s = get_settings()
    return {
        "pro": s.PADDLE_PRICE_PRO,
        "business": s.PADDLE_PRICE_BUSINESS,
        "enterprise": s.PADDLE_PRICE_ENTERPRISE,
    }.get(tier) or None


def _require_paddle() -> None:
    if not paddle_api.paddle_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Billing is not configured (PADDLE_API_KEY / PADDLE_CLIENT_TOKEN missing)",
        )


async def _get_team(team_id: uuid.UUID, db: AsyncSession) -> Team:
    team = (await db.execute(select(Team).where(Team.id == team_id))).scalar_one_or_none()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    return team


@router.get("/config")
async def billing_config(
    team_id: TeamId,
    db: DbSession,
    current_user: User = Depends(require_team_owner),
):
    """Frontend bootstrap: Paddle.js token, environment, tier→price map."""
    s = get_settings()
    return {
        "configured": paddle_api.paddle_configured(),
        "environment": s.PADDLE_ENVIRONMENT,
        "team_id": str(team_id),
        "customer_email": current_user.email,
        "client_token": s.PADDLE_CLIENT_TOKEN if paddle_api.paddle_configured() else None,
        "prices": {
            "pro": s.PADDLE_PRICE_PRO or None,
            "business": s.PADDLE_PRICE_BUSINESS or None,
            "enterprise": s.PADDLE_PRICE_ENTERPRISE or None,
        },
    }


@router.get("/plans")
async def list_plans(team_id: TeamId, db: DbSession):
    """Public plan catalog — tiers, quota limits, configured price ids."""
    s = get_settings()
    price_for = {"pro": s.PADDLE_PRICE_PRO, "business": s.PADDLE_PRICE_BUSINESS, "enterprise": s.PADDLE_PRICE_ENTERPRISE}
    return {
        "plans": [
            {
                "tier": tier,
                "limits": limits,
                "purchasable": tier in PURCHASABLE_TIERS and bool(price_for.get(tier)),
                "price_id": price_for.get(tier) or None,
            }
            for tier, limits in PLAN_LIMITS.items()
        ]
    }


@router.get("/subscription")
async def get_subscription(
    team_id: TeamId,
    db: DbSession,
):
    """Current team's billing state."""
    team = await _get_team(team_id, db)
    return {
        "plan_tier": team.plan_tier,
        "subscription_status": team.subscription_status,
        "subscription_period_end": team.subscription_period_end,
        "paddle_customer_id": team.paddle_customer_id,
        "paddle_subscription_id": team.paddle_subscription_id,
    }


@router.post("/checkout", response_model=CheckoutResponse)
async def create_checkout(
    body: CheckoutRequest,
    team_id: TeamId,
    db: DbSession,
    current_user: User = Depends(require_team_owner),
):
    """Create a Paddle transaction and return its hosted checkout URL.

    The frontend may also use Paddle.js overlay with the ``price_id`` from
    ``/billing/config``; both paths set ``custom_data.team_id`` so the webhook
    resolves the team without extra lookups.
    """
    _require_paddle()
    if body.tier not in PURCHASABLE_TIERS:
        raise HTTPException(status_code=400, detail=f"Invalid tier '{body.tier}'")
    price_id = _tier_price_id(body.tier)
    if not price_id:
        raise HTTPException(status_code=400, detail=f"No Paddle price configured for tier '{body.tier}'")

    team = await _get_team(team_id, db)
    try:
        customer_id = team.paddle_customer_id or await paddle_api.get_or_create_customer(
            str(team.id), current_user.email, current_user.name
        )
        if not team.paddle_customer_id:
            team.paddle_customer_id = customer_id
            await db.commit()
        txn = await paddle_api.create_checkout_transaction(
            price_id=price_id,
            team_id=str(team.id),
            customer_id=customer_id,
        )
    except paddle_api.PaddleError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return CheckoutResponse(transaction_id=txn["id"], checkout_url=txn["checkout_url"])


@router.post("/portal")
async def customer_portal(
    team_id: TeamId,
    db: DbSession,
    current_user: User = Depends(require_team_owner),
):
    """Return a Paddle hosted customer-portal session URL."""
    _require_paddle()
    team = await _get_team(team_id, db)
    if not team.paddle_customer_id:
        raise HTTPException(status_code=400, detail="No Paddle customer for this team")
    try:
        url = await paddle_api.create_portal_session(team.paddle_customer_id, team.paddle_subscription_id)
    except paddle_api.PaddleError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"portal_url": url}


@router.post("/cancel")
async def cancel_subscription(
    team_id: TeamId,
    db: DbSession,
    current_user: User = Depends(require_team_owner),
):
    """Cancel at period end. Tier stays until the period lapses."""
    _require_paddle()
    team = await _get_team(team_id, db)
    if not team.paddle_subscription_id:
        raise HTTPException(status_code=400, detail="No active subscription")
    try:
        sub = await paddle_api.cancel_subscription(team.paddle_subscription_id)
    except paddle_api.PaddleError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    team.subscription_status = "canceled_pending"
    period = (sub.get("current_billing_period") or {}).get("ends_at")
    if period:
        from datetime import datetime

        team.subscription_period_end = datetime.fromisoformat(period.replace("Z", "+00:00"))
    await db.commit()
    return {"status": "canceled_pending", "period_end": team.subscription_period_end}


@router.post("/sync")
async def sync_subscription(
    team_id: TeamId,
    db: DbSession,
    current_user: User = Depends(require_team_owner),
):
    """Pull latest subscription state from Paddle and reconcile the team row."""
    _require_paddle()
    team = await _get_team(team_id, db)
    if not team.paddle_subscription_id:
        raise HTTPException(status_code=400, detail="No Paddle subscription linked")
    try:
        sub = await paddle_api.get_subscription(team.paddle_subscription_id)
    except paddle_api.PaddleError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    _apply_subscription(team, sub)
    await db.commit()
    return {
        "plan_tier": team.plan_tier,
        "subscription_status": team.subscription_status,
        "subscription_period_end": team.subscription_period_end,
    }


def _apply_subscription(team: Team, sub: dict) -> None:
    """Map a Paddle subscription object onto the team row."""
    team.paddle_subscription_id = sub.get("id", team.paddle_subscription_id)
    team.subscription_status = sub.get("status", team.subscription_status)
    period = (sub.get("current_billing_period") or {}).get("ends_at")
    if period:
        from datetime import datetime

        team.subscription_period_end = datetime.fromisoformat(period.replace("Z", "+00:00"))
    items = sub.get("items") or []
    price_id = ((items[0].get("price") or {}).get("id")) if items else None
    if price_id:
        tier = paddle_api.tier_for_price(price_id)
        if tier and sub.get("status") in ("active", "trialing", "past_due"):
            team.plan_tier = tier
    if sub.get("status") in ("canceled", "expired"):
        team.plan_tier = "free"


async def _notify_team_owner(db: AsyncSession, team: Team, subject: str, text: str, template: str) -> None:
    """Best-effort billing email to the team owner."""
    try:
        owner = (await db.execute(select(User).where(User.id == team.owner_id))).scalar_one_or_none()
        if not owner:
            return
        from app.services.email_digest import send_email

        await send_email(subject=subject, text_body=text, to_addrs=[owner.email])
    except Exception as exc:  # noqa: BLE001
        logger.warning("billing email '%s' failed for team %s: %s", template, team.id, exc)


async def _find_team_for_event(db: AsyncSession, data: dict) -> Team | None:
    """Resolve the team for a Paddle event via custom_data → customer_id."""
    team_id = (data.get("custom_data") or {}).get("team_id")
    if team_id:
        try:
            team = (await db.execute(select(Team).where(Team.id == uuid.UUID(team_id)))).scalar_one_or_none()
            if team:
                return team
        except (ValueError, TypeError):
            pass
    customer_id = data.get("customer_id")
    if customer_id:
        return (
            await db.execute(select(Team).where(Team.paddle_customer_id == customer_id))
        ).scalar_one_or_none()
    return None


@router.post("/webhook")
async def paddle_webhook(request: Request, db: DbSession):
    """Paddle notification webhook — signature-verified, idempotent.

    Configure the notification destination in Paddle to
    ``POST /api/v1/billing/webhook`` and set ``PADDLE_WEBHOOK_SECRET``.
    """
    raw = await request.body()
    sig = request.headers.get("Paddle-Signature", "")
    if not paddle_api.verify_webhook_signature(raw, sig):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        event = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON") from None

    event_id = event.get("event_id", "")
    event_type = event.get("event_type", "unknown")
    data = event.get("data") or {}

    # Idempotency — replayed deliveries short-circuit
    existing = (
        await db.execute(select(BillingEvent).where(BillingEvent.event_id == event_id))
    ).scalar_one_or_none()
    if existing:
        return {"status": "duplicate", "event_id": event_id}

    row = BillingEvent(event_id=event_id, event_type=event_type, payload=event)
    db.add(row)
    team = await _find_team_for_event(db, data)
    if team:
        row.team_id = team.id

    try:
        if event_type.startswith("subscription."):
            await _handle_subscription_event(db, team, event_type, data)
        elif event_type in ("transaction.completed", "transaction.paid"):
            # A paid transaction carrying a subscription activates it implicitly
            if team and data.get("subscription_id"):
                team.paddle_subscription_id = data["subscription_id"]
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        row.error = str(exc)[:2000]
        await db.commit()
        logger.exception("billing webhook %s (%s) failed", event_type, event_id)
        raise HTTPException(status_code=500, detail="Webhook processing failed") from exc

    return {"status": "processed", "event_type": event_type}


async def _handle_subscription_event(
    db: AsyncSession, team: Team | None, event_type: str, data: dict
) -> None:
    if not team:
        logger.warning("billing webhook %s: no team resolved", event_type)
        return

    if data.get("customer_id"):
        team.paddle_customer_id = data["customer_id"]

    if event_type in (
        "subscription.created",
        "subscription.activated",
        "subscription.updated",
        "subscription.resumed",
    ):
        _apply_subscription(team, data)
        if event_type == "subscription.activated":
            await _notify_team_owner(
                db, team,
                f"SocialAuto {team.plan_tier} subscription active",
                f"Your {team.plan_tier} plan is now active.",
                "billing_activated",
            )
    elif event_type == "subscription.past_due":
        team.subscription_status = "past_due"
        await _notify_team_owner(
            db, team,
            "SocialAuto payment failed",
            "Your subscription payment failed. Update your payment method to keep your plan.",
            "billing_past_due",
        )
    elif event_type in ("subscription.canceled", "subscription.expired"):
        team.subscription_status = data.get("status", "canceled")
        team.plan_tier = "free"
        await _notify_team_owner(
            db, team,
            "SocialAuto subscription ended",
            "Your subscription has ended and the team is now on the free plan.",
            "billing_canceled",
        )
    elif event_type == "subscription.paused":
        team.subscription_status = "paused"
