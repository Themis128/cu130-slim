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
QUEUE_PLATFORM = Gauge(
    "socialauto_publish_queue_platform",
    "Publish queue items by platform and status",
    ["platform", "status"],
)
PUBLISHED_7D = Gauge(
    "socialauto_posts_published_7d",
    "Publish targets completed in the last 7 days, by platform",
    ["platform"],
)
FAILURES_7D = Gauge(
    "socialauto_publish_failures_7d",
    "Publish targets failed in the last 7 days, by platform",
    ["platform"],
)
FOLLOWERS = Gauge(
    "socialauto_followers_latest",
    "Latest follower snapshot per connected account",
    ["platform", "account"],
)
TOKEN_EXPIRY = Gauge(
    "socialauto_token_expires_in_seconds",
    "Seconds until the stored OAuth token expires (negative = already expired)",
    ["platform", "account"],
)
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
HTTP_ROUTE = Gauge(
    "socialauto_http_route",
    "Registered HTTP route — lists every endpoint so dashboards can show zero-traffic routes",
    ["method", "path"],
)
# endpoint handler → full route path (incl. include prefixes), filled by
# register_route_metrics; used by the middleware for stable path labels.
_ENDPOINT_PATHS: dict = {}
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
            # After routing, scope["endpoint"] is the handler — map it to the
            # full template path (/api/v1/posts/{id}) via _ENDPOINT_PATHS.
            # Fallback: the matched route's own (leaf) path. Bounded either way.
            route = scope.get("route")
            path = (
                _ENDPOINT_PATHS.get(scope.get("endpoint"))
                or getattr(route, "path", None)
                or "unmatched"
            )
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

            QUEUE_PLATFORM.clear()
            for platform, status, n in (
                await db.execute(
                    text(
                        "SELECT sa.platform, pq.status::text, count(*) "
                        "FROM publish_queue pq "
                        "JOIN social_accounts sa ON sa.id = pq.social_account_id "
                        "GROUP BY sa.platform, pq.status"
                    )
                )
            ).all():
                QUEUE_PLATFORM.labels(platform=platform, status=status).set(n)

            PUBLISHED_7D.clear()
            FAILURES_7D.clear()
            for platform, status, n in (
                await db.execute(
                    text(
                        "SELECT sa.platform, pq.status::text, count(*) "
                        "FROM publish_queue pq "
                        "JOIN social_accounts sa ON sa.id = pq.social_account_id "
                        "WHERE pq.status IN ('completed', 'failed') "
                        "AND coalesce(pq.locked_at, pq.created_at) "
                        "    > now() - interval '7 days' "
                        "GROUP BY sa.platform, pq.status"
                    )
                )
            ).all():
                if status == "completed":
                    PUBLISHED_7D.labels(platform=platform).set(n)
                elif status == "failed":
                    FAILURES_7D.labels(platform=platform).set(n)

            FOLLOWERS.clear()
            for platform, account, followers in (
                await db.execute(
                    text(
                        "SELECT DISTINCT ON (fs.social_account_id) "
                        "fs.platform, "
                        "coalesce(sa.username, sa.account_id, fs.social_account_id::text), "
                        "fs.followers "
                        "FROM follower_snapshots fs "
                        "JOIN social_accounts sa ON sa.id = fs.social_account_id "
                        "ORDER BY fs.social_account_id, fs.captured_at DESC"
                    )
                )
            ).all():
                FOLLOWERS.labels(platform=platform, account=account).set(followers)

            TOKEN_EXPIRY.clear()
            for platform, account, ttl in (
                await db.execute(
                    text(
                        "SELECT platform, "
                        "coalesce(username, account_id, id::text), "
                        "EXTRACT(EPOCH FROM token_expires_at - now()) "
                        "FROM social_accounts WHERE token_expires_at IS NOT NULL"
                    )
                )
            ).all():
                TOKEN_EXPIRY.labels(platform=platform, account=account).set(float(ttl))

            REFRESH_TS.set(time.time())
    except Exception as exc:  # noqa: BLE001
        logger.warning("business metrics refresh failed", error=str(exc))


def register_route_metrics(app) -> None:
    """Emit one gauge series per registered route (full path incl. include
    prefixes) so dashboards can enumerate every endpoint even when it has
    received no traffic yet. Also fills _ENDPOINT_PATHS for the middleware."""
    from fastapi.routing import APIRoute
    from starlette.routing import Route

    def _walk(routes, prefix=""):
        for route in routes:
            if isinstance(route, APIRoute | Route):
                yield prefix, route
            ctx = getattr(route, "include_context", None)
            nested = prefix + (getattr(ctx, "prefix", "") or "")
            for attr in ("routes", "original_router"):
                sub = getattr(route, attr, None)
                if sub is not None:
                    yield from _walk(getattr(sub, "routes", sub), nested)

    for prefix, route in _walk(app.routes):
        full_path = prefix + route.path
        _ENDPOINT_PATHS[route.endpoint] = full_path
        for method in sorted(route.methods or ()):
            HTTP_ROUTE.labels(method=method, path=full_path).set(1)


def metrics_response() -> bytes:
    return generate_latest()


__all__ = [
    "CONTENT_TYPE_LATEST",
    "APP_INFO",
    "PrometheusMiddleware",
    "metrics_response",
    "refresh_business_metrics",
    "register_route_metrics",
]
