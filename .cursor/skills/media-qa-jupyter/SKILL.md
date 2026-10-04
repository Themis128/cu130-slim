---
name: media-qa-jupyter
description: >-
  Validate post media before publishing using the media-notebooks QA flow —
  deterministic platform-rule checks plus a semantic DMR vision-model
  (qwen3-vl) caption match. Use before publishing posts with generated
  media, when a digest flags broken/wrong media, or after changing the
  generation pipeline (e.g. #301 SD 1.5 settings fix).
allowed-tools:
  - read
  - exec
  - grep
  - glob
triggers:
  - user
  - model
---

# Media QA via Jupyter + DMR

## When to use

- Before publishing posts that carry AI-generated media (the 2026-10-04
  digest shipped three distorted/unrelated images to production).
- After a digest flags "wrong media" on any platform target.
- After changing the image-generation pipeline (provider, settings, prompt
  templates) — run the QA notebook over sample outputs first.

## What runs where

| Layer | Where | Checks |
|---|---|---|
| Deterministic pre-flight | `publishing.py` (`validate_media_for_platform`) — runs in the publish queue | formats, count, dimensions, ratio, byte size per platform |
| Semantic QA (this skill) | `social-jupyter` container, `notebooks/media_validation.ipynb` | DMR `qwen3-vl` captions the image; compare against post intent |

The deterministic layer blocks media that *cannot* publish; the semantic
layer catches media that *would publish but is wrong* (e.g. the 2026-10-04
crowd image generated for a Raspberry Pi story).

## Procedure

```bash
# 1. Interactive QA in the notebook UI (localhost:8888, token from .env):
docker compose up -d social-jupyter
#    open work/media_validation.ipynb → set media_paths + platform → Run All

# 2. Headless (papermill) — parameters: media_paths (JSON list), platform, post_id:
docker exec social-jupyter papermill work/media_validation.ipynb \
  work/media_validation-output.ipynb \
  -p media_paths '["2026/10/04/f66a441d654f4e04.jpg"]' -p platform instagram

# 3. Report: work/media_validation_report.json (verdict per asset + DMR captions)
```

## DMR requirements

- Docker Model Runner serves OpenAI-compatible chat at
  `http://host.docker.internal:12435/engines/v1` (host: `localhost:12434`).
- The vision model must be pulled once: `docker model pull qwen3-vl`
  (already present on this host as of 2026-10-04).
- No DMR/no model pulled → the deterministic verdict still stands; the
  semantic cell degrades to a warning, the report notes it.

## Interpreting results

- `verdict: fail` + dimension/ratio reason → regenerate at the platform's
  creation size (see the table in `notebooks/media_validation.ipynb`).
- `dmr_description` that contradicts the post intent (e.g. "a dense crowd
  of people" for a Raspberry Pi post) → the asset is semantically wrong:
  regenerate with a corrected prompt before publishing.
- Records are kept next to the notebook (`media_validation_report.json`) —
  attach them to the digest triage when re-publishing.
