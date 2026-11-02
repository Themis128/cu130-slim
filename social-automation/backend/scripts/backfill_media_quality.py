#!/usr/bin/env python3
"""
Retroactive quality backfill script for existing media assets.

This script iterates over media assets that don't have quality metadata
and runs the full quality pipeline (spellcheck, NLP, SEO, Sofia) on them.

The pipeline is resilient - if external services (LanguageTool, DMR, CF) are
unavailable, it still records quality metadata with available scores and
flags any issues.

Usage:
    python scripts/backfill_media_quality.py [--dry-run] [--limit N] [--team-id TEAM_ID] [--skip-nlp] [--force]

Use --force to reprocess assets that already have quality metadata (e.g. to
overwrite scores written during a degraded run when LanguageTool/DMR were down).
"""

import argparse
import asyncio
import logging
import sys
from uuid import UUID

# Add the backend to the path
sys.path.insert(0, "/home/tbaltzakis/cu130-slim/social-automation/backend")

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.db.session import async_session_maker
from app.models.content import MediaAsset
from app.services.media_quality import apply_media_quality

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


async def backfill_media_quality(
    db: AsyncSession,
    team_id: UUID | None = None,
    limit: int = 100,
    dry_run: bool = False,
    skip_nlp: bool = False,
    force: bool = False,
) -> dict:
    """
    Backfill quality metadata for media assets.

    Args:
        db: Database session
        team_id: Optional team ID to filter assets
        limit: Maximum number of assets to process
        dry_run: If True, only log what would be done without making changes
        skip_nlp: If True, skip NLP checks (faster, no LLM calls)
        force: If True, reprocess assets that already have quality metadata
            (use to overwrite scores written during a degraded run where
            external services — LanguageTool/DMR — were unavailable)

    Returns:
        Dict with stats: processed, updated, skipped, errors
    """
    stats = {"processed": 0, "updated": 0, "skipped": 0, "errors": 0}

    # Build query - fetch all assets then filter in Python
    query = select(MediaAsset)

    if team_id:
        query = query.where(MediaAsset.team_id == team_id)

    query = query.limit(limit)

    result = await db.execute(query)
    all_assets = result.scalars().all()

    # Filter in Python to avoid JSONB operator issues
    assets = []
    for asset in all_assets:
        if asset.meta_data is None:
            assets.append(asset)
        elif isinstance(asset.meta_data, dict):
            if force or 'quality' not in asset.meta_data:
                assets.append(asset)

    scope = "all assets (--force)" if force else "assets without quality metadata"
    logger.info(f"Found {len(assets)} media assets to process — {scope} (from {len(all_assets)} total)")

    for asset in assets:
        stats["processed"] += 1
        try:
            logger.info(f"Processing asset {asset.id}: {asset.filename}")

            # Build text fields from asset
            caption = asset.ai_caption or asset.filename or ""
            alt_text = asset.alt_text or ""
            tags = asset.tags or []

            # Skip if no text content to analyze
            if not caption:
                logger.info(f"  Skipping asset {asset.id}: no text content")
                stats["skipped"] += 1
                continue

            if dry_run:
                logger.info(f"  [DRY RUN] Would apply quality pipeline to asset {asset.id}")
                stats["updated"] += 1
                continue

            # Determine platform from tags or default to linkedin
            platform = "linkedin"
            if tags:
                for tag in tags:
                    if tag in ("instagram", "twitter", "x", "facebook", "threads", "tiktok", "bluesky"):
                        platform = tag
                        break

            # Apply quality pipeline
            quality_result = await apply_media_quality(
                caption=caption,
                alt_text=alt_text,
                tags=tags,
                platform=platform,
                db=db,
                team_id=asset.team_id,
                provider_name="dmr",
                run_nlp=not skip_nlp,
                run_spellcheck=True,
                run_seo=True,
            )

            # Persist quality metadata
            if asset.meta_data is None:
                asset.meta_data = {}

            asset.meta_data["quality"] = quality_result.to_dict()
            asset.meta_data["quality_applied"] = (
                quality_result.spellcheck_applied
                or bool(quality_result.nlp_report)
                or bool(quality_result.seo_score)
            )

            # Update corrected text fields on the asset
            if quality_result.alt_text and quality_result.alt_text != asset.alt_text:
                asset.alt_text = quality_result.alt_text
            if quality_result.tags and list(quality_result.tags) != list(asset.tags or []):
                asset.tags = list(quality_result.tags)

            flag_modified(asset, "meta_data")
            await db.commit()

            logger.info(
                f"  Updated asset {asset.id}: "
                f"spellcheck={quality_result.spellcheck_applied}, "
                f"seo={quality_result.seo_score.get('overall', 'N/A')}, "
                f"sofia={quality_result.sofia_score}, "
                f"gibberish={len(quality_result.gibberish_tokens)} tokens"
            )
            stats["updated"] += 1

        except Exception as exc:
            logger.error(f"  Error processing asset {asset.id}: {exc}")
            stats["errors"] += 1

    return stats


async def main():
    parser = argparse.ArgumentParser(description="Backfill quality metadata for media assets")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")
    parser.add_argument("--limit", type=int, default=100, help="Maximum number of assets to process")
    parser.add_argument("--team-id", type=str, help="Filter by team ID (UUID)")
    parser.add_argument("--db-url", type=str, help="Database URL (defaults to env)")
    parser.add_argument("--skip-nlp", action="store_true", help="Skip NLP checks (no LLM calls)")
    parser.add_argument("--force", action="store_true", help="Reprocess assets that already have quality metadata")

    args = parser.parse_args()

    team_id = UUID(args.team_id) if args.team_id else None

    logger.info("Starting media quality backfill...")
    if args.dry_run:
        logger.info("DRY RUN MODE - no changes will be made")
    if args.skip_nlp:
        logger.info("NLP checks will be skipped (no LLM calls)")
    if args.force:
        logger.info("FORCE MODE - assets with existing quality metadata will be reprocessed")

    async with async_session_maker() as db:
        stats = await backfill_media_quality(
            db=db,
            team_id=team_id,
            limit=args.limit,
            dry_run=args.dry_run,
            skip_nlp=args.skip_nlp,
            force=args.force,
        )

        logger.info("Backfill complete!")
        logger.info(f"  Processed: {stats['processed']}")
        logger.info(f"  Updated: {stats['updated']}")
        logger.info(f"  Skipped: {stats['skipped']}")
        logger.info(f"  Errors: {stats['errors']}")


if __name__ == "__main__":
    asyncio.run(main())
