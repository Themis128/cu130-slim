"""Smoke-test media AI auto-tagging and R2 presigned URLs against real services.

Run inside the social-api container:
    python scripts/smoke_test_media_ai.py
"""
from __future__ import annotations

import asyncio
import io
import os
import sys
import uuid
from datetime import UTC, datetime

from PIL import Image
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import async_session_maker
from app.models.content import MediaAsset
from app.models.user import Team, TeamMember, User
from app.services import media_ai, r2_presigned


def _make_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (256, 256), color=(80, 120, 200)).save(buf, format="PNG")
    return buf.getvalue()


async def _call_cf_caption(image_b64: str, settings) -> dict | None:
    if not (settings.CLOUDFLARE_ACCOUNT_ID or "").strip() or not (settings.CLOUDFLARE_API_TOKEN or "").strip():
        return None
    try:
        return await media_ai._call_cloudflare_vision(
            image_b64=image_b64,
            task="caption",
            prompt="Generate a concise caption for this image",
        )
    except Exception as exc:
        print(f"  CAPTION_CALL_FAILED: {exc}")
        return None


async def main() -> int:
    settings = get_settings()

    print("=== Environment check ===")
    missing = []
    if not (settings.CLOUDFLARE_ACCOUNT_ID or "").strip():
        missing.append("CLOUDFLARE_ACCOUNT_ID")
    if not (settings.CLOUDFLARE_API_TOKEN or "").strip():
        missing.append("CLOUDFLARE_API_TOKEN")
    if missing:
        print(f"  MISSING: {', '.join(missing)}")
        print("  Please add these to .env and ensure the token has Workers AI / AI:Run permissions.")

    r2_missing = []
    if not (settings.R2_ACCESS_KEY_ID or "").strip():
        r2_missing.append("R2_ACCESS_KEY_ID")
    if not (settings.R2_SECRET_ACCESS_KEY or "").strip():
        r2_missing.append("R2_SECRET_ACCESS_KEY")
    if not (settings.R2_BUCKET_NAME or "").strip():
        r2_missing.append("R2_BUCKET_NAME")
    if r2_missing:
        print(f"  R2 presigned URLs MISSING: {', '.join(r2_missing)}")
        print("  Presigned upload will fall back to the server-side /upload endpoint.")

    # Generate a small test image
    image_bytes = _make_png()
    image_b64 = f"data:image/png;base64,{__import__('base64').b64encode(image_bytes).decode()}"

    print("\n=== Cloudflare Workers AI vision ===")
    caption = await _call_cf_caption(image_b64, settings)
    if caption:
        print(f"  CAPTION_RESULT: {caption}")
    else:
        print("  CAPTION_RESULT: none (Cloudflare not configured or call failed)")

    print("\n=== R2 presigned upload URL ===")
    presigned = r2_presigned.presigned_upload_url(
        team_id="smoke-team",
        filename="smoke.png",
        mime_type="image/png",
        size_bytes=1234,
    )
    if presigned:
        print(f"  PRESIGNED_URL: {presigned['upload_url'][:120]}...")
    else:
        print("  PRESIGNED_URL: none (S3 credentials not configured)")

    print("\n=== Full auto_tag_asset flow ===")
    async with async_session_maker() as db:
        # Find or create a test user and team
        result = await db.execute(select(User).limit(1))
        user = result.scalar_one_or_none()
        if not user:
            user = User(
                id=uuid.uuid4(),
                email="smoke@example.com",
                password_hash="x",
                name="Smoke Test",
            )
            db.add(user)
            await db.flush()
            team = Team(name="Smoke Team", owner_id=user.id)
            db.add(team)
            await db.flush()
            db.add(TeamMember(team_id=team.id, user_id=user.id))
        else:
            team = (await db.execute(select(Team).limit(1))).scalars().first()
            if not team:
                team = Team(name="Smoke Team", owner_id=user.id)
                db.add(team)
                await db.flush()

        date_folder = datetime.now(UTC).strftime("%Y/%m/%d")
        storage_path = f"{date_folder}/smoke_{uuid.uuid4().hex[:8]}.png"

        # Save the test image to local disk
        upload_dir = os.environ.get("UPLOAD_DIR", "/app/uploads")
        abs_path = __import__("pathlib").Path(upload_dir) / storage_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        async with __import__("aiofiles").open(str(abs_path), "wb") as f:
            await f.write(image_bytes)

        asset = MediaAsset(
            team_id=team.id,
            user_id=user.id,
            filename="smoke.png",
            mime_type="image/png",
            size_bytes=len(image_bytes),
            width=256,
            height=256,
            storage_backend="local",
            storage_path=storage_path,
            alt_text="",
            tags=[],
            source="upload",
        )
        db.add(asset)
        await db.commit()
        await db.refresh(asset)
        print(f"  CREATED_ASSET: {asset.id}")

        await media_ai.auto_tag_asset(asset.id)

        # Reload asset to see updated fields
        result = await db.execute(select(MediaAsset).where(MediaAsset.id == asset.id))
        asset = result.scalar_one()
        print(f"  AI_CAPTION: {asset.ai_caption}")
        print(f"  AI_TAGS: {asset.ai_tags}")
        print(f"  EMBEDDING_ID: {asset.embedding_id}")

        print("\n=== Chroma similar-assets query ===")
        try:
            similar = await media_ai.get_similar_assets(team.id, asset.id)
            print(f"  SIMILAR: {similar}")
        except Exception as exc:
            print(f"  SIMILAR_QUERY_FAILED: {exc}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
