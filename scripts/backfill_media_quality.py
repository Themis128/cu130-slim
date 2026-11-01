#!/usr/bin/env python3
"""
Retroactive quality backfill script for existing media assets.

This script iterates over media assets that don't have quality metadata
and runs the full quality pipeline (spellcheck, NLP, SEO, Sofia) on them.

Usage:
    python scripts/backfill_media_quality.py [--dry-run] [--limit N] [--team-id TEAM_ID]
"""

import argparse
import asyncio
import logging
import sys
from typing import Optional
from uuid import UUID

# Add the backend to the path
sys.path.insert(0, "/home/tbaltzakis/cu130-slim/social-automation/backend")

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.db.session import get_db
from app.models.content import MediaAsset
from app.models.user import Team
from app.services.media_quality import apply_media_quality, persist_media_quality_metadata

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


async def backfill_media_quality(
    db: AsyncSession,
    team_id: Optional[UUID] = None,
    limit: int = 100,
    dry_run: bool = False,
) -> dict:
    """
    Backfill quality metadata for media assets.
    
    Args:
        db: Database session
        team_id: Optional team ID to filter assets
        limit: Maximum number of assets to process
        dry_run: If True, only log what would be done without making changes
        
    Returns:
        Dict with stats: processed, updated, skipped, errors
    """
    stats = {"processed": 0, "updated": 0, "skipped": 0, "errors": 0}
    
    # Build query for assets without quality metadata
    from sqlalchemy import or_
    query = select(MediaAsset).where(
        or_(
            MediaAsset.meta_data.is_(None),
            ~MediaAsset.meta_data.has_key("quality")
        )
    )
    
    if team_id:
        query = query.where(MediaAsset.team_id == team_id)
    
    query = query.limit(limit)
    
    result = await db.execute(query)
    assets = result.scalars().all()
    
    logger.info(f"Found {len(assets)} media assets without quality metadata")
    
    for asset in assets:
        stats["processed"] += 1
        try:
            logger.info(f"Processing asset {asset.id}: {asset.filename}")
            
            # Build text fields from asset
            caption = asset.ai_caption or asset.filename or ""
            alt_text = asset.alt_text or ""
            tags = asset.tags or []
            
            # Skip if no text content to analyze
            if not caption and not alt_text and not tags:
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
                run_nlp=True,
                run_spellcheck=True,
                run_seo=True,
            )
            
            # Persist quality metadata
            await persist_media_quality_metadata(asset, quality_result, db)
            
            logger.info(
                f"  Updated asset {asset.id}: "
                f"spellcheck={quality_result.spellcheck_applied}, "
                f"nlp_fixed={quality_result.nlp_report.get('fixed', False) if quality_result.nlp_report else False}, "
                f"seo={quality_result.seo_score.get('overall') if quality_result.seo_score else 'N/A'}, "
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
    
    args = parser.parse_args()
    
    team_id = UUID(args.team_id) if args.team_id else None
    
    settings = get_settings()
    db_url = args.db_url or settings.DATABASE_URL
    
    engine = create_async_engine(db_url, pool_pre_ping=True)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session() as db:
        logger.info("Starting media quality backfill...")
        if args.dry_run:
            logger.info("DRY RUN MODE - no changes will be made")
        
        stats = await backfill_media_quality(
            db=db,
            team_id=team_id,
            limit=args.limit,
            dry_run=args.dry_run,
        )
        
        logger.info("Backfill complete!")
        logger.info(f"  Processed: {stats['processed']}")
        logger.info(f"  Updated: {stats['updated']}")
        logger.info(f"  Skipped: {stats['skipped']}")
        logger.info(f"  Errors: {stats['errors']}")
    
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())