---
name: external-call-budget
description: >-
  The call-budget contract for work inside Celery tasks: every external
  call (HTTP, sidecar, DMR, ffmpeg, platform API) must carry a per-call
  timeout well below the task's soft limit, expensive outputs persist
  BEFORE optional post-processing, advisory analysis never gates the
  result, and retries are budget-gated (elapsed + estimated + margin <
  soft limit). Apply when writing/reviewing worker tasks or when a task
  dies after succeeding (post-render timeout, publish stuck on a wedged
  sidecar call). Proven twice 2026-11-16: orphaned Wan2.2 render and a
  20-min publish hold.
allowed-tools:
  - read
  - grep
triggers:
  - user
  - model
---

# External call budget (Celery task contract)

A task's `soft_time_limit` is a safety net, not a plan. Every external
call inside a task must be individually bounded so no single dependency
can consume the whole budget — otherwise the task dies with its work
half-committed.

## The five rules

1. **Per-call timeout < soft limit, always.** No bare `httpx` calls with
   a shared client default that can exceed the remaining budget. Prefer
   `httpx.Timeout(read=X, connect=Y)` — connect and read are different
   phases (a cold model load is a *read* stall; a dead host is a
   *connect* stall). One `timeout=300` on a sidecar op means the task
   can sit 5+ min on a wedged browser before the fallback even runs.
2. **Persist before you analyze.** Anything expensive to produce
   (renders, uploads, generated media) commits to durable storage the
   moment it exists. Optional post-processing (QA, enrichment,
   tagging) runs on the persisted artifact, never between produce and
   persist. Counter-example fixed 2026-11-16: `generate_video_asset_task`
   ran semantic QA before `save_uploaded_media` → DMR cold-load ate the
   2700 s soft limit → a finished render was discarded.
3. **Advisory steps fail soft and annotated.** QA/classification/
   enrichment returning "unavailable"/`"qa timeout"` is metadata, not a
   task failure. Wrap in `asyncio.wait_for` + try/except; store the
   degraded verdict; never let an optional dependency kill real output.
4. **Retries are budget-gated.** Before an in-task retry: `elapsed +
   estimated_cost + margin < soft_limit`, else mark `skipped` and keep
   the first result. A second Wan2.2 render inside a task that just
   spent ~45 min rendering can never fit — structurally impossible
   retries guarantee a timeout. And retry output is non-destructive:
   save as a new artifact, mark the old `superseded_by`.
5. **Preserve the first real verdict.** If the retry/re-check can't
   complete, keep the original result — do not overwrite a real
   `match=False` with a fabricated `"not run"` pass.
   (`test_video_qa_retry_frame_extract_fails_keeps_first_verdict`.)

## Per-call bounds in use (2026-11-16)

| Call | Bound | Where |
|---|---|---|
| DMR vision semantic QA | `asyncio.wait_for` 180 s | `media.py::_qa_frame` |
| ffmpeg frame extract | `subprocess timeout=60` | `media.py::_qa_frame` |
| CF Workers AI vision | `httpx.Timeout(60, connect=5)` | `media_ai.py` |
| FB sidecar `post_video` | HTTP 300 s | `facebook_sidecar.py` |
| FB sidecar `post_photo` | HTTP 300 s | `facebook_sidecar.py` |
| `generate_video_asset_task` | soft 2700 s / hard 3000 s | celery_app |

## Review checklist

When a task or publish op "dies after succeeding" or hangs silently:

- [ ] Does every external call have its own timeout below the soft limit?
- [ ] Is anything expensive produced but only persisted after optional
      steps? → move the commit earlier.
- [ ] Can an unavailable optional service stall the critical path?
      → `wait_for` + fail-soft verdict.
- [ ] Can a retry ever fit the remaining budget? → gate it on elapsed.
- [ ] Does the sidecar call have a client deadline? (Server-side browser
      loops do NOT stop on their own — the HTTP timeout is the only bound.)
