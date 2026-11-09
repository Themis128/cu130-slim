# kakkava-brain-sync

Adopt the contents of `C:\Users\tbaltzakis\OneDrive\Kakkava Sofia` into the
SocialAuto **Company Brain** — Chroma `team_*_brain` memories (semantic
recall) plus a Nextcloud "Kakkava Sofia — Source Library" index note.

First ingest ran 2026-11-09: 82 text documents → 362 `kakkava-sofia-*`
memories. Recall verified (e.g. "5-hour content method" → DAY 20 PDF).

## How it works

- `scripts/kakkava_brain_sync.sh` (host, cron-safe) — builds a
  path+size+mtime manifest of the source folder; **only re-ingests when the
  manifest changed**. Tars the folder, `docker cp`s it plus the ingest
  script into `social-api`, extracts to `/tmp/kakkava-sofia`, runs the
  ingest, cleans up.
- `scripts/kakkava_brain_ingest.py` (runs **inside** `social-api` — needs
  `app.services.company_brain` + `pymupdf`) — extracts text from
  pdf/md/html/txt (≤30k chars/file), chunks at ~2800 chars, upserts each
  chunk via `company_brain.remember()` with path-derived doc_ids
  (`kakkava-sofia-<slug>-<hash>-<i>`) so re-runs are idempotent upserts.
  Writes the index memory + Nextcloud note at the end.

## Schedule

`/etc/cron.d/kakkava-brain-sync` — daily 06:30. Manifest-gated, so a no-op
day costs one `find`+`tar`-skip. Log: `~/logs/kakkava-brain-sync.log`.

## Manual run / env overrides

```bash
KAKKAVA_SRC="/mnt/c/Users/tbaltzakis/OneDrive/Kakkava Sofia" \
KAKKAVA_REPO=/home/tbaltzakis/cu130-slim \
bash .devin/skills/kakkava-brain-sync/scripts/kakkava_brain_sync.sh
```

- `KAKKAVA_SRC` — source folder (default the OneDrive path).
- `KAKKAVA_REPO` — repo root the ingest script is read from.
- `KAKKAVA_STATE` — manifest/state dir (default `~/.local/state/kakkava-brain-sync`).
- `SOCIALAUTO_TEAM_ID` — optional; otherwise resolved via `social-postgres`
  (`teams.name ILIKE '%baltzakis%'`).

Delete `$KAKKAVA_STATE/manifest.txt` to force a full re-ingest.

## Notes / limits

- Non-text files (images, .zip, .skill.zip) are skipped — they appear in
  the manifest but produce no memories.
- `MAX_FILE_CHARS=30000` caps huge PDFs; `CHUNK=2800` bounds embedding size.
- A few chunks may fail embedding transiently (`remember failed` on
  stderr) — re-running the sync retries them (upsert is idempotent).
- Requires `social-api` + `social-postgres` + `chroma` up; the script
  exits 0 with a skip message when the stack is down (cron-safe).
