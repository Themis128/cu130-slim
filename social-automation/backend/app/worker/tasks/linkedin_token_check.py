"""Celery task — LinkedIn OAuth token health.

LinkedIn access tokens die silently: revocation (password change, app
permission removal, LinkedIn-side invalidation) flips every API lane to
401 while ``social_accounts.status`` stays ``active`` and
``token_expires_at`` still points months out — observed 2026-11-17 when
both LinkedIn grants returned AUTHENTICATION_FAILED + invalid_grant for
~a day with no alert. This task probes each active LinkedIn account
token every 6h and raises a P2 Telegram alert when a token is rejected
or dies within 7 days — the LinkedIn counterpart of
``facebook_token_check``.

Probe: GET api.linkedin.com/v2/userinfo with the bearer token. 200 =
healthy (both person and organization rows store member-grant tokens —
LinkedIn has no separate org token). 401/403 = invalid. Anything else
(timeout, 5xx, non-JSON) is inconclusive and never flagged.

Recovery is manual: the OAuth grant must be re-consented
(``scripts/linkedin_consent_url.py``) — LinkedIn refresh tokens return
invalid_grant once the grant is revoked.

Lane: P2 (ops) — reconnect is a human OAuth click.
"""

import logging
import re
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
from sqlalchemy import cast, func, select, update
from sqlalchemy.dialects.postgresql import JSONB

from app.core.security import decrypt_token
from app.models.social_account import SocialAccount
from app.services.slack_notifications import send_telegram_alert
from app.worker._async import run_async, task_session
from app.worker.celery_app import celery_app

celery_app.set_default()
celery_app.set_current()

logger = logging.getLogger(__name__)

USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
WARN_DAYS = 7
DEAD_STATUSES = (401, 403)

# --- Linkedin-Version sunset guard ---------------------------------------
# Marketing API versions are monthly; each sunsets ~12 months after its
# release month on the 15th (202601 → 2027-01-15, per Microsoft Learn).
# Pins live as literals in services/scripts, so scan the source tree —
# new pins are covered automatically. Alert weekly inside the 90-day
# runway (text carries the ISO-week bucket so dedup yields ~1 alert/wk).
# Versions already past nominal sunset are skipped: LinkedIn enforcement
# is endpoint-specific and laxer than the docs (202312/202510 pins still
# serve live traffic), so a past date is not itself a failure signal.

_VERSION_HDR_RE = re.compile(r"Linkedin-Version[\"']?\s*[:=]\s*[\"'](20\d{4})")
_VERSION_CONST_RE = re.compile(
    r"(?:API_VERSION|LINKEDIN_VERSION|PAGES_DP_VERSION|MEMBER_DP_VERSION|VER)"
    r"\s*=\s*[\"'](20\d{4})"
)
SUNSET_WARN_DAYS = 90
_BACKEND_ROOT = Path(__file__).resolve().parents[3]


def _version_sunset(version: str) -> date:
    """Approximate LinkedIn marketing-version sunset: same month next year."""
    return date(int(version[:4]) + 1, int(version[4:]), 15)


def _collect_linkedin_versions(roots: list[Path] | None = None) -> dict[str, list[str]]:
    """Map each pinned ``Linkedin-Version`` (YYYYMM) to the files using it."""
    found: dict[str, list[str]] = {}
    for root in roots or [_BACKEND_ROOT / "app", _BACKEND_ROOT / "scripts"]:
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            versions = set(_VERSION_HDR_RE.findall(text))
            # Module constants only count in linkedin-named files — a bare
            # ``API_VERSION`` elsewhere belongs to a different API.
            if "linkedin" in path.name.lower():
                versions.update(_VERSION_CONST_RE.findall(text))
            for v in versions:
                try:
                    loc = str(path.relative_to(_BACKEND_ROOT))
                except ValueError:
                    loc = str(path)
                found.setdefault(v, []).append(loc)
    return found


async def _check_version_sunset(stats: dict) -> None:
    today = date.today()
    for version, files in sorted(_collect_linkedin_versions().items()):
        days_left = (_version_sunset(version) - today).days
        if days_left <= 0 or days_left > SUNSET_WARN_DAYS:
            continue
        stats["version_alerts"] += 1
        weeks = max(days_left, 0) // 7
        await send_telegram_alert(
            "ops",
            f"LinkedIn-Version {version} sunsets {_version_sunset(version).isoformat()} "
            f"(~{weeks}w left, {days_left}d) — bump pins before NONEXISTENT_VERSION. "
            f"Pinned in: {', '.join(sorted(set(files))[:6])}",
        )


@celery_app.task(name="app.worker.tasks.linkedin_token_check.check_linkedin_tokens")
def check_linkedin_tokens() -> dict:
    return run_async(_check())


async def _probe(token: str) -> bool | None:
    """True = token accepted, False = rejected, None = inconclusive."""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(USERINFO_URL, headers={"Authorization": f"Bearer {token}"})
    except Exception as exc:  # noqa: BLE001
        logger.warning("LinkedIn token probe transport error: %s", exc)
        return None
    if r.status_code == 200:
        return True
    if r.status_code in DEAD_STATUSES:
        return False
    logger.warning("LinkedIn token probe inconclusive HTTP %s", r.status_code)
    return None


async def _check() -> dict:
    stats = {"scanned": 0, "healthy": 0, "expiring": 0, "expired": 0, "inconclusive": 0, "version_alerts": 0}
    async with task_session() as db:
        rows = (
            (
                await db.execute(
                    select(SocialAccount).where(
                        SocialAccount.platform == "linkedin",
                        SocialAccount.status == "active",
                    )
                )
            )
            .scalars()
            .all()
        )

        for acct in rows:
            stats["scanned"] += 1
            meta = acct.meta_data or {}
            raw = acct.access_token_enc
            if not raw:
                continue
            token = decrypt_token(bytes(raw))
            try:
                valid = await _probe(token)
            except Exception as exc:  # noqa: BLE001
                logger.warning("LinkedIn token probe failed %s: %s", acct.id, exc)
                continue

            meta["li_token_checked_at"] = datetime.now(UTC).isoformat()
            if valid is None:
                stats["inconclusive"] += 1
            elif not valid:
                stats["expired"] += 1
                meta["li_token_invalid"] = True
                await send_telegram_alert(
                    "ops",
                    f"LinkedIn token invalid/expired for {acct.username or acct.account_id} — "
                    "re-consent via scripts/linkedin_consent_url.py (grant revoked; refresh returns invalid_grant).",
                )
            else:
                meta["li_token_invalid"] = False
                if acct.token_expires_at:
                    days = (acct.token_expires_at - datetime.now(UTC)).days
                    if days <= WARN_DAYS:
                        stats["expiring"] += 1
                        await send_telegram_alert(
                            "ops",
                            f"LinkedIn token for {acct.username or acct.account_id} expires in {days}d — re-consent via scripts/linkedin_consent_url.py soon.",
                        )
                    else:
                        stats["healthy"] += 1
                else:
                    stats["healthy"] += 1
            # Merge only the token-health keys atomically — assigning the
            # whole meta_data doc would clobber concurrent heal writes.
            patch: dict = {"li_token_checked_at": meta["li_token_checked_at"]}
            if valid is not None:
                patch["li_token_invalid"] = meta["li_token_invalid"]
            await db.execute(
                update(SocialAccount)
                .where(SocialAccount.id == acct.id)
                .values(meta_data=func.coalesce(SocialAccount.meta_data, cast({}, JSONB)).op("||")(cast(patch, JSONB)))
            )
        await db.commit()
    await _check_version_sunset(stats)
    return stats
