"""Sync SocialAuto operational data into Nextcloud productivity apps.

Two surfaces, both idempotent and driven entirely by real Postgres rows:

* Tables — latest ``follower_snapshots`` per account land as rows in the
  ``SocialAuto Metrics`` table (date/platform/account/metric/value/delta).
  Dedupe key is date+platform+account+metric so repeat runs are no-ops.
* Deck — posts land as cards on the ``SocialAuto Content Pipeline`` board.
  Every card carries a ``post:<uuid>`` marker in its description; cards
  move stacks when the post status changes (failed -> Needs Attention).

Auth uses a Nextcloud app password — never the account login password.
Runs in a Celery task so API latency is unaffected. All failures are
non-fatal: Nextcloud is a reporting surface, never on the publish path.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.models.analytics import FollowerSnapshot
from app.models.content import Post, PostStatus
from app.models.social_account import SocialAccount

logger = logging.getLogger(__name__)

settings = get_settings()

POST_MARKER_PREFIX = "post:"
DECK_STACK_BY_STATUS = {
    "draft": "Drafting",
    "scheduled": "Scheduled",
    "published": "Published",
    "failed": "Needs Attention",
}
# How far back Deck cards track terminal-state posts (keeps the board lean).
TERMINAL_WINDOW_DAYS = 14


def nextcloud_sync_enabled() -> bool:
    return bool(
        settings.NEXTCLOUD_SYNC_ENABLED
        and _base_url()
        and settings.NEXTCLOUD_USERNAME.strip()
        and settings.NEXTCLOUD_APP_PASSWORD.strip()
    )


def _base_url() -> str:
    if settings.NEXTCLOUD_BASE_URL.strip():
        return settings.NEXTCLOUD_BASE_URL.rstrip("/")
    dav = settings.NEXTCLOUD_DAV_URL.strip()
    if "/remote.php" in dav:
        return dav.split("/remote.php")[0].rstrip("/")
    return ""


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        auth=(settings.NEXTCLOUD_USERNAME, settings.NEXTCLOUD_APP_PASSWORD),
        timeout=httpx.Timeout(30.0, connect=10.0),
        headers={"OCS-APIRequest": "true", "User-Agent": "socialauto-nextcloud-sync/1.0"},
    )


# ── Tables ────────────────────────────────────────────────────────────


async def _table_columns(client: httpx.AsyncClient, table_id: int) -> dict[str, dict]:
    r = await client.get(f"{_base_url()}/index.php/apps/tables/api/1/tables/{table_id}/columns")
    r.raise_for_status()
    return {c["title"]: c for c in r.json()}


def _selection_option_id(column: dict, label: str) -> int | None:
    """Tables selection cells store the option id, not the label."""
    for opt in column.get("selectionOptions") or []:
        if str(opt.get("label", "")).lower() == label.lower():
            return int(opt["id"])
    return None


async def _table_rows(client: httpx.AsyncClient, table_id: int) -> list[dict]:
    r = await client.get(f"{_base_url()}/index.php/apps/tables/api/1/tables/{table_id}/rows")
    r.raise_for_status()
    return r.json()


async def sync_follower_metrics(db: AsyncSession) -> dict:
    """Append today's follower snapshot deltas to the metrics table."""
    table_id = settings.NEXTCLOUD_TABLE_ID
    if not table_id:
        return {"skipped": "NEXTCLOUD_TABLE_ID unset"}

    # Latest + previous snapshot per account → value + delta.
    latest = (
        select(
            FollowerSnapshot.social_account_id,
            FollowerSnapshot.platform,
            FollowerSnapshot.followers,
            FollowerSnapshot.captured_at,
        )
        .distinct(FollowerSnapshot.social_account_id)
        .order_by(FollowerSnapshot.social_account_id, desc(FollowerSnapshot.captured_at))
        .cte("latest")
    )
    prev_sub = (
        select(
            FollowerSnapshot.social_account_id,
            FollowerSnapshot.followers,
            func.row_number()
            .over(
                partition_by=FollowerSnapshot.social_account_id,
                order_by=desc(FollowerSnapshot.captured_at),
            )
            .label("rn"),
        )
        .cte("prev")
    )
    rows_q = await db.execute(
        select(
            latest.c.platform,
            latest.c.followers,
            latest.c.captured_at,
            prev_sub.c.followers.label("prev_followers"),
            SocialAccount.username,
            SocialAccount.display_name,
        )
        .join(SocialAccount, SocialAccount.id == latest.c.social_account_id)
        .join(
            prev_sub,
            (prev_sub.c.social_account_id == latest.c.social_account_id) & (prev_sub.c.rn == 2),
            isouter=True,
        )
    )
    metrics = rows_q.all()
    if not metrics:
        return {"inserted": 0, "existing": 0}

    async with _client() as client:
        cols = await _table_columns(client, table_id)
        existing = {
            _row_key(cols, row)
            for row in await _table_rows(client, table_id)
        }
        inserted = skipped = 0
        for platform, followers, captured_at, prev_followers, username, display_name in metrics:
            day = captured_at.date().isoformat()
            account = username or display_name or platform
            key = f"{day}|{platform}|{account}|followers"
            if key in existing:
                skipped += 1
                continue
            platform_opt = _selection_option_id(cols["Platform"], platform)
            if platform_opt is None:
                logger.warning("nextcloud sync: no Platform option for %r — skipping", platform)
                continue
            # Tables v1 row data is an object keyed by column id —
            # {columnId,value} lists 500 with "column id 0" (API quirk).
            payload = {
                "data": {
                    str(cols["Date"]["id"]): day,
                    str(cols["Platform"]["id"]): platform_opt,
                    str(cols["Metric"]["id"]): "followers",
                    str(cols["Value"]["id"]): followers,
                    str(cols["Delta"]["id"]): followers - (prev_followers or followers),
                    str(cols["Notes"]["id"]): account,
                }
            }
            r = await client.post(
                f"{_base_url()}/index.php/apps/tables/api/1/tables/{table_id}/rows",
                json=payload,
            )
            r.raise_for_status()
            inserted += 1
    return {"inserted": inserted, "existing": skipped}


def _row_key(cols: dict[str, dict], row: dict) -> str:
    data = row.get("data", [])
    if isinstance(data, dict):
        vals = {int(k): v for k, v in data.items()}
    else:
        vals = {cell["columnId"]: cell.get("value") for cell in data}
    # Selection cells store the option id — resolve it back to the label.
    platform_col = cols.get("Platform", {})
    raw_platform = vals.get(platform_col.get("id"), "")
    platform = raw_platform
    for opt in platform_col.get("selectionOptions") or []:
        if str(opt.get("id")) == str(raw_platform) or str(opt.get("label", "")).lower() == str(
            raw_platform
        ).lower():
            platform = opt.get("label", raw_platform)
            break
    return "|".join(
        [
            str(vals.get(cols["Date"]["id"], ""))[:10],
            str(platform),
            str(vals.get(cols["Notes"]["id"], "")),
            str(vals.get(cols["Metric"]["id"], "")),
        ]
    )


# ── Deck ──────────────────────────────────────────────────────────────


def _card_title(post: Post) -> str:
    text = (post.content_text or "").strip().splitlines()
    head = text[0] if text else post.id.hex[:8]
    return f"[{post.status.value}] {head}"[:250]


def _card_description(post: Post) -> str:
    lines = [f"{POST_MARKER_PREFIX}{post.id}", f"status: {post.status.value}"]
    if post.scheduled_at:
        lines.append(f"scheduled_at: {post.scheduled_at.isoformat()}")
    if post.published_at:
        lines.append(f"published_at: {post.published_at.isoformat()}")
    if post.failure_reason:
        lines.append(f"failure: {post.failure_reason[:300]}")
    return "\n".join(lines)


async def _board_stacks(client: httpx.AsyncClient, board_id: int) -> list[dict]:
    r = await client.get(f"{_base_url()}/index.php/apps/deck/api/v1.1/boards/{board_id}/stacks")
    r.raise_for_status()
    return r.json()


async def _move_card(
    client: httpx.AsyncClient, board_id: int, stack_id: int, card_id: int, target_stack: int
) -> None:
    """Reorder into the target stack; the API maps body stackId → destination."""
    r = await client.put(
        f"{_base_url()}/index.php/apps/deck/api/v1.1/boards/{board_id}"
        f"/stacks/{stack_id}/cards/{card_id}/reorder",
        json={"order": 999, "stackId": target_stack},
    )
    r.raise_for_status()


async def sync_deck_pipeline(db: AsyncSession) -> dict:
    """Create/move Deck cards so the board mirrors post statuses."""
    board_id = settings.NEXTCLOUD_DECK_BOARD_ID
    if not board_id:
        return {"skipped": "NEXTCLOUD_DECK_BOARD_ID unset"}

    cutoff = datetime.now(UTC) - timedelta(days=TERMINAL_WINDOW_DAYS)
    posts_q = await db.execute(
        select(Post).where(
            Post.status.in_([PostStatus.DRAFT, PostStatus.SCHEDULED])
            | (
                Post.status.in_([PostStatus.PUBLISHED, PostStatus.FAILED])
                & (func.coalesce(Post.published_at, Post.failed_at) >= cutoff)
            )
        )
    )
    posts = posts_q.scalars().all()

    async with _client() as client:
        stacks = await _board_stacks(client, board_id)
        stack_by_title = {s["title"]: s for s in stacks}
        card_by_post: dict[str, tuple[dict, dict]] = {}
        for stack in stacks:
            for card in stack.get("cards") or []:
                desc = card.get("description") or ""
                for line in desc.splitlines():
                    if line.startswith(POST_MARKER_PREFIX):
                        card_by_post[line[len(POST_MARKER_PREFIX):].strip()] = (card, stack)

        created = moved = 0
        for post in posts:
            target_title = DECK_STACK_BY_STATUS.get(post.status.value)
            target = stack_by_title.get(target_title) if target_title else None
            if target is None:
                continue
            marker = str(post.id)
            if marker not in card_by_post:
                r = await client.post(
                    f"{_base_url()}/index.php/apps/deck/api/v1.1/boards/{board_id}"
                    f"/stacks/{target['id']}/cards",
                    json={
                        "title": _card_title(post),
                        "type": "plain",
                        "order": 999,
                        "description": _card_description(post),
                    },
                )
                r.raise_for_status()
                created += 1
                continue
            card, stack = card_by_post[marker]
            if stack["id"] == target["id"]:
                if card.get("title") != _card_title(post):
                    await client.put(
                        f"{_base_url()}/index.php/apps/deck/api/v1.1/boards/{board_id}"
                        f"/stacks/{stack['id']}/cards/{card['id']}",
                        json={
                            "title": _card_title(post),
                            "type": "plain",
                            "order": card.get("order", 999),
                            "description": _card_description(post),
                            "duedate": card.get("duedate"),
                            "owner": card.get("owner"),
                        },
                    ).raise_for_status()
                continue
            await _move_card(client, board_id, stack["id"], card["id"], target["id"])
            moved += 1

        # Verify moves landed (Deck ≤ some versions ignore body stackId — bug #7933);
        # stragglers are deleted + recreated in the right stack.
        stragglers = 0
        if moved:
            stacks = await _board_stacks(client, board_id)
            loc = {}
            for stack in stacks:
                for card in stack.get("cards") or []:
                    desc = card.get("description") or ""
                    for line in desc.splitlines():
                        if line.startswith(POST_MARKER_PREFIX):
                            loc[line[len(POST_MARKER_PREFIX):].strip()] = (card, stack)
            for post in posts:
                marker = str(post.id)
                if marker in card_by_post and marker in loc:
                    target = stack_by_title.get(DECK_STACK_BY_STATUS.get(post.status.value, ""))
                    card, stack = loc[marker]
                    if target and stack["id"] != target["id"]:
                        await client.delete(
                            f"{_base_url()}/index.php/apps/deck/api/v1.1/boards/{board_id}"
                            f"/stacks/{stack['id']}/cards/{card['id']}"
                        )
                        await client.post(
                            f"{_base_url()}/index.php/apps/deck/api/v1.1/boards/{board_id}"
                            f"/stacks/{target['id']}/cards",
                            json={
                                "title": _card_title(post),
                                "type": "plain",
                                "order": 999,
                                "description": _card_description(post),
                            },
                        )
                        stragglers += 1
    return {"created": created, "moved": moved + stragglers, "tracked": len(posts)}


async def sync_all(session_factory: async_sessionmaker[AsyncSession]) -> dict:
    if not nextcloud_sync_enabled():
        return {"skipped": "disabled"}
    async with session_factory() as db:
        metrics = await sync_follower_metrics(db)
        deck = await sync_deck_pipeline(db)
    result = {"metrics": metrics, "deck": deck}
    logger.info("nextcloud sync: %s", result)
    return result
