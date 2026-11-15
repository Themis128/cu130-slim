#!/usr/bin/env python3
"""Dead-tuple triage for SocialAuto Postgres — read-only.

Reports, for a given table:
  1. live/dead tuple counts + autovacuum state (is recovery still possible?)
  2. heap_page_items dump grouped by line pointer, with t_xmax / t_ctid,
     so the corrupting transaction's xid (the one appearing ~n_live_tup
     times among dead tuples) can be identified.

Usage (from repo root, WSL):
    python3 .devin/skills/postgres-recovery-ops/scripts/pg_deadtuples.py social_accounts
    python3 .devin/skills/postgres-recovery-ops/scripts/pg_deadtuples.py --stats
    python3 .devin/skills/postgres-recovery-ops/scripts/pg_deadtuples.py social_accounts --blocks 0-5

Never writes. Requires docker + social-postgres container.
"""
import argparse
import json
import re
import subprocess
import sys

DB = "social_automation"
USER = "social_user"
CTR = "social-postgres"


def psql(sql: str) -> str:
    r = subprocess.run(
        ["docker", "exec", CTR, "psql", "-U", USER, "-d", DB, "-At", "-c", sql],
        capture_output=True, text=True, timeout=60,
    )
    if r.returncode != 0:
        sys.exit(f"psql failed: {r.stderr.strip()}")
    return r.stdout


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("table", nargs="?", help="table to inspect")
    ap.add_argument("--stats", action="store_true", help="live/dead stats for all user tables")
    ap.add_argument("--blocks", default=None, help="block range e.g. 0-5 (default: all blocks)")
    args = ap.parse_args()

    if args.stats or not args.table:
        print(psql(
            "SELECT relname || ' live=' || n_live_tup || ' dead=' || n_dead_tup "
            "|| ' last_autovac=' || coalesce(last_autovacuum::text,'never') "
            "FROM pg_stat_user_tables ORDER BY n_dead_tup DESC;"
        ))
        if not args.table:
            return

    t = args.table
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", t):
        sys.exit("bad table name")

    # relfilenode + block count
    info = psql(
        f"SELECT c.relfilenode || '|' || pg_relation_size(c.oid)/8192 "
        f"FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        f"WHERE c.relname='{t}' AND n.nspname='public';"
    ).strip()
    if not info:
        sys.exit(f"table {t} not found")
    filenode, nblocks = info.split("|")
    print(f"# {t}: relfilenode={filenode} blocks={nblocks}")

    psql(f"CREATE EXTENSION IF NOT EXISTS pageinspect;")

    if args.blocks:
        lo, hi = args.blocks.split("-")
        blocks = range(int(lo), int(hi) + 1)
    else:
        blocks = range(int(nblocks))

    # heap_page_items columns: blkno offnum lp lp_flags lp_len t_xmin t_xmax
    # t_field3 t_ctid t_infomask2 t_infomask t_hoff t_bits t_oid t_data
    rows = []
    for b in blocks:
        out = psql(
            f"SELECT blkno||'|'||offnum||'|'||lp_flags||'|'||t_xmin||'|'||t_xmax"
            f"||'|'||t_ctid||'|'||t_hoff||'|'||coalesce(t_bits::text,'')"
            f" FROM heap_page_items(get_raw_page('{t}', {b}));"
        )
        for line in out.strip().splitlines():
            if line:
                rows.append((b, line))

    # summarize xmax frequency among dead tuples (lp_flags bit0 = LP_UNUSED? dead = t_xmax<>0)
    from collections import Counter
    xmax_counter = Counter()
    dead_rows = []
    for b, line in rows:
        parts = line.split("|")
        if len(parts) >= 5:
            xmax = parts[4]
            if xmax and xmax != "0":
                xmax_counter[xmax] += 1
                dead_rows.append((b, line))

    print(f"# {len(rows)} line pointers, {len(dead_rows)} dead tuples")
    print("# top t_xmax among dead tuples (corrupting xid ~ n_live_tup occurrences):")
    for xid, cnt in xmax_counter.most_common(10):
        print(f"  xmax={xid}  count={cnt}")

    print("\n# dead tuples (blk|off|flags|xmin|xmax|ctid|hoff|bits):")
    for b, line in dead_rows:
        print(f"  blk={b} {line}")


if __name__ == "__main__":
    main()
