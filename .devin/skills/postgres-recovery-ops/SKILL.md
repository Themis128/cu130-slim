# postgres-recovery-ops

Recover SocialAuto Postgres (`social-postgres` container, db `social_automation`,
user `social_user` — superuser) from accidental mass UPDATE/DELETE corruption.

## Incident this exists for (2026-11-15)

A hand-written `UPDATE ... FROM social_accounts t` with a malformed WHERE
(`s.id = '<partial-uuid>'::uuid IS NOT TRUE`) matched **all 13 rows** and copied
one arbitrary row's token columns across every account. All encrypted tokens +
`status='active'` were destroyed in one transaction.

Lessons baked into the tooling:

- **Never run UPDATE/DELETE without first running the same WHERE as a SELECT**
  and eyeballing the row count.
- `UPDATE n` where `n` ≫ expected = stop and assess before anything else.
- Full-row `FROM` self-joins need an explicit join condition; a missing one is
  a cartesian product and the "last row wins" is nondeterministic.

## Triage order (do these in order, cheap → deep)

1. **Confirm scope** — `pg_stat_user_tables.n_dead_tup` vs `n_live_tup` tells
   you how many old row versions still sit in the heap:

   ```sql
   SELECT relname, n_live_tup, n_dead_tup, last_autovacuum
   FROM pg_stat_user_tables WHERE relname='<table>';
   ```

   `n_dead_tup ≥ n_live_tup` and `last_autovacuum` NULL = **pre-images likely
   still on disk**. Act before autovacuum runs (it will, soon — dead-tuple
   threshold on a small hot table is minutes).

2. **Check for a fresher logical backup** — `~/docker-restore/` on WSL:
   - `pgdumpall-*.sql.gz` files are **mangled** (PS `>` redirect corrupted
     the gzip stream — files decode as UTF-16/CP437 garbage, not gzip).
     Treat as forensic-only; do not count on them for restore.
   - `vol-cu130-slim_social_postgres_data_v17-*.tar.gz` = whole-data-dir
     volume snapshots; freshest usable was `20261112-0812` (~2d stale).
     The tar can be truncated mid-member — parse members manually and
     recover `base/<dboid>/<relfilenode>` heap files anyway.
   - If a fresher volume tar exists, restoring the whole cluster is cleaner
     than tuple surgery.

3. **Re-derive tokens from authoritative sources** (often faster than tuple
   surgery — see "Token re-provisioning" below). Meta rows were already
   revoked by the FB password change, so their "loss" costs nothing.

4. **Dead-tuple extraction via pageinspect** (last resort, see below).

## Dead-tuple extraction (pageinspect)

`pageinspect` is available; `social_user` is superuser.

```sql
CREATE EXTENSION IF NOT EXISTS pageinspect;
SELECT relfilenode FROM pg_class WHERE relname='social_accounts';
SELECT * FROM heap_page_items(get_raw_page('social_accounts', <blk>));
```

`heap_page_items` returns every line pointer incl. dead tuples with `t_xmax`,
`t_ctid`, `t_hoff`, `t_bits`. The tuple whose `t_xmax` = **your bad
transaction's xid** is the exact pre-image. One UPDATE = one xid → all 13 dead
tuples share it; identify it as the xid appearing ~n_live_tup times.

**Caveat**: `t_data` is `text` built with `cstring_to_text` — it truncates at
the first NUL byte, and heap tuple headers are full of them. For rows with
binary columns (bytea tokens) t_data alone is usually NOT enough.

### FPI/WAL fallback (deterministic, heavier)

`wal_level=replica`, `archive_mode=off`, but `pg_wal/` retains segments.
`full_page_writes` is on by default → the first WAL record touching a page
after each checkpoint carries a **full pre-change page image**. If the corrupting
UPDATE was the first touch of `social_accounts`' pages post-checkpoint, the old
tuples are recoverable bit-for-bit:

1. Find the segment covering the corruption timestamp:
   `SELECT pg_walfile_name(pg_current_wal_lsn())` for now; walk backwards by
   time via file mtimes in `pg_wal/`.
2. `pg_wal_dump`/`pg_waldump` the segment, filter records for the table's
   `relfilenode` + `RelFileNode`/`BlkRef` of type FPI.
3. Reconstruct the page image, parse `HeapTupleHeaderData` + attrs per
   `pg_attribute` (attlen/attalign) — bytea fields come out as length-prefixed
   binary. Doable in ~100 lines of Python; only reach for it when re-derivation
   fails.

## Token re-provisioning map (social_accounts)

Encrypted cols: `access_token_enc`, `refresh_token_enc` (bytea, app-level
`encrypt_token()` in `app/core/security.py`, key `TOKEN_ENCRYPTION_KEY` in
`.env`). To write a token back:

```bash
docker compose exec -T social-api python - <<'PY'
from app.core.security import encrypt_token
print(encrypt_token("TOKEN").hex())   # -> bytea for UPDATE ... decode(hex,'hex')
PY
```

Authoritative sources per platform:

| platform | source |
|---|---|
| twitter/X | `.env` `TWITTER_ACCESS_TOKEN`(+`_OAUTH`), `X_ACCESS_TOKEN` |
| bluesky | `.env` `BSKY_MAIN_PASSWORD` (app password) — check `bluesky_api.py` for exact stored form |
| telegram | `.env` `TG_BOT_TOKEN` / `TELEGRAM_BOT_TOKEN` |
| whatsapp | `.env` `WHATSAPP_ACCESS_TOKEN` (dead anyway — Meta pw change) |
| facebook/instagram/threads | dead — needs OAuth re-auth regardless (`meta-oauth-ops` skill) |
| tiktok | re-run `scripts/tiktok_reauth.py` (refresh lane works unattended) |
| linkedin | refresh_token grant heals without consent — `linkedin-cm-ops` skill |

Also restore `status` — the corrupting UPDATE force-set `status='active'` on
revoked rows. Known-good states (2026-11-15): instagram/threads `revoked`,
tiktok `cloudless.gr` row `revoked`, others `active`.

## Resolution runbook (proven 2026-11-15, social_accounts ×13)

The incident was fully repaired with this sequence — reuse it:

1. **Snapshot first**: `pg_preupdate_snapshot.sh social_accounts` →
   `social_accounts_predrop_20261115` (kept in-DB as the rollback source).
2. **Identify corrupting xid**: `pg_deadtuples.py social_accounts` → the
   `t_xmax` appearing ~n_live_tup times (was 138552).
3. **Extract pre-images**: `pg_heap_extract.py social_accounts --xmax 138552
   --json` — decoded all 13 dead tuples incl. bytea tokens. Rows whose
   pre-image was already vacuumed/overwritten came from the Nov-12 volume
   tar's heap file instead (parse `base/<dboid>/<relfilenode>` offline with
   the same decoder — the tool accepts a dumped page file via `--page-file`
   in spirit; port `TupleParser`/`parse_page` directly).
4. **Generate per-row UPDATEs**: one `UPDATE ... WHERE id='<uuid>'` per row,
   `access_token_enc=decode('<hex>','hex')` — values taken from the decoded
   dead tuples VERBATIM (they are already app-encrypted Fernet blobs; do NOT
   re-encrypt). Wrap all in one transaction; expect exactly `UPDATE 1` × n.
5. **Verify**: distinct token lengths/hashes per row + decrypt each inside
   `social-api` via `app.core.security.decrypt_token` — check platform
   prefixes (`act.`/`rft.` tiktok, `EAAB`/`IGAA`/`THAA` Meta, `AQX`/`AQW`
   LinkedIn, bot token telegram, `TH!` bluesky) and NEVER print full tokens.
6. **`VACUUM <table>`** once extraction is done (dead tuples → reusable
   space; `pg_stat_user_tables.n_dead_tup` should drop to ~0).

### Heap tuple format cheatsheet (what the decoder implements)

- Page: 24B `PageHeaderData` (pd_lower@16, pd_upper@18, LE); line pointers
  from offset 24, 4B each: `lp_off=u16@0`, `lp_flags=(u16@2>>14)&3`,
  `lp_len=u16@2&0x3FFF`. Tuple bytes at `page[lp_off:lp_off+lp_len]`.
- Tuple header 23B: `xmin u32@0, xmax u32@4, field3 u32@8, ctid (blk u32 +
  off u16)@12, infomask2 u16@18, infomask u16@20, hoff u8@22`. Data starts
  at `hoff`; null bitmap at offset 23, `ceil(natts/8)` bytes, **bit SET =
  NOT NULL** (inverted vs intuition).
- Attributes: order/alignment/length from `pg_attribute`+`pg_type`
  (`typlen`, `typalign` c/s/i/d → 1/2/4/8). Align each attr offset
  relative to data start before reading.
- Varlena (typlen=-1): **1-byte header** when `b0&1` → `total = b0>>1`
  INCLUDES the header byte; payload = `data[off+1 : off+total]`.
  **4-byte header** → u32 LE, `total = val>>2` includes the 4-byte header;
  payload = `data[off+4 : off+total]`. EXTERNAL toast pointers are 18-byte
  varlena blobs (fingerprinted, not inlined).
- **Padding is inconsistent**: varlena attrs must be tried at EVERY offset
  in `[off, align4(off)]`, timestamps at `[off, align8(off)]` — tuples in
  this heap pack headers unaligned. Fixed attrs try `{off, aligned}`.
- **Trailing attrs may not decode**: `meta_data` jsonb + `scopes` array +
  trailing timestamps sometimes can't be walked end-to-end. The tool falls
  back to parsing through `status` (`_parse_mode=prefix:status`) — same
  scope the proven decoder used; all credential columns are covered.
- `t_hoff` already includes header+bitmap padding — do NOT add bitmap size
  on top of it.

## Tooling

`scripts/pg_deadtuples.py` — dead/live counts + dominant dead xid:

```bash
python3 .devin/skills/postgres-recovery-ops/scripts/pg_deadtuples.py social_accounts
python3 .devin/skills/postgres-recovery-ops/scripts/pg_deadtuples.py --stats
```

`scripts/pg_heap_extract.py` — full tuple decoder (live+dead, all columns):

```bash
python3 .devin/skills/postgres-recovery-ops/scripts/pg_heap_extract.py social_accounts --xmax 138552 --json
python3 .devin/skills/postgres-recovery-ops/scripts/pg_heap_extract.py social_accounts --live-only --fields platform,username,status
```

`scripts/pg_preupdate_snapshot.sh` — `CREATE TABLE <t>_predrop_<date> AS
SELECT` before ANY manual UPDATE/DELETE:

```bash
bash .devin/skills/postgres-recovery-ops/scripts/pg_preupdate_snapshot.sh social_accounts
```
