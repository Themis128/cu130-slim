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
import uuid
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
    "review": "In Review",
    "approved": "Scheduled",
    "publishing": "Scheduled",
    "scheduled": "Scheduled",
    "published": "Published",
    "failed": "Needs Attention",
}
# How far back Deck cards track terminal-state posts (keeps the board lean).
TERMINAL_WINDOW_DAYS = 14


def _team_filter():
    """Scope the export to one team when configured (multi-tenant safety)."""
    if not settings.NEXTCLOUD_SYNC_TEAM_ID.strip():
        return None
    try:
        return uuid.UUID(settings.NEXTCLOUD_SYNC_TEAM_ID.strip())
    except ValueError:
        logger.warning("nextcloud sync: bad NEXTCLOUD_SYNC_TEAM_ID — exporting all teams")
        return None


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
    team_id = _team_filter()
    latest_q = select(
        FollowerSnapshot.social_account_id,
        FollowerSnapshot.platform,
        FollowerSnapshot.followers,
        FollowerSnapshot.captured_at,
    )
    prev_q = select(
        FollowerSnapshot.social_account_id,
        FollowerSnapshot.followers,
        func.row_number()
        .over(
            partition_by=FollowerSnapshot.social_account_id,
            order_by=desc(FollowerSnapshot.captured_at),
        )
        .label("rn"),
    )
    if team_id is not None:
        # Filter inside the CTEs — a top-level where on FollowerSnapshot
        # would add an uncorrelated cross join.
        latest_q = latest_q.where(FollowerSnapshot.team_id == team_id)
        prev_q = prev_q.where(FollowerSnapshot.team_id == team_id)
    latest = (
        latest_q.distinct(FollowerSnapshot.social_account_id)
        .order_by(FollowerSnapshot.social_account_id, desc(FollowerSnapshot.captured_at))
        .cte("latest")
    )
    prev_sub = prev_q.cte("prev")
    query = (
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
    rows_q = await db.execute(query)
    metrics = rows_q.all()
    if not metrics:
        return {"inserted": 0, "existing": 0, "updated": 0}

    async with _client() as client:
        cols = await _table_columns(client, table_id)
        # key -> (row_id, stored follower value) so same-day changes update
        # the row instead of freezing at first insertion.
        existing = {
            _row_key(cols, row): (row["id"], _row_value(cols, row))
            for row in await _table_rows(client, table_id)
        }
        inserted = skipped = updated = 0
        for platform, followers, captured_at, prev_followers, username, display_name in metrics:
            day = captured_at.date().isoformat()
            account = username or display_name or platform
            key = f"{day}|{platform}|{account}|followers"
            delta = followers - (prev_followers if prev_followers is not None else followers)
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
                    str(cols["Delta"]["id"]): delta,
                    str(cols["Notes"]["id"]): account,
                }
            }
            if key in existing:
                row_id, stored = existing[key]
                if stored == followers:
                    skipped += 1
                    continue
                r = await client.put(
                    f"{_base_url()}/index.php/apps/tables/api/1/rows/{row_id}",
                    json=payload,
                )
                r.raise_for_status()
                updated += 1
                continue
            r = await client.post(
                f"{_base_url()}/index.php/apps/tables/api/1/tables/{table_id}/rows",
                json=payload,
            )
            r.raise_for_status()
            inserted += 1
    return {"inserted": inserted, "existing": skipped, "updated": updated}


def _row_cells(row: dict) -> dict[int, object]:
    data = row.get("data", [])
    if isinstance(data, dict):
        return {int(k): v for k, v in data.items()}
    return {cell["columnId"]: cell.get("value") for cell in data}


def _row_value(cols: dict[str, dict], row: dict) -> object:
    return _row_cells(row).get(cols["Value"]["id"])


def _row_key(cols: dict[str, dict], row: dict) -> str:
    vals = _row_cells(row)
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


def _card_duedate(post: Post) -> str | None:
    """Deck 'due today/tomorrow/upcoming' dashboard widgets key off duedate —
    feed it from scheduled_at so the pipeline board drives them."""
    return post.scheduled_at.isoformat() if post.scheduled_at else None


def _duedate_differs(card_due: str | None, want: str | None) -> bool:
    if want is None:
        return False
    if not card_due:
        return True
    try:
        return datetime.fromisoformat(card_due) != datetime.fromisoformat(want)
    except ValueError:
        return card_due != want


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


def _owner_uid(card: dict) -> str:
    """Deck update expects owner as a uid string; GET returns an object."""
    owner = card.get("owner")
    if isinstance(owner, dict):
        return str(owner.get("uid") or owner.get("primaryKey") or "")
    return str(owner or settings.NEXTCLOUD_USERNAME)


async def _update_card(
    client: httpx.AsyncClient, board_id: int, stack_id: int, card: dict, post: Post
) -> None:
    r = await client.put(
        f"{_base_url()}/index.php/apps/deck/api/v1.1/boards/{board_id}"
        f"/stacks/{stack_id}/cards/{card['id']}",
        json={
            "title": _card_title(post),
            "type": "plain",
            "order": card.get("order", 999),
            "description": _card_description(post),
            "duedate": _card_duedate(post) or card.get("duedate"),
            "owner": _owner_uid(card),
        },
    )
    r.raise_for_status()


async def _create_card(
    client: httpx.AsyncClient, board_id: int, stack_id: int, post: Post, source: dict | None = None
) -> dict:
    body: dict = {
        "title": _card_title(post),
        "type": "plain",
        "order": 999,
        "description": _card_description(post),
    }
    duedate = _card_duedate(post) or (source or {}).get("duedate")
    if duedate:
        body["duedate"] = duedate
    r = await client.post(
        f"{_base_url()}/index.php/apps/deck/api/v1.1/boards/{board_id}/stacks/{stack_id}/cards",
        json=body,
    )
    r.raise_for_status()
    new_card = r.json()
    # Recreate path: carry over labels + assignees the sync did not author.
    if source:
        for label in source.get("labels") or []:
            try:
                r = await client.put(
                    f"{_base_url()}/index.php/apps/deck/api/v1.1/boards/{board_id}"
                    f"/stacks/{stack_id}/cards/{new_card['id']}/assignLabel",
                    json={"labelId": label["id"]},
                )
                r.raise_for_status()
            except (httpx.HTTPError, KeyError) as exc:
                logger.warning("nextcloud sync: label carry-over failed: %s", exc)
        for user in source.get("assignedUsers") or []:
            uid = (user.get("participant") or {}).get("uid") or user.get("id")
            if not uid:
                continue
            try:
                r = await client.put(
                    f"{_base_url()}/index.php/apps/deck/api/v1.1/boards/{board_id}"
                    f"/stacks/{stack_id}/cards/{new_card['id']}/assignUser",
                    json={"userId": uid},
                )
                r.raise_for_status()
            except httpx.HTTPError as exc:
                logger.warning("nextcloud sync: assignee carry-over failed: %s", exc)
    return new_card


async def sync_deck_pipeline(db: AsyncSession) -> dict:
    """Create/move/archive Deck cards so the board mirrors post statuses."""
    board_id = settings.NEXTCLOUD_DECK_BOARD_ID
    if not board_id:
        return {"skipped": "NEXTCLOUD_DECK_BOARD_ID unset"}

    cutoff = datetime.now(UTC) - timedelta(days=TERMINAL_WINDOW_DAYS)
    live_statuses = [
        PostStatus.DRAFT,
        PostStatus.REVIEW,
        PostStatus.APPROVED,
        PostStatus.SCHEDULED,
        PostStatus.PUBLISHING,
    ]
    query = select(Post).where(
        Post.status.in_(live_statuses)
        | (
            Post.status.in_([PostStatus.PUBLISHED, PostStatus.FAILED])
            & (func.coalesce(Post.published_at, Post.failed_at) >= cutoff)
        )
    )
    team_id = _team_filter()
    if team_id is not None:
        query = query.where(Post.team_id == team_id)
    posts_q = await db.execute(query)
    posts = posts_q.scalars().all()
    tracked_ids = {str(p.id) for p in posts}

    async with _client() as client:
        stacks = await _board_stacks(client, board_id)
        stack_by_title = {s["title"]: s for s in stacks}
        card_by_post: dict[str, tuple[dict, dict]] = {}
        for stack in stacks:
            for card in stack.get("cards") or []:
                if card.get("archived"):
                    continue
                desc = card.get("description") or ""
                for line in desc.splitlines():
                    if line.startswith(POST_MARKER_PREFIX):
                        card_by_post[line[len(POST_MARKER_PREFIX):].strip()] = (card, stack)

        # Archive cards whose post left the tracked window (aged out of
        # terminal window, archived, or deleted) — the board stays live.
        archived = 0
        for marker, (card, stack) in card_by_post.items():
            if marker in tracked_ids:
                continue
            r = await client.put(
                f"{_base_url()}/index.php/apps/deck/api/v1.1/boards/{board_id}"
                f"/stacks/{stack['id']}/cards/{card['id']}/archive"
            )
            r.raise_for_status()
            archived += 1

        created = moved = 0
        for post in posts:
            target_title = DECK_STACK_BY_STATUS.get(post.status.value)
            target = stack_by_title.get(target_title) if target_title else None
            if target is None:
                continue
            marker = str(post.id)
            if marker not in card_by_post:
                await _create_card(client, board_id, target["id"], post)
                created += 1
                continue
            card, stack = card_by_post[marker]
            if stack["id"] == target["id"]:
                if (
                    card.get("title") != _card_title(post)
                    or card.get("description") != _card_description(post)
                    or _duedate_differs(card.get("duedate"), _card_duedate(post))
                ):
                    await _update_card(client, board_id, stack["id"], card, post)
                continue
            await _move_card(client, board_id, stack["id"], card["id"], target["id"])
            await _update_card(client, board_id, target["id"], card, post)
            moved += 1

        # Verify moves landed (Deck ≤ some versions ignore body stackId — bug #7933).
        # Fallback recreates in the target stack FIRST, carrying labels,
        # assignees, and due dates, and only removes the source after the
        # replacement exists — comments/attachments still can't transfer via
        # REST, so this only fires when reorder truly failed.
        recreated = 0
        if moved:
            stacks = await _board_stacks(client, board_id)
            loc = {}
            for stack in stacks:
                for card in stack.get("cards") or []:
                    if card.get("archived"):
                        continue
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
                        new_card = await _create_card(
                            client, board_id, target["id"], post, source=card
                        )
                        if not new_card.get("id"):
                            logger.warning(
                                "nextcloud sync: recreate failed for card %s — source kept",
                                card["id"],
                            )
                            continue
                        r = await client.delete(
                            f"{_base_url()}/index.php/apps/deck/api/v1.1/boards/{board_id}"
                            f"/stacks/{stack['id']}/cards/{card['id']}"
                        )
                        r.raise_for_status()
                        recreated += 1
    return {
        "created": created,
        "moved": moved + recreated,
        "archived": archived,
        "tracked": len(posts),
    }


async def sync_all(session_factory: async_sessionmaker[AsyncSession]) -> dict:
    if not nextcloud_sync_enabled():
        return {"skipped": "disabled"}
    async with session_factory() as db:
        metrics = await sync_follower_metrics(db)
        deck = await sync_deck_pipeline(db)
    result = {"metrics": metrics, "deck": deck}
    logger.info("nextcloud sync: %s", result)
    return result
