"""Company brain — persistent operational memory for the agent stack.

Adopted from the company-agents pattern (agent-swarm "persistent brain",
cyber-ai-team "business memory"): agents that write today's real outcomes
to a searchable store and read them back before recommending tomorrow's
actions. Built entirely on infrastructure we already run:

* **Write** — a daily Celery task summarizes the day's real Postgres
  signals (publish outcomes per platform, failure reasons, follower
  deltas) into two surfaces:
    - Chroma ``team_<id>_brain`` collection — semantic recall for agents.
    - Nextcloud Notes ``Company Brain — YYYY-MM-DD`` — owner-visible
      governance journal (readable, editable, durable).
* **Read** — ``recall()`` returns the most relevant past learnings for a
  query; ``strategy_report._llm_actions`` injects them into the strategist
  prompt so tomorrow's actions account for what actually happened.

Every path is fail-silent: the brain is advisory memory, never on the
publish path. No external service beyond Chroma + Nextcloud is required —
embeddings go through the existing DMR → Cloudflare BGE-M3 chain.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.models.analytics import FollowerSnapshot
from app.models.content import Post, PostStatus, PostTarget
from app.services.chroma_client import (
    _CHROMA_TIMEOUT,
    _collection_base_url,
    _get_collection_id,
    _get_embedding,
)

logger = logging.getLogger(__name__)

settings = get_settings()

BRAIN_COLLECTION_SUFFIX = "brain"
NOTE_CATEGORY = "Company Brain"
_NOTE_TITLE_PREFIX = "Company Brain — "


def company_brain_enabled() -> bool:
    return bool(
        settings.COMPANY_BRAIN_ENABLED
        and (settings.CHROMA_URL or "").strip()
    )


def _brain_collection(team_id) -> str:
    return f"team_{str(team_id).replace('-', '_')}_{BRAIN_COLLECTION_SUFFIX}"


# ── Memory write/read (Chroma) ────────────────────────────────────────


async def remember(team_id, doc_id: str, text: str, metadata: dict | None = None) -> bool:
    """Embed + upsert one memory document into the team's brain collection."""
    if not company_brain_enabled() or not text.strip():
        return False
    embedding = await _get_embedding(text[:6000])
    if not embedding:
        return False
    col = _brain_collection(team_id)
    async with httpx.AsyncClient(timeout=_CHROMA_TIMEOUT) as client:
        try:
            col_id = await _get_collection_id(client, col)
            if not col_id:
                return False
            base = _collection_base_url()
            # delete+add keeps the upsert idempotent across Chroma versions.
            await client.post(f"{base}/{col_id}/delete", json={"ids": [doc_id]})
            r = await client.post(
                f"{base}/{col_id}/add",
                json={
                    "ids": [doc_id],
                    "embeddings": [embedding],
                    "documents": [text],
                    "metadatas": [metadata or {}],
                },
            )
            return r.status_code in (200, 201)
        except Exception as exc:  # noqa: BLE001 — brain must never break callers
            logger.warning("company_brain remember failed: %s", exc)
            return False


async def recall(team_id, query: str, n_results: int = 4) -> list[str]:
    """Return the n most semantically relevant memories for a query."""
    if not company_brain_enabled() or not query.strip():
        return []
    embedding = await _get_embedding(query[:2000])
    if not embedding:
        return []
    col = _brain_collection(team_id)
    async with httpx.AsyncClient(timeout=_CHROMA_TIMEOUT) as client:
        try:
            col_id = await _get_collection_id(client, col)
            if not col_id:
                return []
            r = await client.post(
                f"{_collection_base_url()}/{col_id}/query",
                json={"query_embeddings": [embedding], "n_results": n_results},
            )
            if r.status_code != 200:
                return []
            return r.json().get("documents", [[]])[0]
        except Exception as exc:  # noqa: BLE001
            logger.warning("company_brain recall failed: %s", exc)
            return []


# ── Daily journal (write path) ────────────────────────────────────────


async def _day_signals(db: AsyncSession, team_id, day: date) -> dict:
    """Collect the day's real outcomes from Postgres."""
    start = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
    end = start + timedelta(days=1)

    posts_q = await db.execute(
        select(Post)
        .where(
            Post.team_id == team_id,
            func.coalesce(Post.published_at, Post.failed_at).is_not(None),
            func.coalesce(Post.published_at, Post.failed_at) >= start,
            func.coalesce(Post.published_at, Post.failed_at) < end,
        )
        .options(selectinload(Post.targets).selectinload(PostTarget.social_account))
    )
    posts = list(posts_q.scalars().unique().all())

    published: dict[str, int] = {}
    failed: dict[str, list[str]] = {}
    for post in posts:
        platforms = {
            t.social_account.platform for t in post.targets if t.social_account
        } or {"unknown"}
        for plat in platforms:
            if post.status == PostStatus.PUBLISHED:
                published[plat] = published.get(plat, 0) + 1
            elif post.status == PostStatus.FAILED:
                reason = (post.failure_reason or "unknown").splitlines()[0][:120]
                failed.setdefault(plat, []).append(reason)

    # Follower delta vs the day before — latest snapshot on/before `end`
    # minus latest on/before `start`, per platform.
    followers: dict[str, tuple[int | None, int | None]] = {}
    for boundary, which in ((end, "now"), (start, "then")):
        sq = (
            select(
                FollowerSnapshot.platform,
                FollowerSnapshot.followers,
            )
            .where(
                FollowerSnapshot.team_id == team_id,
                FollowerSnapshot.captured_at < boundary,
            )
            .distinct(FollowerSnapshot.platform)
            .order_by(FollowerSnapshot.platform, FollowerSnapshot.captured_at.desc())
        )
        for platform, count in (await db.execute(sq)).all():
            cur = followers.setdefault(platform, (None, None))
            followers[platform] = (
                (count, cur[1]) if which == "now" else (cur[0], count)
            )

    return {
        "published": published,
        "failed": failed,
        "followers": followers,
        "post_count": len(posts),
    }


def _journal_text(team_name: str, day: date, signals: dict) -> str:
    lines = [f"# Company Brain — {day.isoformat()}", "", f"Team: {team_name}", ""]

    pub = signals["published"]
    if pub:
        lines.append("## Published")
        lines.extend(f"- {plat}: {n} post(s)" for plat, n in sorted(pub.items()))
    else:
        lines.append("## Published\n- none")

    failed = signals["failed"]
    lines.append("\n## Failures")
    if failed:
        for plat, reasons in sorted(failed.items()):
            top = "; ".join(list(dict.fromkeys(reasons))[:3])
            lines.append(f"- {plat}: {len(reasons)} failed — {top}")
    else:
        lines.append("- none")

    lines.append("\n## Followers")
    any_delta = False
    for plat, (now, then) in sorted(signals["followers"].items()):
        if now is None:
            continue
        delta = (now - then) if then is not None else None
        delta_s = f"{delta:+d}" if delta is not None else "n/a"
        lines.append(f"- {plat}: {now} ({delta_s} vs prior day)")
        any_delta = True
    if not any_delta:
        lines.append("- no snapshot data")

    return "\n".join(lines)


async def _write_nextcloud_note(title: str, content: str) -> bool:
    """Create-or-update the journal note via the Nextcloud Notes API."""
    base = (settings.NEXTCLOUD_BASE_URL or "").strip().rstrip("/")
    if not base and "/remote.php" in (settings.NEXTCLOUD_DAV_URL or ""):
        base = settings.NEXTCLOUD_DAV_URL.split("/remote.php")[0].rstrip("/")
    if not base or not settings.NEXTCLOUD_USERNAME.strip():
        return False
    auth = (settings.NEXTCLOUD_USERNAME, settings.NEXTCLOUD_APP_PASSWORD)
    api = f"{base}/index.php/apps/notes/api/v1/notes"
    async with httpx.AsyncClient(auth=auth, timeout=20.0) as client:
        try:
            note_id = None
            r = await client.get(api)
            if r.status_code == 200:
                for note in r.json():
                    if note.get("title") == title:
                        note_id = note["id"]
                        break
            if note_id is not None:
                r = await client.put(
                    f"{api}/{note_id}",
                    json={"content": content, "category": NOTE_CATEGORY},
                )
            else:
                r = await client.post(
                    api, json={"title": title, "content": content, "category": NOTE_CATEGORY}
                )
            r.raise_for_status()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("company_brain note write failed: %s", exc)
            return False


async def write_daily_journal(db: AsyncSession, team, day: date | None = None) -> dict:
    """Summarize `day` (default: today UTC) into brain memory + NC note."""
    day = day or datetime.now(UTC).date()
    signals = await _day_signals(db, team.id, day)
    text = _journal_text(team.name or str(team.id), day, signals)

    doc_id = f"journal-{day.isoformat()}"
    memory_ok = await remember(
        team.id, doc_id, text, metadata={"kind": "daily-journal", "day": day.isoformat()}
    )
    note_ok = await _write_nextcloud_note(f"{_NOTE_TITLE_PREFIX}{day.isoformat()}", text)
    return {
        "team": str(team.id),
        "day": day.isoformat(),
        "posts": signals["post_count"],
        "memory": memory_ok,
        "note": note_ok,
    }
