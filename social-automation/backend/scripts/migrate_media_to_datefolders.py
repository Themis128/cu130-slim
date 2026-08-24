import os
from datetime import datetime

from sqlalchemy import select, update

from app.db.session import get_db
from app.models.content import MediaAsset


async def migrate():
    async for db in get_db():
        result = await db.execute(select(MediaAsset))
        assets = result.scalars().all()
        upload_dir = os.environ.get("UPLOAD_DIR", "/app/uploads")
        for asset in assets:
            old_path = asset.storage_path  # This is currently an absolute path like "/app/uploads/uuid.ext"
            if not old_path or not os.path.isabs(old_path):
                continue  # already migrated or invalid
            # Extract filename
            filename = os.path.basename(old_path)
            # Determine date from asset.created_at (fallback to today if not set)
            dt = asset.created_at or datetime.utcnow()
            date_folder = dt.strftime("%Y/%m/%d")
            # New relative path
            new_relative = os.path.join(date_folder, filename)
            new_abs = os.path.join(upload_dir, new_relative)
            # Ensure directory exists
            os.makedirs(os.path.dirname(new_abs), exist_ok=True)
            # Move file if it exists and is not already in the right place
            if os.path.exists(old_path) and old_path != new_abs:
                os.rename(old_path, new_abs)
            # Update database
            await db.execute(
                update(MediaAsset)
                .where(MediaAsset.id == asset.id)
                .values(storage_path=new_relative)
            )
        await db.commit()
        break

if __name__ == "__main__":
    import asyncio
    asyncio.run(migrate())
