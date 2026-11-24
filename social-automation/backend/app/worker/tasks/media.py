"""Celery tasks for the media library."""

import asyncio
import logging
import time
import uuid

from celery import shared_task

from app.services import comfyui_video, gpu_arbiter, media_ai
from app.services.db_sync import sync_after_worker_task
from app.services.media_storage import save_uploaded_media
from app.worker._async import get_session_factory, run_async, task_session

celery_app = __import__("app.worker.celery_app", fromlist=["celery_app"]).celery_app

logger = logging.getLogger(__name__)


celery_app.set_default()
celery_app.set_current()

# Mirrors the generate_video_asset_task annotation in celery_app.py — the
# QA-retry gate needs the task's soft budget to decide whether a second
# render can still fit. Keep in sync with the task_annotations entry.
_TASK_SOFT_LIMIT_S = 2700
# DMR vision cold-loads run for many minutes; QA is advisory metadata and
# must never consume the render task's time budget (2026-11-16: a finished
# Wan2.2 render was discarded when the QA vision call ate the soft limit).
_SEMANTIC_QA_TIMEOUT_S = 180
# Headroom for a second render + re-QA under the task soft limit.
_QA_RETRY_MARGIN_S = 120


@shared_task
def auto_tag_asset_task(asset_id: str) -> None:
    """Run AI auto-tagging for a media asset."""
    try:
        asset_uuid = uuid.UUID(asset_id)
    except ValueError:
        logger.warning("auto_tag_asset_task: invalid asset_id %s", asset_id)
        return

    async def _run() -> None:
        # NullPool: run_async() creates a fresh loop per task; pooled
        # connections from the shared engine bind to the creating loop.
        await media_ai.auto_tag_asset(asset_uuid, session_factory=get_session_factory())

    try:
        run_async(_run())
        # Push worker writes (media_assets) to D1 primary
        run_async(sync_after_worker_task(["media_assets"]))
    except Exception as exc:
        logger.warning("auto_tag_asset_task failed for %s: %s", asset_id, exc)


@shared_task(bind=True)
def generate_video_asset_task(self, team_id: str, user_id: str, prompt: str, options: dict | None = None) -> str:
    """Generate a video via ComfyUI (LTX-Video 2B GGUF) and store a MediaAsset.

    Returns the new media asset id as a string so the API can hand it back
    through the Celery result backend.
    """
    options = options or {}
    task_key = self.request.id

    async def _run() -> str:
        # Redelivery checkpoint: a worker death after _persist committed
        # returns the finished asset instead of re-rendering (~40 min GPU).
        cached = await comfyui_video.resume_get(task_key) if task_key else None
        if cached and cached.get("asset_id"):
            logger.info("[video-task] redelivery with finished asset %s — no re-render", cached["asset_id"])
            return cached["asset_id"]
        started = time.monotonic()
        # Expand the terse prompt into a detailed scene description BEFORE
        # the GPU lock — the call runs on the warm DMR mid model.
        expanded_prompt = await media_ai.expand_visual_prompt(prompt, media_type="video")
        async with task_session() as db:
            num_frames = int(options.get("num_frames") or 81)
            frame_rate = int(options.get("frame_rate") or 24)
            # Long-form path: explicit shot list, or duration_seconds split
            # into per-segment prompts (Creator Rewards needs 60s+).
            scene_prompts = options.get("scene_prompts") or []
            duration = int(options.get("duration_seconds") or 0)
            if not scene_prompts and duration > 0:
                seg_len = num_frames / float(frame_rate)
                n = max(1, round(duration / seg_len))
                scene_prompts = [expanded_prompt] * n
            model = options.get("model") or "wan22"
            image_bytes = None
            image_asset_id = options.get("image_asset_id")
            if image_asset_id:
                from app.models.content import MediaAsset

                src = await db.get(MediaAsset, uuid.UUID(image_asset_id))
                if not src:
                    raise ValueError(f"image_asset_id {image_asset_id} not found")
                image_bytes = await media_ai._load_image_bytes(src)
                if not image_bytes:
                    raise ValueError(f"could not load bytes for asset {image_asset_id}")

            async def _persist(vdata: bytes, vmeta: dict):
                """Store rendered bytes + create the MediaAsset row — the
                durable step. Must run before any optional post-processing."""
                asset = await save_uploaded_media(
                    db,
                    team_id=uuid.UUID(team_id),
                    user_id=uuid.UUID(user_id),
                    original_filename=vmeta["filename"],
                    content=vdata,
                    mime_type="video/mp4",
                    alt_text=options.get("alt_text") or f"AI-generated video: {prompt[:120]}",
                    tags=options.get("tags") or ["comfyui", vmeta.get("model", "wan22"), "generated-video"],
                    width=vmeta["width"],
                    height=vmeta["height"],
                )
                asset.source = f"comfyui-{vmeta.get('model', 'wan22')}"
                asset.generation_prompt = prompt
                asset.duration_seconds = int(round(vmeta["duration_seconds"]))
                meta_dict = dict(asset.meta_data or {})
                if expanded_prompt != prompt:
                    meta_dict["expanded_prompt"] = expanded_prompt
                asset.meta_data = meta_dict
                await db.commit()
                return asset

            def _qa_budget_s() -> float:
                """Seconds the QA wait_for may use without crossing the task
                soft limit — reserves 60s for the final commit/return."""
                return max(
                    0.0,
                    _TASK_SOFT_LIMIT_S - (time.monotonic() - started) - 60.0,
                )

            async def _qa_frame(vdata: bytes) -> dict:
                """Extract frame 0 + bounded semantic QA. Non-raising: DMR
                vision cold-loads can exceed the remaining task budget, so the
                call is wrapped in wait_for and every failure degrades to
                advisory metadata instead of discarding the render."""
                budget = _qa_budget_s()
                if budget < 15.0:
                    return {"match": True, "caption": None, "reason": "not run: no budget", "vision": None}
                timeout = min(_SEMANTIC_QA_TIMEOUT_S, budget)
                try:
                    import subprocess
                    import tempfile
                    from pathlib import Path

                    with tempfile.TemporaryDirectory() as td:
                        src_mp4 = Path(td) / "clip.mp4"
                        frame_png = Path(td) / "f0.png"
                        src_mp4.write_bytes(vdata)
                        proc = await asyncio.to_thread(
                            subprocess.run,
                            ["ffmpeg", "-y", "-i", str(src_mp4), "-frames:v", "1", str(frame_png)],
                            capture_output=True,
                            timeout=60,
                        )
                        if proc.returncode == 0 and frame_png.exists():
                            return await asyncio.wait_for(
                                media_ai.verify_media_semantics(frame_png.read_bytes(), prompt, media_type="video"),
                                timeout=timeout,
                            )
                except TimeoutError:
                    logger.warning("[video-task] semantic QA timed out (%ss)", timeout)
                    return {"match": True, "caption": None, "reason": "qa timeout", "vision": None}
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[video-task] semantic QA skipped: %s", type(exc).__name__)
                return {"match": True, "caption": None, "reason": "not run", "vision": None}

            # One-model-in-VRAM: hold the GPU lock for the render so
            # DMR can't load a model mid-job (8GB card).
            render_started = time.monotonic()
            async with gpu_arbiter.media_gpu_lock():
                if scene_prompts:
                    data, meta = await comfyui_video.generate_video_segments(
                        prompts=[p if (p or "").strip() else expanded_prompt for p in scene_prompts],
                        negative_prompt=options.get("negative_prompt"),
                        width=int(options.get("width") or 704),
                        height=int(options.get("height") or 1280),
                        num_frames=num_frames,
                        frame_rate=frame_rate,
                        steps=int(options.get("steps") or 20),
                        cfg=float(options.get("cfg_scale") or 5.0),
                        seed=options.get("seed"),
                        filename_prefix="socialauto",
                        model=model,
                    )
                else:
                    data, meta = await comfyui_video.generate_video(
                        prompt=expanded_prompt,
                        negative_prompt=options.get("negative_prompt"),
                        width=int(options.get("width") or 704),
                        height=int(options.get("height") or 1280),
                        num_frames=num_frames,
                        frame_rate=frame_rate,
                        steps=int(options.get("steps") or 20),
                        cfg=float(options.get("cfg_scale") or 5.0),
                        seed=options.get("seed"),
                        filename_prefix="socialauto",
                        model=model,
                        image_bytes=image_bytes,
                        i2v_strength=float(options.get("i2v_strength") or 1.0),
                        resume_key=task_key,
                    )
            render_elapsed = time.monotonic() - render_started

            # Persist the render BEFORE semantic QA — the bytes are the
            # expensive product; QA only annotates metadata afterwards.
            asset = await _persist(data, meta)
            if task_key:
                await comfyui_video.resume_put(task_key, {"asset_id": str(asset.id), "done": True}, ttl_s=14400)

            # Semantic QA on frame 0 — DMR vision, AFTER the GPU lock was
            # released (the vision model reloads through the arbiter).
            qa = await _qa_frame(data)

            if qa.get("match") is False:
                # A second render costs ~another render_elapsed — only attempt
                # it when it can still fit under the task soft limit.
                retry_cost = render_elapsed + _SEMANTIC_QA_TIMEOUT_S + _QA_RETRY_MARGIN_S
                if time.monotonic() - started + retry_cost < _TASK_SOFT_LIMIT_S:
                    logger.warning("[video-task] QA mismatch (%s) — retrying once", qa.get("reason"))
                    retry_prompt = f"{expanded_prompt}. Depict: {prompt[:200]}. Avoid: {qa.get('reason', 'wrong subject')}"
                    if task_key:
                        # Clear the first render's prompt_id or the retry
                        # would "resume" the finished job and re-fetch it.
                        await comfyui_video.resume_put(task_key, {"prompt_id": None}, ttl_s=14400)
                    try:
                        async with gpu_arbiter.media_gpu_lock():
                            data, meta = await comfyui_video.generate_video(
                                prompt=retry_prompt,
                                negative_prompt=options.get("negative_prompt"),
                                width=int(options.get("width") or 704),
                                height=int(options.get("height") or 1280),
                                num_frames=num_frames,
                                frame_rate=frame_rate,
                                steps=int(options.get("steps") or 20),
                                cfg=float(options.get("cfg_scale") or 5.0),
                                seed=None,
                                filename_prefix="socialauto",
                                model=model,
                                image_bytes=image_bytes,
                                i2v_strength=float(options.get("i2v_strength") or 1.0),
                                resume_key=task_key,
                            )
                        # Persist the retry render BEFORE its QA — same rule as
                        # the first pass: the bytes are the expensive product,
                        # and a soft-limit kill during QA must not lose them.
                        previous = asset
                        asset = await _persist(data, meta)
                        if task_key:
                            await comfyui_video.resume_put(task_key, {"asset_id": str(asset.id), "done": True}, ttl_s=14400)
                        retry_qa = await _qa_frame(data)
                        # Keep the first verdict when the retry's QA couldn't
                        # produce one — a fake "not run" pass would erase a
                        # real mismatch.
                        if retry_qa.get("reason") not in ("not run", "qa timeout"):
                            qa = retry_qa
                        prev_meta = dict(previous.meta_data or {})
                        prev_meta["superseded_by"] = str(asset.id)
                        previous.meta_data = prev_meta
                    except Exception as exc:  # noqa: BLE001
                        # A failed _persist leaves the session in a pending-
                        # rollback state — clear it so the final meta commit
                        # doesn't die with PendingRollbackError.
                        await db.rollback()
                        logger.warning("[video-task] QA retry render failed: %s", type(exc).__name__)
                        qa = {**qa, "retry": f"failed: {type(exc).__name__}"}
                else:
                    logger.warning(
                        "[video-task] QA mismatch (%s) — retry skipped, insufficient task budget",
                        qa.get("reason"),
                    )
                    qa = {**qa, "retry": "skipped: insufficient task budget"}

            meta_dict = dict(asset.meta_data or {})
            meta_dict["semantic_qa"] = qa
            asset.meta_data = meta_dict
            await db.commit()
            return str(asset.id)

    asset_id = run_async(_run())
    run_async(sync_after_worker_task(["media_assets"]))
    return asset_id
