"""Prometheus instrumentation: HTTP request metrics + business vitals.

Exposes /metrics (scraped by the omv kube-prom stack via a headless
Service/Endpoints + ServiceMonitor pointing at the workstation LAN IP).
Business gauges are rebuilt from cheap GROUP BY counts on every scrape —
prometheus-client Gauges are cleared first so vanished label sets don't
go stale.
"""
import time

import structlog
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, Info, generate_latest
from sqlalchemy import text
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import get_settings
from app.db.session import async_session_maker

logger = structlog.get_logger()

# ── HTTP metrics (updated by middleware) ─────────────────────────────
HTTP_REQUESTS = Counter(
    "socialauto_http_requests_total",
    "HTTP requests by method, route template and status code",
    ["method", "path", "status"],
)
HTTP_LATENCY = Histogram(
    "socialauto_http_request_duration_seconds",
    "HTTP request latency by method and route template",
    ["method", "path"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)

# ── Business vitals (rebuilt per scrape) ─────────────────────────────
USERS = Gauge("socialauto_users", "Registered users", ["state"])
TEAMS = Gauge("socialauto_teams", "Teams by plan tier", ["plan_tier"])
SUBSCRIPTIONS = Gauge(
    "socialauto_subscriptions", "Teams by subscription status", ["status"]
)
SOCIAL_ACCOUNTS = Gauge(
    "socialauto_social_accounts", "Connected social accounts", ["platform", "status"]
)
POSTS = Gauge("socialauto_posts", "Posts by status", ["status"])
QUEUE = Gauge("socialauto_publish_queue", "Publish queue items by status", ["status"])
MEDIA = Gauge("socialauto_media_assets", "Media assets in library")
BILLING_EVENTS = Gauge("socialauto_billing_events", "Billing webhook events", ["type"])
BILLING_ERRORS = Gauge(
    "socialauto_billing_events_errors", "Billing events recorded with an error"
)
AI_CALLS = Gauge("socialauto_ai_calls_24h", "AI calls last 24h", ["provider"])
AI_ERRORS = Gauge("socialauto_ai_errors_24h", "Failed AI calls last 24h")
AI_COST = Gauge(
    "socialauto_ai_estimated_cost_usd_24h", "Estimated AI cost last 24h (USD)", ["provider"]
)
AI_NEURONS = Gauge(
    "socialauto_ai_estimated_neurons_24h", "Estimated Workers-AI neurons last 24h", ["provider"]
)
REFRESH_TS = Gauge(
    "socialauto_business_metrics_timestamp_seconds",
    "Unix time of the last successful business-metric refresh",
)
APP_INFO = Info("socialauto_app", "Build info")

_settings = get_settings()
APP_INFO.info({"app": _settings.APP_NAME, "version": _settings.APP_VERSION})


class PrometheusMiddleware:
    """Pure-ASGI middleware recording request count + latency per route."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] == "/metrics":
            await self.app(scope, receive, send)
            return

        method = scope["method"]
        start = time.perf_counter()
        status = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            # After routing, scope["route"] holds the matched APIRoute/Mount;
            # its .path is the template (/api/v1/posts/{id}) — bounded cardinality.
            route = scope.get("route")
            path = getattr(route, "path", None) or "unmatched"
            elapsed = time.perf_counter() - start
            HTTP_REQUESTS.labels(method=method, path=path, status=status).inc()
            HTTP_LATENCY.labels(method=method, path=path).observe(elapsed)


async def refresh_business_metrics() -> None:
    """Rebuild business gauges from DB counts. Fails soft — a broken DB
    must not break the scrape (Prometheus still gets HTTP/process metrics)."""
    try:
        async with async_session_maker() as db:
            row = (
                await db.execute(
                    text(
                        "SELECT count(*) AS total, "
                        "count(*) FILTER (WHERE two_factor_enabled) AS twofa FROM users"
                    )
                )
            ).one()
            USERS.labels(state="total").set(row.total)
            USERS.labels(state="two_factor_enabled").set(row.twofa)

            TEAMS.clear()
            for tier, n in (
                await db.execute(text("SELECT plan_tier, count(*) FROM teams GROUP BY plan_tier"))
            ).all():
                TEAMS.labels(plan_tier=tier).set(n)

            SUBSCRIPTIONS.clear()
            for status, n in (
                await db.execute(
                    text("SELECT subscription_status, count(*) FROM teams GROUP BY subscription_status")
                )
            ).all():
                SUBSCRIPTIONS.labels(status=status).set(n)

            SOCIAL_ACCOUNTS.clear()
            for platform, status, n in (
                await db.execute(
                    text(
                        "SELECT platform, status, count(*) FROM social_accounts "
                        "GROUP BY platform, status"
                    )
                )
            ).all():
                SOCIAL_ACCOUNTS.labels(platform=platform, status=status).set(n)

            POSTS.clear()
            for status, n in (
                await db.execute(text("SELECT status::text, count(*) FROM posts GROUP BY status"))
            ).all():
                POSTS.labels(status=status).set(n)

            QUEUE.clear()
            for status, n in (
                await db.execute(
                    text("SELECT status::text, count(*) FROM publish_queue GROUP BY status")
                )
            ).all():
                QUEUE.labels(status=status).set(n)

            MEDIA.set(
                (await db.execute(text("SELECT count(*) FROM media_assets"))).scalar_one()
            )

            BILLING_EVENTS.clear()
            for etype, n in (
                await db.execute(
                    text("SELECT event_type, count(*) FROM billing_events GROUP BY event_type")
                )
            ).all():
                BILLING_EVENTS.labels(type=etype).set(n)

            BILLING_ERRORS.set(
                (
                    await db.execute(
                        text("SELECT count(*) FROM billing_events WHERE error IS NOT NULL")
                    )
                ).scalar_one()
            )

            AI_CALLS.clear()
            AI_COST.clear()
            AI_NEURONS.clear()
            for provider, calls, cost, neurons in (
                await db.execute(
                    text(
                        "SELECT provider, count(*) AS calls, "
                        "coalesce(sum(estimated_cost),0) AS cost, "
                        "coalesce(sum(estimated_neurons),0) AS neurons "
                        "FROM ai_usage_logs WHERE created_at > now() - interval '24 hours' "
                        "GROUP BY provider"
                    )
                )
            ).all():
                AI_CALLS.labels(provider=provider).set(calls)
                AI_COST.labels(provider=provider).set(float(cost))
                AI_NEURONS.labels(provider=provider).set(float(neurons))

            AI_ERRORS.set(
                (
                    await db.execute(
                        text(
                            "SELECT count(*) FROM ai_usage_logs "
                            "WHERE created_at > now() - interval '24 hours' AND success = false"
                        )
                    )
                ).scalar_one()
            )

            REFRESH_TS.set(time.time())
    except Exception as exc:  # noqa: BLE001
        logger.warning("business metrics refresh failed", error=str(exc))


def metrics_response() -> bytes:
    return generate_latest()


__all__ = [
    "CONTENT_TYPE_LATEST",
    "APP_INFO",
    "PrometheusMiddleware",
    "metrics_response",
    "refresh_business_metrics",
]
