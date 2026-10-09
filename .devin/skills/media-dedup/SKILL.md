---
name: media-dedup
description: Perceptual-hash media deduplication enforcing the no-duplicated-posts rule — publish-path skip when a draft's image set already shipped to the same account, plus a backfill/audit script for media_assets phashes. Use for duplicate-media issues, repackaged-content detection, or hashing questions.
---

# Media Dedup (perceptual hash)

## What it does

`app/services/media_dedup.py` gives each image `MediaAsset` a 64-bit
perceptual hash stored in `meta_data["phash"]` (JSONB — no migration).

The publishing worker calls `media_duplicate_reason()` after the text dedup
check (`worker/tasks/publishing.py`): if **every** image on a draft
near-matches the image set of a post already published to the same account
within 30 days, the target is skipped with an explanatory `error_message`.
Partial overlap (shared logo, reused thumbnail) does NOT count — set-level
match required. Text dedup stays the first check; this catches
repackaged-same-media duplicates (carousel → reel, etc.).

## Why imagehash and not Meta PDQ

Meta's `pdqhash`/`vpdq` (facebook/ThreatExchange) require a C++ toolchain —
the slim backend image has no `g++`, and `threatexchange` pulls the same
native deps. `imagehash.phash` (in pyproject deps) is the pure-Python
equivalent for general-purpose dedup. If a compiled image ever ships, swap
`compute_phash` internals — the meta_data storage contract stays.

## Tuning

- `DUP_DISTANCE = 8` — Hamming distance (of 64 bits) for "same image".
  Raise to ~10 to be more aggressive (catches recompressed/recolored),
  lower to ~5 if false positives appear.
- `DUP_WINDOW_DAYS = 30` — how far back published-post media is compared.
- Videos are skipped (no frame hashing — vpdq is native-only).

## Audit / backfill

Backfill phashes for existing assets and report near-duplicate pairs:

```bash
docker exec social-api python3 /app/scripts/dedup_scan.py            # dry run
docker exec social-api python3 /app/scripts/dedup_scan.py --write    # persist hashes
```

## Failure mode

`media_duplicate_reason` is called under try/except in the worker — a dedup
failure logs and publishes rather than blocking the queue.
