#!/usr/bin/env python3
"""Recover an orphaned AI video render into a MediaAsset (+ post attach).

Failure signature this covers: ``generate_video_asset_task`` dies AFTER
ComfyUI produced the mp4 but BEFORE ``save_uploaded_media`` committed —
post-render ``SoftTimeLimitExceeded`` (e.g. a semantic-QA vision call
hanging on DMR cold-load, or an infra kill during the QA window). The
bytes are still on disk in the ComfyUI output volume, the post keeps a
stale ``pending_video_task`` marker, and the materialize loop will never
attach anything because the task id it tracks is dead.

Run inside ``social-api`` / any ``social-worker-*`` (app importable at
``/app``):

    # 1) get the file into the container
    docker cp /home/tbaltzakis/cu130-slim/storage-user/output/socialauto_00015.mp4 \
        social-worker-media:/tmp/render.mp4

    # 2) recover + attach (team/user are taken from the post)
    docker exec social-worker-media \
        python scripts/recover_rendered_video.py --file /tmp/render.mp4 --post c4dff316

Asset-only (no post):
    --team-id <uuid> --user-id <uuid>

Attach mirrors the ``report_videos`` materialize recipe exactly:
pops ``pending_video_task`` / ``video_render_attempts`` /
``video_render_dispatched_at`` / ``last_render_error``, sets
``meta_data.video_asset_id``, ``media_ids=[asset]``, ``status=SCHEDULED``;
``scheduled_at`` is only backfilled when NULL (unless ``--schedule`` is
given — use it to re-slot a missed window).

``--check <post-id-prefix>`` prints the post's pending-render state plus
the celery meta of the tracked task id (stale STARTED/not-found = orphan).

Safety: the attach step re-reads the post row ``FOR UPDATE`` after the
(asset) save commits and refuses to overwrite a ``video_asset_id`` or
media that appeared meanwhile (e.g. ``materialize_report_videos`` retry),
and refuses posts already PUBLISHING/PUBLISHED.

Asset creation goes through ``POST /api/v1/media/upload`` per the
platform-API rule (quality probe, provenance, phash wiring). ``--direct``
falls back to ``save_uploaded_media`` for when social-api itself is the
thing that's down.

Renders with no audio stream are refused unless ``--allow-silent`` is
given — Wan2.2 frame-combine output is silent and would publish muted.
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime

for _p in ("/app", "/home/tbaltzakis/cu130-slim/social-automation/backend"):
    if os.path.isdir(os.path.join(_p, "app")):
        sys.path.insert(0, _p)
        break

from sqlalchemy import String, cast, select  # noqa: E402

from app.db.session import async_session_maker  # noqa: E402
from app.models.content import MediaAsset, Post, PostStatus  # noqa: E402
from app.services.media_storage import save_uploaded_media  # noqa: E402

_PENDING_KEYS = ("pending_video_task", "video_render_attempts", "video_render_dispatched_at", "last_render_error")
_API_BASE = os.environ.get("SOCIAL_API_URL", "http://social-api:8000")


async def _resolve_post(db, prefix: str) -> Post:
    """Exactly one post must match — a short/shared prefix must never
    silently pick the wrong row."""
    rows = (await db.execute(select(Post).where(cast(Post.id, String).like(f"{prefix}%")))).scalars().all()
    if not rows:
        raise SystemExit(f"no post matching {prefix!r}")
    if len(rows) > 1:
        raise SystemExit(f"{len(rows)} posts match {prefix!r} ({', '.join(str(p.id) for p in rows[:5])}) — use a longer prefix")
    return rows[0]


def _upload_via_api(team_id, user_id, path: str, data: bytes, args) -> str:
    """POST /api/v1/media/upload with a short-lived JWT minted in-process —
    the platform-API path so the asset gets the video probe, quality
    metadata, and storage wiring the endpoint owns."""
    import httpx

    from app.core.security import create_access_token

    token = create_access_token({"sub": str(user_id)})
    files = {"file": (args.filename or os.path.basename(path), data, "video/mp4")}
    form = {
        "alt_text": args.alt_text or f"AI-generated video: {(args.prompt or '')[:120]}",
        "tags": args.tags or "comfyui,generated-video,recovered",
    }
    with httpx.Client(timeout=120) as client:
        r = client.post(
            f"{_API_BASE}/api/v1/media/upload",
            params={"team_id": str(team_id)},
            files=files,
            data=form,
            headers={"Authorization": f"Bearer {token}"},
        )
    if r.status_code != 201:
        raise SystemExit(f"media upload API failed: HTTP {r.status_code} {r.text[:200]}")
    return str(r.json()["id"])


def _probe(path: str) -> tuple[int | None, int | None, float | None, bool]:
    """Best-effort ffprobe -> (width, height, duration_seconds, has_audio)."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", "-show_format", path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        info = json.loads(out.stdout or "{}")
        vstream = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
        has_audio = any(s.get("codec_type") == "audio" for s in info.get("streams", []))
        return (
            vstream.get("width"),
            vstream.get("height"),
            float(info.get("format", {}).get("duration") or 0) or None,
            has_audio,
        )
    except Exception:  # noqa: BLE001
        return None, None, None, False


async def _check(post_prefix: str) -> None:
    async with async_session_maker() as db:
        post = await _resolve_post(db, post_prefix)
        meta = post.meta_data or {}
        task_id = meta.get("pending_video_task")
        print(f"post {post.id}  status={post.status.value}  media_ids={post.media_ids}")
        print(f"  scheduled_at={post.scheduled_at}  pending_video_task={task_id}")
        print(f"  attempts={meta.get('video_render_attempts')}  last_error={meta.get('last_render_error')}")
        print(f"  video_asset_id={meta.get('video_asset_id')}")
    if task_id:
        try:
            from celery.result import AsyncResult

            from app.worker.celery_app import celery_app

            res = AsyncResult(task_id, app=celery_app)
            print(f"  task {task_id}: state={res.state} (stale STARTED/not-found = orphaned render)")
        except Exception as exc:  # noqa: BLE001
            print(f"  task {task_id}: meta lookup failed ({type(exc).__name__}) — likely orphaned")


async def _recover(args: argparse.Namespace) -> None:
    path = args.file
    data = open(path, "rb").read()
    if len(data) < 1024:
        raise SystemExit(f"refusing: {path} is only {len(data)} bytes — not a rendered video")
    width, height, duration, has_audio = _probe(path)
    print(f"file {path}: {len(data)} bytes  {width}x{height}  {duration}s  audio={'yes' if has_audio else 'NO'}")
    if not has_audio and not args.allow_silent:
        raise SystemExit(
            "render has no audio stream — Wan2.2 frame-combine output is silent. Mux audio first, or pass --allow-silent if the publish path adds music."
        )

    async with async_session_maker() as db:
        post = None
        if args.post:
            post = await _resolve_post(db, args.post)
            team_id, user_id = post.team_id, post.user_id
            print(f"post {post.id}: status={post.status.value} team={team_id} user={user_id}")
        else:
            if not (args.team_id and args.user_id):
                raise SystemExit("--team-id and --user-id are required without --post")
            team_id, user_id = uuid.UUID(args.team_id), uuid.UUID(args.user_id)

        if args.dry_run:
            print("dry-run: would upload + attach; nothing written")
            return

        if args.direct:
            asset = await save_uploaded_media(
                db,
                team_id=team_id,
                user_id=user_id,
                original_filename=args.filename or os.path.basename(path),
                content=data,
                mime_type="video/mp4",
                alt_text=args.alt_text or f"AI-generated video: {(args.prompt or '')[:120]}",
                tags=args.tags.split(",") if args.tags else ["comfyui", "generated-video", "recovered"],
                width=width,
                height=height,
            )
            asset.source = args.source  # String(20) — keep short
            if args.prompt:
                asset.generation_prompt = args.prompt
            if duration:
                asset.duration_seconds = int(round(duration))
            meta = dict(asset.meta_data or {})
            meta["semantic_qa"] = {"match": True, "reason": "manually recovered — QA skipped", "vision": None}
            meta["recovered_from"] = path
            meta["recovered_at"] = datetime.now(UTC).isoformat()
            asset.meta_data = meta
            await db.commit()
            asset_id = asset.id
            print(f"asset {asset_id} saved via --direct (source={asset.source})")
        else:
            asset_id = await asyncio.to_thread(_upload_via_api, team_id, user_id, path, data, args)
            # Annotate provenance on the API-created row — creation itself
            # already went through the platform path (probe/quality/phash).
            asset = await db.get(MediaAsset, uuid.UUID(asset_id))
            meta = dict(asset.meta_data or {})
            meta["semantic_qa"] = {"match": True, "reason": "manually recovered — QA skipped", "vision": None}
            meta["recovered_from"] = path
            meta["recovered_at"] = datetime.now(UTC).isoformat()
            if args.source:
                meta["recovered_source"] = args.source
            asset.meta_data = meta
            await db.commit()
            print(f"asset {asset_id} uploaded via /api/v1/media/upload")

        if post is not None:
            # Re-read the row under a lock: the asset save just committed,
            # and materialize_report_videos may have attached newer media in
            # the meantime — refuse to overwrite it.
            fresh = (await db.execute(select(Post).where(Post.id == post.id).with_for_update().execution_options(populate_existing=True))).scalars().first()
            if fresh is None:
                raise SystemExit(f"post {post.id} vanished during recovery — asset {asset_id} saved unattached")
            if fresh.status in (PostStatus.PUBLISHING, PostStatus.PUBLISHED):
                raise SystemExit(
                    f"post {fresh.id} is {fresh.status.value} — refusing to rewrite media on a post that already shipped (asset {asset_id} saved unattached)"
                )
            fm = dict(fresh.meta_data or {})
            existing = fm.get("video_asset_id")
            if existing and str(existing) != str(asset_id):
                raise SystemExit(
                    f"post {fresh.id} already has video_asset_id={existing} — refusing to overwrite newer media (asset {asset_id} saved unattached)"
                )
            for k in _PENDING_KEYS:
                fm.pop(k, None)
            fm["video_asset_id"] = str(asset_id)
            fresh.meta_data = fm
            fresh.media_ids = [uuid.UUID(str(asset_id))]
            fresh.status = PostStatus.SCHEDULED
            if args.schedule:
                fresh.scheduled_at = datetime.fromisoformat(args.schedule)
            elif fresh.scheduled_at is None:
                from datetime import timedelta

                fresh.scheduled_at = datetime.now(UTC) + timedelta(hours=2)
            await db.commit()
            print(f"post {fresh.id}: status=SCHEDULED media_ids=[{asset_id}] scheduled_at={fresh.scheduled_at}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", help="rendered mp4 path as visible inside the container")
    ap.add_argument("--post", help="post id (prefix ok) to attach the recovered asset to")
    ap.add_argument("--check", metavar="POST_PREFIX", help="inspect a post's pending-render state and exit")
    ap.add_argument("--team-id"), ap.add_argument("--user-id")
    ap.add_argument("--filename", help="stored filename (default: basename of --file)")
    ap.add_argument("--source", default="comfyui-wan22", help="MediaAsset.source — max 20 chars")
    ap.add_argument("--prompt", help="generation prompt to record on the asset")
    ap.add_argument("--alt-text"), ap.add_argument("--tags", help="comma-separated")
    ap.add_argument("--schedule", help="ISO datetime to re-slot the post (default: keep, or +2h if NULL)")
    ap.add_argument("--allow-silent", action="store_true", help="permit a render with no audio stream")
    ap.add_argument(
        "--direct",
        action="store_true",
        help="bypass POST /api/v1/media/upload and write via save_uploaded_media (escape hatch for when social-api is down); default is the platform API",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.check:
        asyncio.run(_check(args.check))
        return
    if not args.file:
        ap.error("--file is required (or use --check)")
    asyncio.run(_recover(args))


if __name__ == "__main__":
    main()
