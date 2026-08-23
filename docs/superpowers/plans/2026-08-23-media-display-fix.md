# Media Display Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the media library display so that uploaded media appears correctly in the UI, organized by date folders, and accessible via proper URLs.

**Architecture:** 
- Store uploaded files in subfolders under the upload directory based on UTC date (year/month/day) with UUID filenames.
- Serve these static files via a FastAPI staticfiles mount at `/api/v1/uploads/{path:path}`.
- Update the media API to store the relative path (from upload root) in the `storage_path` field.
- Modify the frontend MediaPage to construct the media URL using the API base and the stored relative path.

**Tech Stack:** 
- Backend: Python/FastAPI (social-automation/backend)
- Frontend: TypeScript/Next.js (social-automation/frontend)
- Storage: Local filesystem (upload directory)

## Global Constraints
- The upload directory defaults to `/app/uploads` (from UPLOAD_DIR env var).
- The API base URL is provided via `NEXT_PUBLIC_API_URL` in the frontend.
- Media items are already scoped to the user's team via the API.
- Filenames are UUID-based and unguessable, allowing public access without additional auth.
- Existing media items in the database may need migration to the new path structure.

---

### Task 1: Backend - Update upload endpoint to store in date folders

**Files:**
- Modify: `social-automation/backend/app/api/media.py:70-100` (the upload_media function)

**Interfaces:**
- Consumes: Uploaded file, alt_text, tags, current_user, db session
- Produces: MediaAsset with updated storage_path (relative path under upload root)

- [ ] **Step 1: Write the failing test** (conceptual; we'll rely on existing tests)
  No new test needed; we will manually verify.

- [ ] **Step 2: Modify upload_media function**
  ```python
  import os
  from datetime import datetime
  import uuid
  # ... existing imports ...

  UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "/app/uploads")

  @router.post("/upload", response_model=MediaAssetResponse, status_code=status.HTTP_201_CREATED)
  async def upload_media(
      file: UploadFile = File(...),
      alt_text: str = Form(None),
      tags: str = Form(""),
      current_user: User = Depends(get_current_user),
      db: AsyncSession = Depends(get_db),
  ):
      result = await db.execute(
          select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
      )
      team = result.scalars().first()
      if not team:
          raise HTTPException(status_code=400, detail="No team found")

      # Determine date-based subfolder
      now = datetime.utcnow()
      date_folder = now.strftime("%Y/%m/%d")
      # Ensure the directory exists
      target_dir = os.path.join(UPLOAD_DIR, date_folder)
      os.makedirs(target_dir, exist_ok=True)

      # Generate unique filename
      file_ext = os.path.splitext(file.filename)[1] if file.filename else ""
      filename = f"{uuid.uuid4()}{file_ext}"
      # Relative path from UPLOAD_DIR (for storage)
      relative_path = os.path.join(date_folder, filename)
      # Absolute disk path
      storage_path = os.path.join(UPLOAD_DIR, relative_path)

      # Read file content
      content = await file.read()

      # Write file to disk
      async with aiofiles.open(storage_path, "wb") as f:
          await f.write(content)

      asset = MediaAsset(
          team_id=team.id,
          user_id=current_user.id,
          filename=file.filename,
          mime_type=file.content_type,
          size_bytes=len(content),
          storage_path=relative_path,  # Store relative path
          alt_text=alt_text,
          tags=tags.split(",") if tags else [],
          source="upload",
      )
      db.add(asset)
      await db.commit()
      await db.refresh(asset)

      return asset
  ```

- [ ] **Step 3: Run the backend to verify no syntax errors**
  Start the backend container (or run uvicorn) and check for import errors.

- [ ] **Step 4: Commit**
  ```bash
  git add social-automation/backend/app/api/media.py
  git commit -m "feat: store uploads in date subfolders, store relative path"
  ```

---

### Task 2: Backend - Update generate-image endpoint to store in date folders

**Files:**
- Modify: `social-automation/backend/app/api/media.py:150-180` (the generate_image function)

**Interfaces:**
- Consumes: prompt, workflow_json, current_user, db session
- Produces: MediaAsset with storage_path set to the generated image path (after we implement actual generation; for now we'll simulate by creating a dummy file)

- [ ] **Step 1: Write the failing test** (skip for now)

- [ ] **Step 2: Modify generate_image function**
  We'll need to actually generate an image via ComfyUI or create a placeholder. For simplicity, we'll create a small placeholder PNG and store it in the date folder.
  ```python
  from datetime import datetime
  import os
  import uuid
  import json
  from PIL import Image  # Might need to install pillow; alternatively create a tiny PNG binary

  # Inside generate_image function:
      # ... after getting team ...
      now = datetime.utcnow()
      date_folder = now.strftime("%Y/%m/%d")
      target_dir = os.path.join(UPLOAD_DIR, date_folder)
      os.makedirs(target_dir, exist_ok=True)

      filename = f"generated_{uuid.uuid4().hex[:8]}.png"
      relative_path = os.path.join(date_folder, filename)
      storage_path = os.path.join(UPLOAD_DIR, relative_path)

      # Create a placeholder image (1x1 white pixel) if we don't have real generation
      # For now, we'll write a minimal PNG binary (1x1 white)
      placeholder_png = bytes([
          0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A,
          0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52,
          0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,
          0x08, 0x02, 0x00, 0x00, 0x00, 0x90, 0x77, 0x53,
          0xDE, 0x00, 0x00, 0x00, 0x0C, 0x49, 0x44, 0x41,
  0x54, 0x78, 0x9C, 0x63, 0x60, 0x00, 0x00, 0x00,
          0x02, 0x00, 0x01, 0xE2, 0x21, 0xBC, 0x33, 0x0D,
          0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4E, 0x4D,
          0xAE, 0x42, 0x60, 0x82
      ])
      async with aiofiles.open(storage_path, "wb") as f:
          await f.write(placeholder_png)

      asset = MediaAsset(
          team_id=team.id,
          user_id=current_user.id,
          filename=filename,
          mime_type="image/png",
          size_bytes=len(placeholder_png),
          storage_path=relative_path,
          alt_text=prompt,
          tags=["ai-generated"],
          source="comfyui",
          generation_prompt=prompt,
          comfyui_workflow_json=json.loads(workflow_json) if workflow_json else None,
      )
      db.add(asset)
      await db.commit()
      await db.refresh(asset)

      return asset
  ```
  Note: We'll need to add Pillow to requirements if we want to do real image generation later; for now placeholder is fine.

- [ ] **Step 3: Run the backend to verify no syntax errors**

- [ ] **Step 4: Commit**
  ```bash
  git add social-automation/backend/app/api/media.py
  git commit -m "feat: update generate-image to store in date subfolders"
  ```

---

### Task 3: Backend - Add static file serving for uploads

**Files:**
- Modify: `social-automation/backend/app/main.py` (to mount static files)
- Possibly: create a directory if needed (but upload directory already exists via env)

**Interfaces:**
- Consumes: None
- Produces: Static file serving at `/api/v1/uploads/{path:path}`

- [ ] **Step 1: Write the failing test** (we'll test by accessing a known file)

- [ ] **Step 2: Modify main.py to mount static files**
  ```python
  from fastapi.staticfiles import StaticFiles
  import os

  # Inside the lifespan or after app creation
  UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "/app/uploads")
  # Mount the uploads directory under /api/v1/uploads
  app.mount("/api/v1/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
  ```

- [ ] **Step 3: Run the backend and verify static serving works**
  Start the backend, place a test file in UPLOAD_DIR, and curl `http://localhost:8001/api/v1/uploads/test.txt` (adjust port as needed).

- [ ] **Step 4: Commit**
  ```bash
  git add social-automation/backend/app/main.py
  git commit -m "feat: add static file serving for uploads under /api/v1/uploads"
  ```

---

### Task 4: Frontend - Modify MediaPage to construct media URL

**Files:**
- Modify: `social-automation/frontend/app/(dashboard)/media/page.tsx` (the img src usage)

**Interfaces:**
- Consumes: item.storage_path (relative path)
- Produces: <img src> pointing to the correct URL

- [ ] **Step 1: Write the failing test** (we'll verify manually)

- [ ] **Step 2: Update the img src line**
  Replace:
  ```tsx
  {item.storage_path ? (
    <img
      src={item.storage_path}
      alt={item.filename || 'Media'}
      className="h-full w-full object-cover transition-transform duration-200 group-hover:scale-105"
    />
  ) : ( ... )}
  ```
  With:
  ```tsx
  {item.storage_path ? (
    <img
      src={`${process.env.NEXT_PUBLIC_API_URL}/uploads/${item.storage_path}`}
      alt={item.filename || 'Media'}
      className="h-full w-full object-cover transition-transform duration-200 group-hover:scale-105"
    />
  ) : ( ... )}
  ```
  Note: We need to ensure there is no double slash. Since NEXT_PUBLIC_API_URL ends with `/api/v1`, the resulting URL will be `http://localhost:8083/api/v1/uploads/...` which matches our mount point.

- [ ] **Step 3: Run the frontend and verify media images load**
  Start the frontend, upload an image, and check that the image appears.

- [ ] **Step 4: Commit**
  ```bash
  git add social-automation/frontend/app/(dashboard)/media/page.tsx
  git commit -m "feat: construct media URL using API base and uploads path"
  ```

---

### Task 5: (Optional) Data migration script to move existing files to date folders and update storage_path

**Files:**
- Create: `social-automation/backend/scripts/migrate_media_to_datefolders.py`

**Interfaces:**
- Consumes: None (runs against the database and filesystem)
- Produces: Media items with updated storage_path and files moved to date subfolders

- [ ] **Step 1: Write the migration script**
  ```python
  import os
  import re
  from datetime import datetime
  from sqlalchemy import select, update
  from app.db.session import get_db
  from app.models.content import MediaAsset
  from app.core.config import get_settings

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
  ```

- [ ] **Step 2: Run the migration script** (after backing up data)

- [ ] **Step 3: Commit**
  ```bash
  git add social-automation/backend/scripts/migrate_media_to_datefolders.py
  git commit -m "feat: add migration script for media to date folders"
  ```

---

### Task 6: Test end-to-end media upload and display

**Files:**
- None (manual testing)

**Interfaces:**
- None

- [ ] **Step 1: Start the full stack (backend, frontend, any dependencies)**
  Use docker-compose or manual processes.

- [ ] **Step 2: Upload an image via the media library UI**
  Verify that the image appears in the grid.

- [ ] **Step 3: Right-click → Open image in new tab (or inspect the img src)**
  Confirm the URL is like `http://localhost:8083/api/v1/uploads/2026/08/23/<uuid>.png` and that the image loads.

- [ ] **Step 4: Upload a video and verify it works (if applicable)**
- [ ] **Step 5: Generate an AI image and verify it appears**

- [ ] **Step 6: Commit any test adjustments or documentation**
  ```bash
  git commit -m "test: verify media display fix works end-to-end"
  ```