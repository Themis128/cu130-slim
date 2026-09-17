"""Billing endpoints — plans, checkout, portal, subscription, webhooks.

Provider is selected via ``BILLING_PROVIDER`` (``paddle`` | ``polar``).
Paddle uses ``Paddle-Signature`` HMAC verification on ``/webhook``; Polar uses
Standard Webhooks (``webhook-id``/``webhook-timestamp``/``webhook-signature``)
on ``/polar-webhook``. Both are idempotent via the ``billing_events`` table.

All mutating endpoints except the webhooks require team OWNER role.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import DbSession, TeamId, require_team_owner
from app.core.config import get_settings
from app.core.quotas import PLAN_LIMITS
from app.models.billing import BillingEvent
from app.models.user import Team, User
from app.services import dodo_api, paddle_api, polar_api

logger = logging.getLogger(__name__)
router = APIRouter()

# Tiers that may be purchased through checkout.
PURCHASABLE_TIERS = ("pro", "business", "enterprise")

_ACTIVE_SUB_STATUSES = ("active", "trialing", "past_due")


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class CheckoutRequest(BaseModel):
    tier: str  # pro | business | enterprise


class CheckoutResponse(BaseModel):
    transaction_id: str
    checkout_url: str | None


def _provider() -> str:
    return get_settings().billing_provider


def _tier_price_id(tier: str) -> str | None:
    """Provider product/price id for a purchasable tier."""
    s = get_settings()
    if _provider() == "polar":
        return {
            "pro": s.POLAR_PRODUCT_PRO,
            "business": s.POLAR_PRODUCT_BUSINESS,
            "enterprise": s.POLAR_PRODUCT_ENTERPRISE,
        }.get(tier) or None
    if _provider() == "dodo":
        return {
            "pro": s.DODO_PRODUCT_PRO,
            "business": s.DODO_PRODUCT_BUSINESS,
            "enterprise": s.DODO_PRODUCT_ENTERPRISE,
        }.get(tier) or None
    return {
        "pro": s.PADDLE_PRICE_PRO,
        "business": s.PADDLE_PRICE_BUSINESS,
        "enterprise": s.PADDLE_PRICE_ENTERPRISE,
    }.get(tier) or None


def _billing_configured() -> bool:
    provider = _provider()
    if provider == "polar":
        return polar_api.polar_configured()
    if provider == "dodo":
        return dodo_api.dodo_configured()
    return paddle_api.paddle_configured()


def _require_billing() -> None:
    if not _billing_configured():
        provider = _provider()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Billing is not configured ({provider} credentials missing)",
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
    """Frontend bootstrap: provider, client token (Paddle only), tier→price map."""
    s = get_settings()
    provider = _provider()
    configured = _billing_configured()
    is_paddle = provider == "paddle"
    env_map = {"paddle": s.PADDLE_ENVIRONMENT, "polar": s.POLAR_ENVIRONMENT, "dodo": s.DODO_ENVIRONMENT}
    return {
        "provider": provider,
        "configured": configured,
        "environment": env_map.get(provider, s.PADDLE_ENVIRONMENT),
        "team_id": str(team_id),
        "customer_email": current_user.email,
        "client_token": s.PADDLE_CLIENT_TOKEN if (is_paddle and configured) else None,
        "prices": {
            "pro": _tier_price_id("pro"),
            "business": _tier_price_id("business"),
            "enterprise": _tier_price_id("enterprise"),
        },
    }


@router.get("/plans")
async def list_plans(team_id: TeamId, db: DbSession):
    """Public plan catalog — tiers, quota limits, configured price ids."""
    price_for = {t: _tier_price_id(t) for t in PURCHASABLE_TIERS}
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
        "polar_customer_id": team.polar_customer_id,
        "polar_subscription_id": team.polar_subscription_id,
        "dodo_customer_id": team.dodo_customer_id,
        "dodo_subscription_id": team.dodo_subscription_id,
        "provider": _provider(),
    }


@router.post("/checkout", response_model=CheckoutResponse)
async def create_checkout(
    body: CheckoutRequest,
    team_id: TeamId,
    db: DbSession,
    current_user: User = Depends(require_team_owner),
):
    """Create a checkout and return its hosted checkout URL.

    Paddle: transaction + hosted checkout URL (frontend may also use the
    Paddle.js overlay with the ``price_id`` from ``/billing/config``).
    Polar: hosted checkout session URL — the frontend redirects to it.
    Both paths embed the team id so webhooks resolve the team without lookups.
    """
    _require_billing()
    if body.tier not in PURCHASABLE_TIERS:
        raise HTTPException(status_code=400, detail=f"Invalid tier '{body.tier}'")
    price_id = _tier_price_id(body.tier)
    if not price_id:
        raise HTTPException(status_code=400, detail=f"No price configured for tier '{body.tier}'")

    team = await _get_team(team_id, db)
    if _provider() == "polar":
        try:
            txn = await polar_api.create_checkout(
                product_id=price_id,
                team_id=str(team.id),
                customer_id=team.polar_customer_id,
                customer_email=current_user.email,
                customer_name=current_user.name,
            )
        except polar_api.PolarError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return CheckoutResponse(transaction_id=txn["id"], checkout_url=txn["checkout_url"])

    if _provider() == "dodo":
        try:
            txn = await dodo_api.create_checkout(
                product_id=price_id,
                team_id=str(team.id),
                customer_id=team.dodo_customer_id,
                customer_email=current_user.email,
                customer_name=current_user.name,
            )
        except dodo_api.DodoError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return CheckoutResponse(transaction_id=txn["id"], checkout_url=txn["checkout_url"])

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
    """Return a hosted customer-portal session URL (Paddle or Polar)."""
    _require_billing()
    team = await _get_team(team_id, db)
    if _provider() == "polar":
        # Polar portals resolve the customer via its external_id (team UUID).
        # Ensure the customer exists so the portal works pre-checkout too.
        try:
            if not team.polar_customer_id:
                team.polar_customer_id = await polar_api.get_or_create_customer(
                    str(team.id), current_user.email, current_user.name
                )
                await db.commit()
            url = await polar_api.create_portal_session(str(team.id))
        except polar_api.PolarError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"portal_url": url}
    if _provider() == "dodo":
        try:
            if not team.dodo_customer_id:
                team.dodo_customer_id = await dodo_api.get_or_create_customer(
                    str(team.id), current_user.email, current_user.name
                )
                await db.commit()
            url = await dodo_api.create_portal_session(team.dodo_customer_id)
        except dodo_api.DodoError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"portal_url": url}
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
    _require_billing()
    team = await _get_team(team_id, db)
    if _provider() == "polar":
        if not team.polar_subscription_id:
            raise HTTPException(status_code=400, detail="No active subscription")
        try:
            sub = await polar_api.cancel_subscription(team.polar_subscription_id)
        except polar_api.PolarError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        team.subscription_status = "canceled_pending"
        end = _parse_dt(sub.get("current_period_end"))
        if end:
            team.subscription_period_end = end
        await db.commit()
        return {"status": "canceled_pending", "period_end": team.subscription_period_end}

    if _provider() == "dodo":
        if not team.dodo_subscription_id:
            raise HTTPException(status_code=400, detail="No active subscription")
        try:
            sub = await dodo_api.cancel_subscription(team.dodo_subscription_id)
        except dodo_api.DodoError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        team.subscription_status = "canceled_pending"
        end = _parse_dt(sub.get("next_billing_date"))
        if end:
            team.subscription_period_end = end
        await db.commit()
        return {"status": "canceled_pending", "period_end": team.subscription_period_end}

    if not team.paddle_subscription_id:
        raise HTTPException(status_code=400, detail="No active subscription")
    try:
        sub = await paddle_api.cancel_subscription(team.paddle_subscription_id)
    except paddle_api.PaddleError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    team.subscription_status = "canceled_pending"
    end = _parse_dt((sub.get("current_billing_period") or {}).get("ends_at"))
    if end:
        team.subscription_period_end = end
    await db.commit()
    return {"status": "canceled_pending", "period_end": team.subscription_period_end}


@router.post("/sync")
async def sync_subscription(
    team_id: TeamId,
    db: DbSession,
    current_user: User = Depends(require_team_owner),
):
    """Pull latest subscription state from the provider and reconcile."""
    _require_billing()
    team = await _get_team(team_id, db)
    if _provider() == "polar":
        if not team.polar_subscription_id:
            raise HTTPException(status_code=400, detail="No Polar subscription linked")
        try:
            sub = await polar_api.get_subscription(team.polar_subscription_id)
        except polar_api.PolarError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        _apply_polar_subscription(team, sub)
        await db.commit()
        return {
            "plan_tier": team.plan_tier,
            "subscription_status": team.subscription_status,
            "subscription_period_end": team.subscription_period_end,
        }

    if _provider() == "dodo":
        if not team.dodo_subscription_id:
            raise HTTPException(status_code=400, detail="No Dodo subscription linked")
        try:
            sub = await dodo_api.get_subscription(team.dodo_subscription_id)
        except dodo_api.DodoError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        _apply_dodo_subscription(team, sub)
        await db.commit()
        return {
            "plan_tier": team.plan_tier,
            "subscription_status": team.subscription_status,
            "subscription_period_end": team.subscription_period_end,
        }

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
    end = _parse_dt((sub.get("current_billing_period") or {}).get("ends_at"))
    if end:
        team.subscription_period_end = end
    items = sub.get("items") or []
    price_id = ((items[0].get("price") or {}).get("id")) if items else None
    if price_id:
        tier = paddle_api.tier_for_price(price_id)
        if tier and sub.get("status") in _ACTIVE_SUB_STATUSES:
            team.plan_tier = tier
    if sub.get("status") in ("canceled", "expired"):
        team.plan_tier = "free"


def _apply_polar_subscription(team: Team, sub: dict) -> None:
    """Map a Polar subscription object onto the team row."""
    team.polar_subscription_id = sub.get("id", team.polar_subscription_id)
    customer_id = sub.get("customer_id") or (sub.get("customer") or {}).get("id")
    if customer_id:
        team.polar_customer_id = customer_id
    status_ = sub.get("status", team.subscription_status)
    if sub.get("cancel_at_period_end") and status_ in _ACTIVE_SUB_STATUSES:
        status_ = "canceled_pending"
    team.subscription_status = status_
    end = _parse_dt(sub.get("current_period_end") or sub.get("ends_at"))
    if end:
        team.subscription_period_end = end
    product_id = sub.get("product_id") or (sub.get("product") or {}).get("id")
    if product_id:
        tier = polar_api.tier_for_product(product_id)
        if tier and sub.get("status") in _ACTIVE_SUB_STATUSES:
            team.plan_tier = tier
    if sub.get("status") in ("canceled", "unpaid") or sub.get("ended_at"):
        team.plan_tier = "free"


_DM_META_KEYS: dict[str, str] = {
    "twitter": "twitter_auto_reply",
    "tiktok": "tiktok_auto_reply",
    "instagram": "instagram_auto_reply",
    "linkedin": "linkedin_auto_reply",
    "threads": "threads_auto_reply",
    "telegram": "telegram_auto_reply",
    "whatsapp": "whatsapp_auto_reply",
}


async def _sync_dm_auto_reply(db: AsyncSession | None, team: Team, paid: bool) -> None:
    """Enable/disable DM auto-reply on all the team's accounts per plan tier.

    Called after subscription lifecycle changes: a paid plan activation
    turns the feature on, a downgrade to free turns it off.
    """
    if db is None:
        return
    from sqlalchemy.orm.attributes import flag_modified

    from app.models.social_account import SocialAccount

    result = await db.execute(
        select(SocialAccount).where(SocialAccount.team_id == team.id)
    )
    for account in result.scalars():
        meta = dict(account.meta_data or {})
        if account.platform == "facebook":
            key = (
                "personal_messenger_auto_reply"
                if account.account_type == "user"
                else "messenger_auto_reply"
            )
        else:
            key = _DM_META_KEYS.get(account.platform)
        if not key:
            continue
        config = meta.get(key) or {}
        if config.get("enabled") == paid:
            continue
        config["enabled"] = paid
        meta[key] = config
        account.meta_data = meta
        flag_modified(account, "meta_data")


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

    await _sync_dm_auto_reply(db, team, paid=team.plan_tier != "free")


def _apply_dodo_subscription(team: Team, sub: dict) -> None:
    """Map a Dodo subscription object onto the team row."""
    team.dodo_subscription_id = sub.get("subscription_id") or sub.get("id") or team.dodo_subscription_id
    customer_id = (sub.get("customer") or {}).get("customer_id") or sub.get("customer_id")
    if customer_id:
        team.dodo_customer_id = customer_id
    status_ = sub.get("status", team.subscription_status)
    if sub.get("cancel_at_next_billing_date") and status_ == "active":
        status_ = "canceled_pending"
    team.subscription_status = status_
    end = _parse_dt(sub.get("next_billing_date") or sub.get("expires_at"))
    if end:
        team.subscription_period_end = end
    product_id = sub.get("product_id")
    if product_id:
        tier = dodo_api.tier_for_product(product_id)
        if tier and sub.get("status") in ("active", "on_hold"):
            team.plan_tier = tier
    if sub.get("status") in ("cancelled", "expired", "failed"):
        team.plan_tier = "free"


# ---------------------------------------------------------------------------
# Polar.sh (Standard Webhooks)
# ---------------------------------------------------------------------------


async def _find_team_for_polar_event(db: AsyncSession, data: dict) -> Team | None:
    """Resolve the team for a Polar event.

    Checkout-scoped identifiers (metadata.team_id, external_customer_id)
    take precedence over the customer-level external_id: Polar merges
    customers by email, so one Polar customer can serve several teams.
    """
    candidates = [
        (data.get("metadata") or {}).get("team_id"),
        data.get("external_customer_id"),
        (data.get("customer") or {}).get("external_id"),
    ]
    for cand in candidates:
        if not cand:
            continue
        try:
            team = (
                await db.execute(select(Team).where(Team.id == uuid.UUID(str(cand))))
            ).scalar_one_or_none()
            if team:
                return team
        except (ValueError, TypeError):
            continue
    customer_id = data.get("customer_id") or (data.get("customer") or {}).get("id")
    if customer_id:
        return (
            await db.execute(select(Team).where(Team.polar_customer_id == customer_id))
        ).scalar_one_or_none()
    return None


@router.post("/polar-webhook")
async def polar_webhook(request: Request, db: DbSession):
    """Polar notification webhook — Standard Webhooks verified, idempotent.

    Configure the endpoint (via Polar dashboard or ``POST /v1/webhooks/endpoints``)
    to ``POST /api/v1/billing/polar-webhook`` and set ``POLAR_WEBHOOK_SECRET``.
    The ``webhook-id`` header is the delivery id — stable across retries — and
    serves as the idempotency key in ``billing_events``.
    """
    raw = await request.body()
    event_id = request.headers.get("webhook-id", "")
    if not polar_api.verify_webhook_signature(
        raw,
        event_id,
        request.headers.get("webhook-timestamp", ""),
        request.headers.get("webhook-signature", ""),
    ):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        event = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON") from None

    event_type = event.get("type", "unknown")
    data = event.get("data") or {}

    existing = (
        await db.execute(select(BillingEvent).where(BillingEvent.event_id == event_id))
    ).scalar_one_or_none()
    if existing:
        return {"status": "duplicate", "event_id": event_id}

    row = BillingEvent(event_id=event_id, event_type=event_type, payload=event)
    db.add(row)
    team = await _find_team_for_polar_event(db, data)
    if team:
        row.team_id = team.id

    try:
        if event_type.startswith("subscription."):
            await _handle_polar_subscription_event(db, team, event_type, data)
        elif event_type == "order.paid":
            # A paid order carrying a subscription links it to the team.
            if team and data.get("subscription_id"):
                team.polar_subscription_id = data["subscription_id"]
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        row.error = str(exc)[:2000]
        await db.commit()
        logger.exception("polar webhook %s (%s) failed", event_type, event_id)
        raise HTTPException(status_code=500, detail="Webhook processing failed") from exc

    return {"status": "processed", "event_type": event_type}


async def _handle_polar_subscription_event(
    db: AsyncSession, team: Team | None, event_type: str, data: dict
) -> None:
    if not team:
        logger.warning("polar webhook %s: no team resolved", event_type)
        return

    # Ignore lifecycle events for a different subscription than the one the
    # team is currently tracked on — activation-type events for a new
    # subscription still apply (upgrade path).
    sub_id = data.get("id") or data.get("subscription_id")
    activating = event_type in (
        "subscription.created",
        "subscription.active",
        "subscription.resumed",
        "subscription.uncanceled",
    ) or (
        event_type == "subscription.updated"
        and data.get("status") in _ACTIVE_SUB_STATUSES
    )
    if (
        team.polar_subscription_id
        and sub_id
        and sub_id != team.polar_subscription_id
        and not activating
    ):
        logger.info(
            "polar webhook %s: ignoring event for non-current subscription %s",
            event_type, sub_id,
        )
        return

    customer_id = data.get("customer_id") or (data.get("customer") or {}).get("id")
    if customer_id:
        team.polar_customer_id = customer_id

    if event_type in (
        "subscription.created",
        "subscription.active",
        "subscription.updated",
        "subscription.resumed",
        "subscription.uncanceled",
    ):
        _apply_polar_subscription(team, data)
        if event_type == "subscription.active":
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
    elif event_type in ("subscription.canceled", "subscription.revoked"):
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

    await _sync_dm_auto_reply(db, team, paid=team.plan_tier != "free")


# ---------------------------------------------------------------------------
# Dodo Payments (Standard Webhooks)
# ---------------------------------------------------------------------------


async def _find_team_for_dodo_event(db: AsyncSession, data: dict) -> Team | None:
    """Resolve the team for a Dodo event via metadata.team_id → customer_id → email."""
    team_id = (data.get("metadata") or {}).get("team_id")
    if team_id:
        try:
            team = (
                await db.execute(select(Team).where(Team.id == uuid.UUID(str(team_id))))
            ).scalar_one_or_none()
            if team:
                return team
        except (ValueError, TypeError):
            pass
    customer_id = data.get("customer_id") or (data.get("customer") or {}).get("customer_id")
    if customer_id:
        team = (
            await db.execute(select(Team).where(Team.dodo_customer_id == customer_id))
        ).scalar_one_or_none()
        if team:
            return team
    email = (data.get("customer") or {}).get("email")
    if email:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if user:
            return (
                await db.execute(select(Team).where(Team.owner_id == user.id))
            ).scalar_one_or_none()
    return None


@router.post("/dodo-webhook")
async def dodo_webhook(request: Request, db: DbSession):
    """Dodo notification webhook — Standard Webhooks verified, idempotent.

    Configure under Dodo Dashboard → Developer → Webhooks with
    ``POST /api/v1/billing/dodo-webhook`` and set ``DODO_WEBHOOK_SECRET``.
    The ``webhook-id`` header is the delivery id and serves as the
    idempotency key in ``billing_events``.
    """
    raw = await request.body()
    event_id = request.headers.get("webhook-id", "")
    if not dodo_api.verify_webhook_signature(
        raw,
        event_id,
        request.headers.get("webhook-timestamp", ""),
        request.headers.get("webhook-signature", ""),
    ):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        event = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON") from None

    event_type = event.get("type", "unknown")
    data = event.get("data") or {}

    existing = (
        await db.execute(select(BillingEvent).where(BillingEvent.event_id == event_id))
    ).scalar_one_or_none()
    if existing:
        return {"status": "duplicate", "event_id": event_id}

    row = BillingEvent(event_id=event_id, event_type=event_type, payload=event)
    db.add(row)
    team = await _find_team_for_dodo_event(db, data)
    if team:
        row.team_id = team.id

    try:
        if event_type.startswith("subscription."):
            await _handle_dodo_subscription_event(db, team, event_type, data)
        elif event_type == "payment.succeeded":
            # A paid subscription payment links the subscription to the team.
            if team and data.get("subscription_id"):
                team.dodo_subscription_id = data["subscription_id"]
            if team and data.get("customer_id"):
                team.dodo_customer_id = data["customer_id"]
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        row.error = str(exc)[:2000]
        await db.commit()
        logger.exception("dodo webhook %s (%s) failed", event_type, event_id)
        raise HTTPException(status_code=500, detail="Webhook processing failed") from exc

    return {"status": "processed", "event_type": event_type}


async def _handle_dodo_subscription_event(
    db: AsyncSession, team: Team | None, event_type: str, data: dict
) -> None:
    if not team:
        logger.warning("dodo webhook %s: no team resolved", event_type)
        return

    # Ignore lifecycle events for a different subscription than the one the
    # team is currently tracked on (e.g. a second checkout that failed must
    # not downgrade an active plan). Activation-type events for a new
    # subscription still apply — that's the upgrade path.
    sub_id = data.get("subscription_id") or data.get("id")
    activating = event_type in (
        "subscription.active",
        "subscription.renewed",
        "subscription.unpaused",
        "subscription.plan_changed",
    ) or (
        event_type == "subscription.updated"
        and data.get("status") in _ACTIVE_SUB_STATUSES
    )
    if (
        team.dodo_subscription_id
        and sub_id
        and sub_id != team.dodo_subscription_id
        and not activating
    ):
        logger.info(
            "dodo webhook %s: ignoring event for non-current subscription %s",
            event_type, sub_id,
        )
        return

    customer_id = (data.get("customer") or {}).get("customer_id") or data.get("customer_id")
    if customer_id:
        team.dodo_customer_id = customer_id

    if event_type in (
        "subscription.active",
        "subscription.updated",
        "subscription.renewed",
        "subscription.unpaused",
        "subscription.plan_changed",
    ):
        _apply_dodo_subscription(team, data)
        if event_type == "subscription.active":
            await _notify_team_owner(
                db, team,
                f"SocialAuto {team.plan_tier} subscription active",
                f"Your {team.plan_tier} plan is now active.",
                "billing_activated",
            )
    elif event_type in ("subscription.on_hold", "subscription.past_due"):
        # Renewal payment failed — recoverable via payment-method update.
        # past_due = grace period open, access kept until deadline.
        team.subscription_status = "past_due" if event_type == "subscription.past_due" else "on_hold"
        await _notify_team_owner(
            db, team,
            "SocialAuto payment failed",
            "Your subscription payment failed. Update your payment method to keep your plan.",
            "billing_past_due",
        )
    elif event_type in ("subscription.cancelled", "subscription.expired"):
        team.subscription_status = data.get("status", "cancelled")
        team.plan_tier = "free"
        await _notify_team_owner(
            db, team,
            "SocialAuto subscription ended",
            "Your subscription has ended and the team is now on the free plan.",
            "billing_canceled",
        )
    elif event_type == "subscription.failed":
        # Terminal: mandate creation failed — never grant the tier.
        team.subscription_status = "failed"
    elif event_type == "subscription.paused":
        team.subscription_status = "paused"

    await _sync_dm_auto_reply(db, team, paid=team.plan_tier != "free")
