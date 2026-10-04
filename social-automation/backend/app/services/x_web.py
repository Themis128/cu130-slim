"""Free X (Twitter) web fallback — cookie-authenticated x.com GraphQL client.

Used ONLY when the official X API v2 refuses with 402 ``credits-depleted`` /
``UsageCapExceeded`` / quota-type 429 and ``X_WEB_FALLBACK_ENABLED`` is true.

- Posting + primary reads: `tweety <https://github.com/mahrtayyab/tweety>`_
  (``tweety-ns``, pinned to a commit on main in pyproject.toml).
- Secondary reads (metrics): `twscrape <https://github.com/vladkens/twscrape>`_
  (read-only).

Auth is cookie-only (``X_WEB_AUTH_TOKEN`` + ``X_WEB_CT0``, optionally
``X_WEB_COOKIES_JSON``) — this module never performs a password login.

Safety guards (shared by every worker through Redis, file fallback):

- circuit breaker: 401/403/429, locked/suspended/challenge/"palm"/transaction-id
  errors trip it for ``X_WEB_BREAKER_HOURS`` (default 6h) and alert Slack;
- max ``X_WEB_MAX_POSTS_PER_DAY`` posts per rolling 24h (default 5);
- randomized gap of ``X_WEB_MIN_GAP_MINUTES``..``X_WEB_MAX_GAP_MINUTES``
  (default 10–30 min) between posts;
- analytics polled at most every ``X_WEB_ANALYTICS_MIN_INTERVAL_HOURS`` (6h).

No likes / follows / DMs are automated here — posting and reads only.
Web automation is against X's ToS; the account can be locked or suspended.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import logging
import os
import random
import re
import shutil
import tempfile
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from app.core.config import get_settings
from app.core.log_sanitize import sanitize_log_text

# twscrape ships opt-out PostHog telemetry — force it off before any import.
os.environ["TWS_TELEMETRY"] = "0"

logger = logging.getLogger(__name__)

# ── Media validation ──────────────────────────────────────────────────────────

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".avif", ".bmp", ".tif", ".tiff"}
GIF_EXTS = {".gif"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v"}
MAX_IMAGES = 4
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_GIF_BYTES = 15 * 1024 * 1024
MAX_VIDEO_BYTES = 512 * 1024 * 1024


class XWebMediaError(ValueError):
    """Media set cannot be posted correctly — the post must fail, not go out without it."""


@dataclasses.dataclass
class MediaPlan:
    kind: str  # "text" | "images" | "gif" | "video"
    paths: list[str]

    @property
    def has_media(self) -> bool:
        return bool(self.paths)


def _sniff(path: str) -> str | None:
    """Classify a file by its magic bytes: image | gif | video | None."""
    with open(path, "rb") as fh:
        head = fh.read(32)
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return "gif"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        # HEIF/AVIF stills share the ISO-BMFF container with MP4/MOV.
        if brand in (b"heic", b"heix", b"hevc", b"heim", b"heis", b"mif1", b"msf1", b"avif", b"avis"):
            return "image"
        return "video"
    if head[4:8] in (b"moov", b"mdat", b"wide", b"free"):
        return "video"
    if (
        head.startswith(b"\xff\xd8\xff")
        or head.startswith(b"\x89PNG\r\n\x1a\n")
        or (head.startswith(b"RIFF") and head[8:12] == b"WEBP")
        or head.startswith(b"BM")
        or head[:4] in (b"II*\x00", b"MM\x00*")
    ):
        return "image"
    return None


def plan_media(media_paths: list[str] | None) -> MediaPlan:
    """Validate the post's media set against X's rules.

    X accepts per tweet: up to 4 images, OR exactly 1 GIF, OR exactly 1
    video. Anything else (unknown types, PDFs, mixed sets, missing/empty
    files, extension/content mismatch) raises ``XWebMediaError`` — callers
    must fail the post instead of publishing without (or with wrong) media.
    """
    paths = [p for p in (media_paths or []) if p]
    if not paths:
        return MediaPlan(kind="text", paths=[])

    kinds: list[str] = []
    for p in paths:
        name = os.path.basename(p)
        if not os.path.isfile(p):
            raise XWebMediaError(f"media file missing on disk: {name}")
        size = os.path.getsize(p)
        if size <= 0:
            raise XWebMediaError(f"media file is empty: {name}")
        ext = os.path.splitext(p)[1].lower()
        if ext in IMAGE_EXTS:
            declared = "image"
        elif ext in GIF_EXTS:
            declared = "gif"
        elif ext in VIDEO_EXTS:
            declared = "video"
        else:
            raise XWebMediaError(f"unsupported media type for X: {name} (allowed: images, GIF, MP4/MOV)")
        actual = _sniff(p)
        if actual != declared:
            raise XWebMediaError(f"media content does not match its type: {name} looks like {actual or 'unknown data'}, expected {declared}")
        if declared == "gif" and size > MAX_GIF_BYTES:
            raise XWebMediaError(f"GIF too large for X ({size // (1024 * 1024)}MB > 15MB): {name}")
        if declared == "video" and size > MAX_VIDEO_BYTES:
            raise XWebMediaError(f"video too large for X ({size // (1024 * 1024)}MB > 512MB): {name}")
        kinds.append(declared)

    unique = set(kinds)
    if unique == {"image"}:
        if len(paths) > MAX_IMAGES:
            raise XWebMediaError(f"X allows at most {MAX_IMAGES} images per post; this post has {len(paths)}")
        return MediaPlan(kind="images", paths=paths)
    if len(paths) == 1 and unique == {"gif"}:
        return MediaPlan(kind="gif", paths=paths)
    if len(paths) == 1 and unique == {"video"}:
        return MediaPlan(kind="video", paths=paths)
    raise XWebMediaError(f"X allows up to 4 images OR one GIF OR one video per post; this post mixes {', '.join(sorted(kinds))}")


def _to_jpeg(src: str, dest_dir: str, index: int) -> str:
    """Re-encode an image as a baseline RGB JPEG under X's 5MB photo limit."""
    from PIL import Image, ImageOps

    with contextlib.suppress(Exception):  # optional HEIC/HEIF support
        import pillow_heif  # type: ignore[import-not-found]

        pillow_heif.register_heif_opener()
    with contextlib.suppress(Exception):  # optional AVIF support
        import pillow_avif  # type: ignore[import-not-found]  # noqa: F401

    ext = os.path.splitext(src)[1].lower()
    dest = os.path.join(dest_dir, f"img{index}.jpg")
    if ext in (".jpg", ".jpeg") and os.path.getsize(src) <= MAX_IMAGE_BYTES:
        with Image.open(src) as probe:
            baseline_rgb = probe.format == "JPEG" and probe.mode in ("RGB", "L") and not probe.info.get("progressive") and not probe.info.get("progression")
        if baseline_rgb:
            # Already a small baseline RGB JPEG — copy under a lowercase
            # extension (tweety derives the MIME type from it, case-sensitively).
            shutil.copyfile(src, dest)
            return dest

    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        elif im.mode != "RGB":
            im = im.convert("RGB")
        if max(im.size) > 4096:
            im.thumbnail((4096, 4096))
        for quality in (92, 85, 75, 65, 55):
            im.save(dest, "JPEG", quality=quality, optimize=True, progressive=False)
            if os.path.getsize(dest) <= MAX_IMAGE_BYTES:
                return dest
    raise XWebMediaError(f"could not re-encode image under 5MB: {os.path.basename(src)}")


def prepare_media(plan: MediaPlan, dest_dir: str) -> list[str]:
    """Materialize upload-ready files (JPEG for photos, lowercase extensions)."""
    out: list[str] = []
    for i, p in enumerate(plan.paths):
        if plan.kind == "images":
            try:
                out.append(_to_jpeg(p, dest_dir, i))
            except XWebMediaError:
                raise
            except Exception as exc:  # noqa: BLE001 — surface as media failure
                raise XWebMediaError(f"image conversion failed for {os.path.basename(p)}: {exc}") from exc
        else:
            ext = os.path.splitext(p)[1].lower()
            dest = os.path.join(dest_dir, f"media{i}{ext}")
            try:
                os.symlink(p, dest)
            except OSError:
                shutil.copyfile(p, dest)
            out.append(dest)
    return out


# ── Error classification (circuit breaker triggers) ───────────────────────────

_TRIP_STATUS = {401, 403, 429}
# X error codes: 32 bad auth, 64 suspended, 88 rate limit, 89 bad token,
# 141 inactive, 185 over status limit, 215 bad auth data, 226 automated
# request, 326 locked, 344 daily limit, 353 CSRF (stale ct0), 399 flow failure.
_TRIP_CODES = {32, 64, 88, 89, 141, 185, 215, 226, 326, 344, 353, 399}
_TRIP_CLASSES = {
    "LockedAccount",
    "SuspendedAccount",
    "InvalidCredentials",
    "DeniedLogin",
    "ActionRequired",
    "ArkoseLoginRequired",
    "AuthenticationRequired",
    "RateLimitReached",
    "CaptchaSolverFailed",
    "NoAccountError",  # twscrape: pool marked the account inactive/locked
}
_TRIP_MARKERS = (
    "locked",
    "suspended",
    "challenge",
    "palm",
    "transaction",
    "captcha",
    "arkose",
    "automated",
    "unusual activity",
    "rate limit",
    "too many requests",
    "could not authenticate",
    "couldn't authenticate",
    "bad authentication",
    "unauthorized",
    "forbidden",
    "denied",
)


def _status_of(exc: BaseException) -> int | None:
    for attr in ("status_code", "status"):
        val = getattr(exc, attr, None)
        if isinstance(val, int):
            return val
    resp = getattr(exc, "response", None)
    val = getattr(resp, "status_code", None)
    return val if isinstance(val, int) else None


def classify_x_web_error(exc: BaseException) -> str | None:
    """Return a breaker reason when ``exc`` signals account/anti-abuse trouble."""
    name = type(exc).__name__
    status = _status_of(exc)
    code = getattr(exc, "error_code", None)
    try:
        code_int = int(code) if code is not None else None
    except (TypeError, ValueError):
        code_int = None
    msg = str(exc)[:300]
    low = msg.lower()
    if name in _TRIP_CLASSES:
        return f"{name}: {msg}"
    if status in _TRIP_STATUS:
        return f"HTTP {status}: {msg}"
    if code_int is not None and (code_int in _TRIP_CODES or code_int in _TRIP_STATUS):
        return f"X error {code_int}: {msg}"
    for marker in _TRIP_MARKERS:
        if marker in low:
            return f"{name}: {msg}"
    return None


# Breaker reasons that are pure capacity limits (429 / codes 88 rate limit,
# 185 over status limit, 344 daily limit) — transient, unlike auth/lock/
# challenge/suspension reasons which need a human.
_CAPACITY_REASON_MARKERS = (
    "http 429",
    "x error 88:",
    "x error 185:",
    "x error 344:",
    "x error 429:",
    "ratelimitreached",
    "rate limit",
    "too many requests",
)
_NON_CAPACITY_REASON_MARKERS = (
    "locked",
    "suspended",
    "challenge",
    "captcha",
    "arkose",
    "automated",
    "unusual activity",
    "authenticat",
    "unauthorized",
    "denied",
)


def is_capacity_reason(reason: str | None) -> bool:
    """True when a breaker reason is a rate/daily limit, not account trouble."""
    low = (reason or "").lower()
    if any(m in low for m in _NON_CAPACITY_REASON_MARKERS):
        return False
    return any(m in low for m in _CAPACITY_REASON_MARKERS)


# ── Persistent guard state (breaker + rate limits) ────────────────────────────


class GuardStore(Protocol):
    def lock(self) -> contextlib.AbstractAsyncContextManager[None]:
        """Exclusive lock around a load → mutate → save cycle."""

    async def load(self) -> dict[str, Any]:
        """Return the current guard state document ({} when absent)."""

    async def save(self, state: dict[str, Any]) -> None:
        """Persist the guard state document."""


class MemoryGuardStore:
    """In-process store (tests / last-resort fallback)."""

    def __init__(self) -> None:
        self.state: dict[str, Any] = {}

    @contextlib.asynccontextmanager
    async def lock(self) -> AsyncIterator[None]:
        yield

    async def load(self) -> dict[str, Any]:
        return json.loads(json.dumps(self.state))

    async def save(self, state: dict[str, Any]) -> None:
        self.state = json.loads(json.dumps(state))


class FileGuardStore:
    """JSON file + flock — used when Redis is unreachable."""

    def __init__(self, path: str) -> None:
        self.path = path

    @contextlib.asynccontextmanager
    async def lock(self) -> AsyncIterator[None]:
        import fcntl

        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path + ".lock", "a+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    async def load(self) -> dict[str, Any]:
        try:
            with open(self.path) as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    async def save(self, state: dict[str, Any]) -> None:
        tmp = f"{self.path}.tmp"
        with open(tmp, "w") as fh:
            json.dump(state, fh)
        os.replace(tmp, self.path)


class RedisGuardStore:
    """Single JSON document in Redis, mutated under a Redis lock."""

    KEY = "x_web:guard"
    LOCK_KEY = "x_web:guard:lock"

    def __init__(self, client: Any) -> None:
        self.client = client

    @contextlib.asynccontextmanager
    async def lock(self) -> AsyncIterator[None]:
        async with self.client.lock(self.LOCK_KEY, timeout=30, blocking_timeout=30):
            yield

    async def load(self) -> dict[str, Any]:
        raw = await self.client.get(self.KEY)
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    async def save(self, state: dict[str, Any]) -> None:
        # 3-day TTL: every field is time-bounded well below that.
        await self.client.set(self.KEY, json.dumps(state), ex=3 * 24 * 3600)


async def default_guard_store() -> GuardStore:
    settings = get_settings()
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        await client.ping()
        return RedisGuardStore(client)
    except Exception as exc:  # noqa: BLE001 — degrade to file persistence
        logger.warning("[x_web] Redis unavailable for guard state (%s) — using %s", type(exc).__name__, settings.X_WEB_STATE_FILE)
        return FileGuardStore(settings.X_WEB_STATE_FILE)


@dataclasses.dataclass
class SlotDecision:
    ok: bool
    reason: str | None = None
    retry_after: datetime | None = None
    breaker_open: bool = False
    permanent: bool = False


def _fmt_ts(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%d %H:%M UTC")


class XWebGuard:
    """Circuit breaker + posting/polling limits for the X web session."""

    def __init__(
        self,
        store: GuardStore,
        *,
        now: Callable[[], float] = time.time,
        rng: random.Random | None = None,
        alert: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self.store = store
        self._now = now
        self._rng = rng or random.Random()
        self._alert = alert

    @staticmethod
    def _settings() -> Any:
        return get_settings()

    async def breaker_state(self) -> tuple[bool, float | None, str | None]:
        state = await self.store.load()
        until = float(state.get("breaker_until") or 0)
        if until > self._now():
            return True, until, state.get("breaker_reason")
        return False, None, None

    async def trip(self, reason: str) -> bool:
        """Open the breaker. Returns True when it was closed before (alert once)."""
        reason = sanitize_log_text(reason, 300)
        hours = float(self._settings().X_WEB_BREAKER_HOURS)
        until = self._now() + hours * 3600
        async with self.store.lock():
            state = await self.store.load()
            was_open = float(state.get("breaker_until") or 0) > self._now()
            state["breaker_until"] = max(until, float(state.get("breaker_until") or 0))
            state["breaker_reason"] = reason[:300]
            state["breaker_tripped_at"] = self._now()
            await self.store.save(state)
        logger.error("[x_web] circuit breaker OPEN until %s: %s", _fmt_ts(until), reason[:300])
        if not was_open and self._alert is not None:
            try:
                await self._alert(
                    "*X web fallback paused (circuit breaker tripped)*\n"
                    f"• Reason: {reason[:300]}\n"
                    f"• Paused until: {_fmt_ts(until)} ({hours:g}h)\n"
                    "• Check the @account on x.com in a normal browser (lock/challenge/suspension), "
                    "refresh X_WEB_AUTH_TOKEN / X_WEB_CT0 if the session expired, then retry the failed posts."
                )
            except Exception:  # noqa: BLE001 — alerting is best-effort
                logger.warning("[x_web] breaker alert failed", exc_info=True)
        return not was_open

    async def reserve_post_slot(self, tweets: int = 1) -> SlotDecision:
        """Atomically check breaker/daily cap/min gap and reserve ``tweets`` slots.

        Every tweet of a thread counts against the rolling-24h cap; the
        min gap applies between posts (a thread's replies go out together
        with a short randomized pause, see ``THREAD_REPLY_DELAY_S``).
        """
        tweets = max(1, int(tweets))
        s = self._settings()
        max_per_day = max(0, int(s.X_WEB_MAX_POSTS_PER_DAY))
        gap_min = max(0.0, float(s.X_WEB_MIN_GAP_MINUTES))
        gap_max = max(gap_min, float(s.X_WEB_MAX_GAP_MINUTES))
        async with self.store.lock():
            state = await self.store.load()
            now = self._now()
            until = float(state.get("breaker_until") or 0)
            if until > now:
                return SlotDecision(
                    ok=False,
                    breaker_open=True,
                    retry_after=datetime.fromtimestamp(until, UTC),
                    reason=f"X web circuit breaker open until {_fmt_ts(until)} ({state.get('breaker_reason') or 'unknown reason'})",
                )
            posts = sorted(float(t) for t in state.get("post_times", []) if now - float(t) < 24 * 3600)
            if tweets > max_per_day:
                return SlotDecision(
                    ok=False,
                    permanent=True,
                    reason=f"X web: thread of {tweets} tweets exceeds X_WEB_MAX_POSTS_PER_DAY={max_per_day}",
                )
            if len(posts) + tweets > max_per_day:
                # Slot frees when enough of the oldest posts age out of 24h.
                need = len(posts) + tweets - max_per_day
                retry = posts[need - 1] + 24 * 3600
                return SlotDecision(
                    ok=False,
                    retry_after=datetime.fromtimestamp(retry, UTC),
                    reason=f"X web daily cap reached ({len(posts)}/{max_per_day} posts in 24h); next slot {_fmt_ts(retry)}",
                )
            next_at = float(state.get("next_post_at") or 0)
            if next_at > now:
                return SlotDecision(
                    ok=False,
                    retry_after=datetime.fromtimestamp(next_at, UTC),
                    reason=f"X web minimum gap between posts; next slot {_fmt_ts(next_at)}",
                )
            posts.extend([now] * tweets)
            state["post_times"] = posts
            state["next_post_at"] = now + self._rng.uniform(gap_min, gap_max) * 60
            await self.store.save(state)
            return SlotDecision(ok=True)

    async def reserve_analytics_slot(self, key: str = "default") -> SlotDecision:
        """Per-account polling floor (``key`` = X handle / user id)."""
        s = self._settings()
        interval = max(0.0, float(s.X_WEB_ANALYTICS_MIN_INTERVAL_HOURS)) * 3600
        async with self.store.lock():
            state = await self.store.load()
            now = self._now()
            until = float(state.get("breaker_until") or 0)
            if until > now:
                return SlotDecision(ok=False, breaker_open=True, reason=f"X web circuit breaker open until {_fmt_ts(until)}")
            polls = state.get("analytics_last")
            polls = polls if isinstance(polls, dict) else {}
            last = float(polls.get(key) or 0)
            if now - last < interval:
                return SlotDecision(ok=False, reason=f"X web analytics for {key} polled at {_fmt_ts(last)}; min interval {interval / 3600:g}h")
            polls[key] = now
            state["analytics_last"] = polls
            await self.store.save(state)
            return SlotDecision(ok=True)


async def _slack_alert(text: str) -> None:
    from app.services.slack_notifications import post_alert_to_slack

    await post_alert_to_slack(text[:2000])


async def default_guard() -> XWebGuard:
    return XWebGuard(await default_guard_store(), alert=_slack_alert)


# ── Cookies / configuration ───────────────────────────────────────────────────


def load_cookies() -> dict[str, str]:
    """Merge X_WEB_COOKIES_JSON with X_WEB_AUTH_TOKEN / X_WEB_CT0 (explicit vars win)."""
    s = get_settings()
    cookies: dict[str, str] = {}
    raw = (s.X_WEB_COOKIES_JSON or "").strip()
    if raw:
        try:
            data = json.loads(raw)
        except ValueError:
            logger.warning("[x_web] X_WEB_COOKIES_JSON is not valid JSON — ignored")
            data = None
        if isinstance(data, dict):
            cookies.update({str(k): str(v) for k, v in data.items() if v is not None})
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("name") and item.get("value") is not None:
                    cookies[str(item["name"])] = str(item["value"])
    if (s.X_WEB_AUTH_TOKEN or "").strip():
        cookies["auth_token"] = s.X_WEB_AUTH_TOKEN.strip()
    if (s.X_WEB_CT0 or "").strip():
        cookies["ct0"] = s.X_WEB_CT0.strip()
    return cookies


def is_configured() -> bool:
    """Feature flag on AND both required cookies present."""
    if not get_settings().X_WEB_FALLBACK_ENABLED:
        return False
    c = load_cookies()
    return bool(c.get("auth_token") and c.get("ct0"))


def _cookie_header(cookies: dict[str, str]) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


def _patch_tweety_home_fallback() -> None:
    """Work around mahrtayyab/tweety#301 until the fix lands upstream.

    X's Rolldown-based ``x-web`` frontend no longer serves the ``ondemand.s``
    webpack chunk map on ``/?mx=2`` or ``/home`` (both now render
    ``entry-client-logged-out``), so ``TransactionGenerator`` cannot extract the
    animation key indices and every tweety request dies with
    ``Couldn't get animation key indices``. ``https://x.com/i/jf/`` still serves
    the legacy responsive-web shell with the manifest — retry it when the
    upstream fallbacks come back without one.
    """
    try:
        import bs4
        from tweety.http import Request
        from tweety.transaction import find_on_demand_file
    except Exception:
        return

    if getattr(Request.get_home_html, "_x_web_i_jf_patched", False):
        return
    original = Request.get_home_html

    async def patched(self: Any) -> Any:
        home_page = await original(self)
        if home_page is not None and not find_on_demand_file(str(home_page)):
            headers = self._get_request_headers()
            headers.pop("authorization", None)
            response = await self._session.request(method="GET", url="https://x.com/i/jf/", headers=headers)
            if response.status_code in range(200, 300):
                candidate = bs4.BeautifulSoup(response.content, "lxml")
                if find_on_demand_file(str(candidate)):
                    return candidate
        return home_page

    patched._x_web_i_jf_patched = True  # type: ignore[attr-defined]
    Request.get_home_html = patched


async def _default_tweety_client() -> Any:
    """Build a tweety client from cookies (in-memory session, never on disk)."""
    from tweety import TwitterAsync
    from tweety.session import MemorySession

    _patch_tweety_home_fallback()
    proxy = (get_settings().X_WEB_PROXY or "").strip() or None
    app = TwitterAsync(MemorySession(), proxy=proxy)
    await app.load_cookies(load_cookies())
    return app


TweetyFactory = Callable[[], Awaitable[Any]]


# ── Publishing ────────────────────────────────────────────────────────────────


@dataclasses.dataclass
class XWebOutcome:
    """Result of an x_web publish attempt.

    status:
      ok          — posted (tweet_ids[0] is the head of the thread)
      unavailable — flag off / cookies missing (caller continues the chain)
      deferred    — daily cap, min-gap or rate-limit trip; retry at ``retry_after``
      breaker     — breaker already open; caller defers to ``retry_after``
      tripped     — this attempt tripped the breaker; fail + alert
      media_error — media invalid or upload failed; fail, never post without it
      identity    — cookies belong to a different account; fail
      too_long    — thread has more tweets than the daily cap allows; fail
      ambiguous   — create_tweet crossed the publish boundary without a
                    confirmed id and reconciliation found nothing; fail
                    without retry (a retry could double-post)
      error       — other pre-publish (transient) error; caller may try the
                    next provider
    """

    status: str
    tweet_ids: list[str] = dataclasses.field(default_factory=list)
    error: str | None = None
    retry_after: datetime | None = None
    partial: bool = False

    @property
    def first_id(self) -> str | None:
        return self.tweet_ids[0] if self.tweet_ids else None


def _tweet_id(obj: Any) -> str | None:
    tid = getattr(obj, "id", None)
    if tid is None and isinstance(obj, dict):
        tid = obj.get("id") or obj.get("rest_id")
    tid = str(tid) if tid is not None else ""
    return tid if re.fullmatch(r"\d{1,30}", tid) else None


# Pause between the replies of one thread (seconds, randomized). Short on
# purpose: the whole thread must finish inside one Celery task.
THREAD_REPLY_DELAY_S = (5.0, 15.0)


def _norm_text(text: str) -> str:
    """Compare tweet texts ignoring URLs (t.co-wrapped) and whitespace."""
    return re.sub(r"\s+", " ", re.sub(r"https?://\S+", "", text or "")).strip().lower()


async def _find_recent_own_tweet(client: Any, text: str) -> str | None:
    """Reconcile an unconfirmed create_tweet against the account's timeline.

    Returns the tweet id when a matching tweet from the last 15 minutes is
    found, else None. Raises when the timeline cannot be read.
    """
    user = getattr(client, "user", None)
    ident = getattr(user, "id", None) or getattr(user, "username", None)
    timeline = await client.get_tweets(ident, pages=1)
    want = _norm_text(text)[:120]
    now = datetime.now(UTC)
    for item in getattr(timeline, "tweets", None) or list(timeline or []):
        for t in getattr(item, "tweets", None) or [item]:
            created = getattr(t, "created_on", None) or getattr(t, "date", None)
            if isinstance(created, datetime):
                created = created if created.tzinfo else created.replace(tzinfo=UTC)
                if (now - created).total_seconds() > 15 * 60:
                    continue
            got = _norm_text(str(getattr(t, "text", "") or ""))
            if want and got.startswith(want[:80]):
                return _tweet_id(t)
    return None


async def publish_via_x_web(
    *,
    expected_username: str | None,
    chunks: list[str],
    media_paths: list[str] | None,
    guard: XWebGuard | None = None,
    client_factory: TweetyFactory | None = None,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> XWebOutcome:
    """Post a tweet (or reply-chain thread) with media through tweety."""
    if not is_configured():
        return XWebOutcome(status="unavailable", error="X web fallback disabled or cookies not set")
    chunks = [c for c in chunks if c and c.strip()]
    try:
        plan = plan_media(media_paths)
    except XWebMediaError as exc:
        return XWebOutcome(status="media_error", error=f"X web: invalid media — {exc}")
    if not chunks and not plan.has_media:
        return XWebOutcome(status="error", error="X web: nothing to post (empty text, no media)")

    guard = guard or await default_guard()
    decision = await guard.reserve_post_slot(max(1, len(chunks)))
    if not decision.ok:
        if decision.permanent:
            return XWebOutcome(status="too_long", error=decision.reason)
        return XWebOutcome(
            status="breaker" if decision.breaker_open else "deferred",
            error=decision.reason,
            retry_after=decision.retry_after,
        )

    factory = client_factory or _default_tweety_client
    workdir = tempfile.mkdtemp(prefix="x_web_")
    posted: list[str] = []
    stage = "login"
    client: Any = None
    try:
        client = await factory()
        stage = "identity"
        expected = (expected_username or "").lstrip("@").lower()
        actual = str(getattr(getattr(client, "user", None), "username", "") or "").lstrip("@").lower()
        if expected and actual != expected:
            return XWebOutcome(
                status="identity",
                error=(
                    f"X web cookies belong to @{actual or 'unknown'}, expected @{expected} — export cookies from a browser logged in as the correct account"
                ),
            )

        uploaded: list[Any] = []
        if plan.has_media:
            stage = "media"
            try:
                files = prepare_media(plan, workdir)
            except XWebMediaError as exc:
                return XWebOutcome(status="media_error", error=f"X web: {exc}")
            uploaded = list(await client.upload_media(files) or [])
            missing = [u for u in uploaded if not getattr(u, "media_id", None)]
            if len(uploaded) != len(files) or missing:
                return XWebOutcome(
                    status="media_error",
                    error=f"X web: media upload incomplete ({len(uploaded) - len(missing)}/{len(files)} uploaded) — not posting without media",
                )

        stage = "post"
        first = await client.create_tweet(chunks[0] if chunks else "", files=uploaded or None)
        first_id = _tweet_id(first)
        if not first_id:
            raise RuntimeError("create_tweet returned no tweet id")
        posted.append(first_id)
        for chunk in chunks[1:]:
            stage = "thread"
            await sleep(random.uniform(*THREAD_REPLY_DELAY_S))
            reply = await client.create_tweet(chunk, reply_to=posted[-1])
            rid = _tweet_id(reply)
            if not rid:
                raise RuntimeError("thread reply returned no tweet id")
            posted.append(rid)
        return XWebOutcome(status="ok", tweet_ids=posted)
    except Exception as exc:  # noqa: BLE001 — classify every upstream failure
        reason = classify_x_web_error(exc)
        if reason:
            await guard.trip(reason)
        detail = sanitize_log_text(f"X web {stage} failed: {type(exc).__name__}: {exc}", 360)
        if posted:
            # Head tweet is live — never re-post; report the incomplete thread.
            logger.warning("[x_web] thread incomplete after %d/%d tweets: %s", len(posted), len(chunks), detail)
            return XWebOutcome(status="ok", tweet_ids=posted, error=detail, partial=True)
        if reason:
            # An explicit X rejection (auth/lock/limit) — nothing was posted.
            hours = float(get_settings().X_WEB_BREAKER_HOURS)
            if is_capacity_reason(reason):
                # Pure rate/daily-limit rejection: the breaker still pauses
                # the session (and alerts), but the post itself is deferred
                # to the breaker reopen time instead of failing permanently.
                return XWebOutcome(
                    status="deferred",
                    error=f"{detail} — X web rate limited; circuit breaker paused for {hours:g}h",
                    retry_after=datetime.now(UTC) + timedelta(hours=hours),
                )
            return XWebOutcome(status="tripped", error=f"{detail} — circuit breaker tripped for {hours:g}h")
        if stage == "post":
            # The request crossed the publish boundary: X may have accepted
            # the tweet even though we saw an error / no id. Reconcile before
            # anything (another provider, a retry) could post it again.
            try:
                found = await _find_recent_own_tweet(client, chunks[0] if chunks else "")
            except Exception as rexc:  # noqa: BLE001
                found = None
                detail += f"; reconcile failed: {type(rexc).__name__}"
            if found:
                logger.warning("[x_web] create_tweet errored but tweet %s is live — treating as posted", found)
                return XWebOutcome(status="ok", tweet_ids=[found], error=detail, partial=len(chunks) > 1)
            return XWebOutcome(
                status="ambiguous",
                error=(
                    f"{detail} — the tweet may or may not be live; check x.com/{(expected_username or '').lstrip('@')} "
                    "before retrying (not retried automatically to avoid a duplicate post)"
                ),
            )
        if stage == "media" or type(exc).__name__ == "UploadFailed":
            return XWebOutcome(status="media_error", error=f"{detail} — not posting without media")
        return XWebOutcome(status="error", error=detail)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ── Analytics (reads) ─────────────────────────────────────────────────────────


@dataclasses.dataclass
class XWebTweetMetrics:
    id: str
    created_at: datetime | None = None
    views: int = 0
    likes: int = 0
    replies: int = 0
    retweets: int = 0
    quotes: int = 0
    bookmarks: int = 0
    text: str = ""


@dataclasses.dataclass
class XWebAnalytics:
    source: str  # "x_web_tweety" | "x_web_twscrape"
    followers: int | None = None
    following: int | None = None
    tweet_count: int | None = None
    listed: int | None = None
    tweets: dict[str, XWebTweetMetrics] = dataclasses.field(default_factory=dict)
    errors: list[str] = dataclasses.field(default_factory=list)


def _int(val: Any) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        try:
            return int(float(str(val).replace(",", "")))
        except (TypeError, ValueError):
            return 0


def _from_tweety_tweet(t: Any) -> XWebTweetMetrics | None:
    tid = _tweet_id(t)
    if not tid:
        return None
    created = getattr(t, "created_on", None) or getattr(t, "date", None)
    return XWebTweetMetrics(
        id=tid,
        created_at=created if isinstance(created, datetime) else None,
        views=_int(getattr(t, "views", 0)),
        likes=_int(getattr(t, "likes", 0)),
        replies=_int(getattr(t, "reply_counts", 0)),
        retweets=_int(getattr(t, "retweet_counts", 0)),
        quotes=_int(getattr(t, "quote_counts", 0)),
        bookmarks=_int(getattr(t, "bookmark_count", 0)),
        text=str(getattr(t, "text", "") or "")[:280],
    )


def _from_twscrape_tweet(t: Any) -> XWebTweetMetrics | None:
    tid = _tweet_id(t) or (str(getattr(t, "id_str", "")) or None)
    if not tid:
        return None
    created = getattr(t, "date", None)
    return XWebTweetMetrics(
        id=tid,
        created_at=created if isinstance(created, datetime) else None,
        views=_int(getattr(t, "viewCount", 0)),
        likes=_int(getattr(t, "likeCount", 0)),
        replies=_int(getattr(t, "replyCount", 0)),
        retweets=_int(getattr(t, "retweetCount", 0)),
        quotes=_int(getattr(t, "quoteCount", 0)),
        bookmarks=_int(getattr(t, "bookmarkedCount", 0)),
        text=str(getattr(t, "rawContent", "") or "")[:280],
    )


async def _read_with_tweety(client: Any, username: str, user_id: str | None, wanted: list[str], max_lookups: int) -> XWebAnalytics:
    out = XWebAnalytics(source="x_web_tweety")
    user = await client.get_user_info(username)
    out.followers = _int(getattr(user, "followers_count", 0))
    out.following = _int(getattr(user, "friends_count", 0))
    out.tweet_count = _int(getattr(user, "statuses_count", 0))
    out.listed = _int(getattr(user, "listed_count", 0))
    timeline = await client.get_tweets(user_id or username, pages=1)
    for item in getattr(timeline, "tweets", None) or list(timeline or []):
        for t in getattr(item, "tweets", None) or [item]:  # SelfThread → tweets
            m = _from_tweety_tweet(t)
            if m:
                out.tweets[m.id] = m
    for tid in [w for w in wanted if w not in out.tweets][:max_lookups]:
        t = await client.tweet_detail(tid)
        m = _from_tweety_tweet(t)
        if m:
            out.tweets[m.id] = m
    return out


async def _default_twscrape_api(username: str) -> Any:
    """twscrape API over a throwaway SQLite pool (cookies never outlive the run)."""
    from twscrape import API  # type: ignore[import-untyped]

    workdir = tempfile.mkdtemp(prefix="x_web_tws_")
    os.chmod(workdir, 0o700)
    proxy = (get_settings().X_WEB_PROXY or "").strip() or None
    api = API(os.path.join(workdir, "accounts.db"), proxy=proxy, raise_when_no_account=True)
    api._x_web_workdir = workdir  # removed by fetch_x_web_analytics
    await api.pool.add_account_cookies(username, _cookie_header(load_cookies()))
    return api


async def _read_with_twscrape(api: Any, username: str, user_id: str | None, wanted: list[str], max_lookups: int) -> XWebAnalytics:
    out = XWebAnalytics(source="x_web_twscrape")
    user = await api.user_by_login(username)
    if user is None:
        raise RuntimeError(f"twscrape: user @{username} not found")
    out.followers = _int(getattr(user, "followersCount", 0))
    out.following = _int(getattr(user, "friendsCount", 0))
    out.tweet_count = _int(getattr(user, "statusesCount", 0))
    out.listed = _int(getattr(user, "listedCount", 0))
    uid = int(user_id) if user_id and str(user_id).isdigit() else int(getattr(user, "id", 0) or 0)
    if uid:
        async for t in api.user_tweets(uid, limit=40):
            m = _from_twscrape_tweet(t)
            if m:
                out.tweets[m.id] = m
    for tid in [w for w in wanted if w not in out.tweets][:max_lookups]:
        t = await api.tweet_details(int(tid))
        m = _from_twscrape_tweet(t) if t is not None else None
        if m:
            out.tweets[m.id] = m
    return out


async def fetch_x_web_analytics(
    *,
    username: str,
    user_id: str | None,
    wanted_tweet_ids: list[str] | None = None,
    guard: XWebGuard | None = None,
    tweety_factory: TweetyFactory | None = None,
    twscrape_factory: Callable[[str], Awaitable[Any]] | None = None,
) -> tuple[XWebAnalytics | None, str | None]:
    """Read account + tweet metrics via tweety, then twscrape as secondary.

    Returns ``(analytics, skip_reason)``. ``analytics`` is None when the
    fallback is disabled, the breaker is open, the 6h polling interval has
    not elapsed, or both readers failed (reason explains which).
    """
    if not is_configured():
        return None, "x_web disabled"
    handle = (username or "").lstrip("@")
    if not handle:
        return None, "x_web: account has no username"
    guard = guard or await default_guard()
    decision = await guard.reserve_analytics_slot(handle.lower())
    if not decision.ok:
        return None, decision.reason
    max_lookups = max(0, int(get_settings().X_WEB_ANALYTICS_MAX_TWEET_LOOKUPS))
    wanted = [w for w in (wanted_tweet_ids or []) if w and w.isdigit()]
    errors: list[str] = []

    try:
        client = await (tweety_factory or _default_tweety_client)()
        return await _read_with_tweety(client, handle, user_id, wanted, max_lookups), None
    except Exception as exc:  # noqa: BLE001
        reason = classify_x_web_error(exc)
        if reason:
            await guard.trip(reason)
            return None, f"x_web tweety read tripped breaker: {reason}"
        errors.append(f"tweety: {type(exc).__name__}: {str(exc)[:200]}")

    api: Any = None
    try:
        api = await (twscrape_factory or _default_twscrape_api)(handle)
        result = await _read_with_twscrape(api, handle, user_id, wanted, max_lookups)
        result.errors.extend(errors)
        return result, None
    except Exception as exc:  # noqa: BLE001
        reason = classify_x_web_error(exc)
        if reason:
            await guard.trip(reason)
            return None, f"x_web twscrape read tripped breaker: {reason}"
        errors.append(f"twscrape: {type(exc).__name__}: {str(exc)[:200]}")
    finally:
        workdir = getattr(api, "_x_web_workdir", None)
        if isinstance(workdir, str):
            shutil.rmtree(workdir, ignore_errors=True)
    return None, "x_web reads failed — " + "; ".join(errors)
