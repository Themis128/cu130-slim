#!/usr/bin/env python3
# ruff: noqa: E402
"""Republish guardrail-failed posts with quality-gated copy.

Runs inside the social-api container:

    docker exec social-api python3 /app/scripts/republish_failed.py            # dry run
    docker exec social-api python3 /app/scripts/republish_failed.py --apply    # write
    docker exec social-api python3 /app/scripts/republish_failed.py --apply --publish-now

Behaviour:
- Finds posts ``status=failed`` whose failure_reason is "All targets were
  skipped (unsupported for publishing)" within ``--days`` (default 14).
- Posts whose targets ALL skipped on intentional policy (dedup windows,
  identical media, media-required) are reclassified ``suppressed`` — they are
  informational, not failures, and must not be republished.
- Posts skipped on fixable guardrails (SEO below minimum, media semantic QA,
  incomplete media) get their copy re-run through ``apply_quality_pipeline``
  (SEO + NLP + spellcheck, target score ``--min-seo``, default 85). The
  improved text becomes a NEW post (fresh id — the 30d content dedup compares
  against published posts, and improved copy differs anyway) targeting only
  healthy ``active`` accounts on text-capable platforms.
- New posts are scheduled one per day at ``--hour`` (default 10:00 UTC) unless
  ``--publish-now`` is given.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta

for path in ("/app", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
    if path not in sys.path and os.path.isdir(os.path.join(path, "app")):
        sys.path.insert(0, path)

from sqlalchemy import select  # noqa: E402

# Same policy markers as app.services.slack_digest._POLICY_SKIP_MARKERS — a
# post skipped ONLY for these reasons enforced an owner rule on purpose.
POLICY_MARKERS = (
    "duplicate content",
    "has no media",
    "never post without media",
    "unsupported for publishing",
    "not supported for publishing",
    "identical media already published",
)

# Platforms able to take a text (or text+image) post today. Dead/revoked
# accounts are filtered by status='active' anyway; this list just documents
# intent and excludes video-only lanes.
TEXT_PLATFORMS = {"linkedin", "facebook", "telegram", "bluesky", "twitter"}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write changes (default dry-run)")
    ap.add_argument("--publish-now", action="store_true", help="publish immediately instead of scheduling")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--min-seo", type=int, default=85)
    ap.add_argument("--hour", type=int, default=10, help="UTC hour for scheduled posts")
    args = ap.parse_args()

    from app.db.session import async_session_maker
    from app.models.content import Post, PostStatus, PostTarget
    from app.models.social_account import SocialAccount
    from app.services.quality_pipeline import apply_quality_pipeline

    cutoff = datetime.now(UTC) - timedelta(days=args.days)
    async with async_session_maker() as db:
        posts = (
            (
                await db.execute(
                    select(Post).where(
                        Post.status == PostStatus.FAILED,
                        Post.failed_at.is_not(None),
                        Post.failed_at >= cutoff,
                        Post.failure_reason.like("All targets%"),
                    )
                )
            )
            .scalars()
            .all()
        )
        print(f"candidate failed posts (last {args.days}d): {len(posts)}")

        suppressed: list[str] = []
        fixable: list[tuple[Post, list[str]]] = []
        healthy_accounts: dict[str, list[SocialAccount]] = {}

        for post in posts:
            if (post.content_text or "").strip().lower().startswith(("calendar e2e", "e2e ")):
                continue  # test artifacts, not content
            targets = (await db.execute(select(PostTarget).where(PostTarget.post_id == post.id))).scalars().all()
            reasons = [((t.error_message or "").lower()) for t in targets if t.status == "skipped"]
            if reasons and all(any(m in r for m in POLICY_MARKERS) for r in reasons):
                suppressed.append(str(post.id))
                continue
            fixable.append((post, reasons))

        # Healthy active accounts per text-capable platform.
        accounts = (
            (
                await db.execute(
                    select(SocialAccount).where(
                        SocialAccount.status == "active",
                        SocialAccount.platform.in_(TEXT_PLATFORMS),
                    )
                )
            )
            .scalars()
            .all()
        )
        for acc in accounts:
            healthy_accounts.setdefault(acc.platform, []).append(acc)
        print(
            f"healthy text-platform accounts: {sum(len(v) for v in healthy_accounts.values())} "
            f"({', '.join(f'{k}:{len(v)}' for k, v in sorted(healthy_accounts.items()))})"
        )

        created: list[str] = []
        skipped_posts: list[tuple[str, str]] = []
        day_offset = 0
        for post, reasons in fixable:
            pid = str(post.id)
            try:
                quality = await apply_quality_pipeline(
                    content=post.content_text or "",
                    platform="linkedin",
                    hashtags=list(post.hashtags or []),
                    db=db,
                    team_id=post.team_id,
                    target_score=args.min_seo,
                    max_iterations=3,
                )
            except Exception as exc:
                skipped_posts.append((pid, f"pipeline_error:{exc!r}"[:120]))
                continue
            overall = int((quality.seo_score or {}).get("overall") or 0)
            new_text = (quality.content or "").strip()
            if overall < args.min_seo or not new_text:
                skipped_posts.append((pid, f"seo_still_low:{overall}"))
                continue
            if quality.gibberish_tokens:
                skipped_posts.append((pid, f"gibberish:{','.join(quality.gibberish_tokens[:3])}"))
                continue

            # Pick one account per platform, prefer company pages.
            chosen: list[SocialAccount] = []
            for platform in ("linkedin", "facebook", "telegram", "bluesky"):
                cands = healthy_accounts.get(platform) or []
                if not cands:
                    continue
                cands.sort(key=lambda a: 0 if ("cloudless" in (a.username or "").lower() or "page" in (a.account_type or "").lower()) else 1)
                chosen.append(cands[0])
            if not chosen:
                skipped_posts.append((pid, "no_healthy_targets"))
                continue

            if args.publish_now:
                scheduled_at = datetime.now(UTC)
            else:
                day_offset += 1
                tomorrow = datetime.now(UTC) + timedelta(days=day_offset)
                scheduled_at = tomorrow.replace(hour=args.hour, minute=0, second=0, microsecond=0)
                new_status = PostStatus.SCHEDULED

            new_post = Post(
                team_id=post.team_id,
                user_id=post.user_id,
                status=new_status,
                content_text=new_text,
                # Carry original media — the publish-time media_semantic_gate
                # re-validates each asset against the NEW copy (content_hash
                # differs) and skips mismatches, so wrong media never ships.
                media_ids=list(post.media_ids or []),
                platform_specific=post.platform_specific or {},
                hashtags=list(post.hashtags or []),
                mention_accounts=list(post.mention_accounts or []),
                link_url=post.link_url,
                link_preview_override=post.link_preview_override,
                scheduled_at=scheduled_at,
                meta_data={
                    **(post.meta_data or {}),
                    "republished_from": pid,
                    "quality_gate": quality.to_dict(),
                },
                pillar_id=post.pillar_id,
                content_brief_id=post.content_brief_id,
            )
            db.add(new_post)
            await db.flush()
            for acc in chosen:
                db.add(PostTarget(post_id=new_post.id, social_account_id=acc.id))
            created.append(f"{pid} -> {new_post.id} seo={overall} targets={[a.platform for a in chosen]} at={scheduled_at}")

        if args.apply:
            if suppressed:
                await db.execute(
                    Post.__table__.update()
                    .where(Post.id.in_([__import__("uuid").UUID(s) for s in suppressed]))
                    .values(status=PostStatus.SUPPRESSED, failure_reason="All targets suppressed by publish guardrails")
                )
            await db.commit()
        else:
            await db.rollback()

        print(f"\n== RESULT (apply={args.apply}) ==")
        print(f"suppressed (policy-only): {len(suppressed)}")
        for s in suppressed:
            print(f"  ~ {s}")
        print(f"republished: {len(created)}")
        for c in created:
            print(f"  + {c}")
        print(f"skipped: {len(skipped_posts)}")
        for pid, why in skipped_posts:
            print(f"  - {pid}: {why}")


if __name__ == "__main__":
    asyncio.run(main())
