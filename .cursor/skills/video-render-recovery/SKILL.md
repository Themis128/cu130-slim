---
name: video-render-recovery
description: >-
  Recover an AI video that rendered successfully but never became a
  MediaAsset — the generate_video_asset_task died AFTER ComfyUI produced
  the mp4 but BEFORE save_uploaded_media committed (post-render
  SoftTimeLimitExceeded on semantic-QA cold-load, or an infra kill in the
  QA window). Detects the orphan signature and reattaches the bytes via
  backend/scripts/recover_rendered_video.py without burning a second
  ~40-min render. Use for "video failed but ComfyUI shows success",
  posts stuck on pending_video_task, or a rendered file in
  storage-user/output with no asset row.
allowed-tools:
  - read
  - exec
  - grep
triggers:
  - user
  - model
---

# Video render recovery

Read `ai-video-ops` for the pipeline; this skill is the rescue lane.

## The orphan signature (verified 2026-11-16)

All true at once:

- The Celery task failed — `SoftTimeLimitExceeded` / worker kill / revoked.
- ComfyUI side says **success** — `GET http://stack-ops:8199/history/<prompt_id>`
  shows `status.success` and `socialauto_XXXXX.mp4` sits in
  `~/cu130-slim/storage-user/output/` (the ComfyUI output bind mount).
- `media_assets` has **no row** for it — `save_uploaded_media` never ran.
- The post keeps `meta_data.pending_video_task` pointing at the dead task;
  the `report_videos` materialize loop polls that id forever and never
  attaches anything.

Root cause on master (pre-PR #2074): semantic QA (`verify_media_semantics`
→ DMR qwen3-vl) ran between "bytes downloaded" and "asset committed". A
DMR cold-load after the GPU arbiter released the render lock could eat
the remaining task budget — dead task, orphaned bytes. Post-#2074 the
asset commits BEFORE QA, so this class should stop occurring; the tool
below is the cleanup for anything that slipped through.

## Diagnose

```bash
# post's pending state + whether its tracked task is stale
docker exec social-worker-media \
    python scripts/recover_rendered_video.py --check c4dff316

# comfyui output inventory (newest first)
ls -lt ~/cu130-slim/storage-user/output/*.mp4 | head
```

Stale `STARTED`/not-found celery meta + an mp4 newer than the task's
dispatch = orphaned render.

## Recover (verified end-to-end 2026-11-16 — asset 37d483f0, post scheduled)

```bash
docker cp ~/cu130-slim/storage-user/output/socialauto_00015.mp4 \
    social-worker-media:/tmp/render.mp4
docker exec social-worker-media \
    python scripts/recover_rendered_video.py --file /tmp/render.mp4 --post c4dff316
```

The script:

1. ffprobes width/height/duration (best-effort).
2. `save_uploaded_media` — real storage path (R2→MinIO→local) + committed
   `MediaAsset`; then sets `source` (`comfyui-wan22`, ≤20 chars),
   `generation_prompt`, `duration_seconds`, and marks `meta_data
   .semantic_qa` as manually-recovered (advisory, not a fabricated pass).
3. Attaches to the post exactly like the `report_videos` materialize
   loop: pops `pending_video_task`/`video_render_attempts`/
   `video_render_dispatched_at`/`last_render_error`, sets
   `meta_data.video_asset_id`, `media_ids=[asset]`, `status=SCHEDULED`;
   `scheduled_at` untouched unless NULL (then +2h) or `--schedule` given.

Flags: `--team-id/--user-id` (asset-only, no post), `--prompt`,
`--alt-text`, `--tags`, `--filename`, `--source`, `--schedule ISO`,
`--dry-run`.

## Rules

- **Never re-render what already rendered** — a Wan2.2 pass is ~40 min of
  GPU. Recover first, re-render only when the file is gone/corrupt.
- `MediaAsset.source` is `String(20)` — keep the label short.
- The recovered asset is advisory-QA-exempt by construction; if you want
  real QA run `media-qa-jupyter` on the asset afterwards.
- If `pending_video_task` points at a LIVE task, wait — the render is
  still legitimately in flight.
