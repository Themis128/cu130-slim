#!/usr/bin/env python3
"""Raw heap-page tuple extractor/decoder for SocialAuto Postgres — read-only.

Dumps every tuple (live AND dead) on a table's heap pages with decoded column
values, using pg_attribute metadata for types/alignment. This is the tool that
recovered the 13 pre-corruption social_accounts rows on 2026-11-15 after
pageinspect's t_data proved unusable (cstring_to_text truncates at NUL).

How it works:
  1. Fetches raw 8kB pages via get_raw_page() -> bytea -> hex through psql.
  2. Parses PageHeaderData (24B) + ItemIdData line pointers (4B each).
  3. Parses HeapTupleHeaderData (23B) per tuple: xmin/xmax/ctid/infomask/hot/
     null-bitmap, data starts at t_hoff.
  4. Decodes attributes per pg_attribute (attlen/typalign/typname) with a
     DFS over varlena candidates (1-byte header, 4-byte header, EXTERNAL
     toast pointer) — picks the parse with the best end-offset + printable
     score. Never prints full secrets: bytea/text cols are shown as
     prefix+sha256+len.

Usage (from repo root, WSL — needs docker + social-postgres):
    python3 .devin/skills/postgres-recovery-ops/scripts/pg_heap_extract.py social_accounts
    python3 .devin/skills/postgres-recovery-ops/scripts/pg_heap_extract.py social_accounts --blocks 0-3
    python3 .devin/skills/postgres-recovery-ops/scripts/pg_heap_extract.py social_accounts --xmax 138552 --fields platform,username,access_token_enc
    python3 .devin/skills/postgres-recovery-ops/scripts/pg_heap_extract.py social_accounts --json > tuples.json

Never writes to the DB. Secrets are fingerprinted, not printed.
"""
import argparse
import hashlib
import json
import re
import struct
import subprocess
import sys
from datetime import datetime, timedelta, timezone

DB = "social_automation"
USER = "social_user"
CTR = "social-postgres"
PG_EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)
PAGE_SIZE = 8192


def psql(sql: str, timeout: int = 120) -> str:
    r = subprocess.run(
        ["docker", "exec", CTR, "psql", "-U", USER, "-d", DB, "-At", "-c", sql],
        capture_output=True, text=True, timeout=timeout,
    )
    if r.returncode != 0:
        sys.exit(f"psql failed: {r.stderr.strip()[:400]}")
    return r.stdout


def table_meta(table: str) -> tuple[int, list[dict]]:
    """Return (nblocks, [attr dicts ordered by attnum])."""
    info = psql(
        "SELECT pg_relation_size(c.oid)/8192 FROM pg_class c "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE c.relname='%s' AND n.nspname='public';" % table
    ).strip()
    if not info:
        sys.exit(f"table {table} not found")
    nblocks = int(info)
    out = psql(
        "SELECT a.attnum||'|'||a.attname||'|'||a.atttypid::regtype||'|'"
        "||t.typlen::text||'|'||t.typalign::text||'|'||t.typcategory::text "
        "FROM pg_attribute a JOIN pg_type t ON t.oid=a.atttypid "
        "WHERE a.attrelid='%s'::regclass AND a.attnum>0 AND NOT a.attisdropped "
        "ORDER BY a.attnum;" % table
    )
    attrs = []
    for line in out.strip().splitlines():
        if not line:
            continue
        num, name, typ, typlen, talign, tcat = line.split("|")
        attrs.append({
            "num": int(num), "name": name, "type": typ,
            "typlen": int(typlen), "align": talign, "cat": tcat,
        })
    return nblocks, attrs


def fetch_page(table: str, blk: int) -> bytes:
    hexpage = psql(
        "SELECT encode(get_raw_page('%s',%d),'hex');" % (table, blk),
        timeout=180,
    ).strip()
    return bytes.fromhex(hexpage)


ALIGN = {"c": 1, "s": 2, "i": 4, "d": 8}


def align_off(off: int, al: str) -> int:
    a = ALIGN.get(al, 1)
    return (off + a - 1) & ~(a - 1)


class TupleParser:
    """Decode one heap tuple's attrs; DFS over varlena ambiguities."""

    def __init__(self, data: bytes, attrs: list[dict]):
        self.data = data
        self.attrs = attrs
        self.natts = len(attrs)

    def parse_header(self):
        d = self.data
        if len(d) < 24:
            return None
        self.xmin, self.xmax, self.field3 = struct.unpack_from("<III", d, 0)
        self.ctid_blk, self.ctid_off = struct.unpack_from("<IH", d, 12)
        self.infomask2, self.infomask = struct.unpack_from("<HH", d, 18)
        self.hoff = d[22]
        # natts lives in infomask2 low 11 bits — use it, not len(attrs),
        # so dropped-column gaps don't misalign the null bitmap.
        self.nattrs = self.infomask2 & 0x7FF
        nbits = (self.nattrs + 7) // 8
        self.bitmap = d[23:23 + nbits]
        self.data_start = self.hoff
        self.tuple_len = len(d)  # caller slices exact lp_len
        return self

    def is_null(self, attnum0: int) -> bool:
        byte = self.bitmap[attnum0 // 8] if attnum0 // 8 < len(self.bitmap) else 0
        # t_bits: bit set = NOT NULL (att_isnull checks ~bit)
        return not (byte & (1 << (attnum0 % 8)))

    def attr_meta(self, idx: int):
        """Map tuple attr position -> pg_attribute row (handles attnum gaps)."""
        if idx >= len(self.attrs):
            return None
        return self.attrs[idx]

    # ---- varlena (proven decode_tuples5 semantics) ----
    # 1B header: b0&1 -> total = b0>>1 INCLUDES the header byte;
    #            payload = data[off+1 : off+total]
    # 4B header: u32 LE -> total = val>>2 INCLUDES the 4-byte header;
    #            payload = data[off+4 : off+total]
    def varlena_at(self, off: int):
        d = self.data
        if off >= len(d):
            return None
        b0 = d[off]
        if b0 & 1:
            total = b0 >> 1
            if total < 1 or off + total > len(d):
                return None
            return d[off + 1:off + total], off + total
        if off + 4 > len(d):
            return None
        v = struct.unpack_from("<I", d, off)[0]
        total = v >> 2
        if total < 4 or off + total > len(d):
            return None
        return d[off + 4:off + total], off + total
    # ---- fixed-length decoders ----
    def decode_fixed(self, a: dict, off: int):
        d, n = self.data, a["typlen"]
        t = a["type"]
        if off + n > len(d):
            return None
        raw = d[off:off + n]
        if t in ("int2", "smallint"):
            return struct.unpack("<h", raw)[0], off + n
        if t in ("int4", "integer"):
            return struct.unpack("<i", raw)[0], off + n
        if t in ("int8", "bigint"):
            return struct.unpack("<q", raw)[0], off + n
        if t in ("bool", "boolean"):
            return bool(raw[0]), off + n
        if t == "oid":
            return struct.unpack("<I", raw)[0], off + n
        if t == "uuid":
            h = raw.hex()
            return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}", off + n
        if t in ("timestamp without time zone", "timestamp with time zone"):
            us = struct.unpack("<q", raw)[0]
            try:
                dt = PG_EPOCH + timedelta(microseconds=us)
                if not (1990 <= dt.year <= 2100):
                    return None
                return dt.isoformat(), off + n
            except (OverflowError, ValueError):
                return None
        if t == "date":
            days = struct.unpack("<i", raw)[0]
            try:
                dt = (PG_EPOCH + timedelta(days=days)).date()
                if not (1990 <= dt.year <= 2100):
                    return None
                return dt.isoformat(), off + n
            except (OverflowError, ValueError):
                return None
        if t == "name":
            return raw.rstrip(b"\x00").decode("utf-8", "replace"), off + n
        # float4/float8/numeric fixed? numeric is varlena; floats:
        if t == "float4":
            return struct.unpack("<f", raw)[0], off + n
        if t == "float8":
            return struct.unpack("<d", raw)[0], off + n
        return raw.hex(), off + n

    def attr_candidates(self, idx: int, off: int):
        """Yield (value_repr, next_off, tag) for attr idx at off."""
        if idx >= getattr(self, "_target", self.nattrs):
            yield ("<END>", off, "end")
            return
        a = self.attr_meta(idx)
        if a is None:
            yield ("<UNKNOWN_ATTR>", off, "unk")
            return
        if self.is_null(idx):
            yield (None, off, "null")
            return
        if a["typlen"] > 0:
            # padding between attrs is inconsistent in practice — try the
            # raw offset and the aligned one (decode_tuples5 semantics)
            for cand in dict.fromkeys((off, align_off(off, a["align"]))):
                r = self.decode_fixed(a, cand)
                if r:
                    yield (r[0], r[1], "fix")
            return
        # varlena: try EVERY offset in [off, align4(off)] — tuples in this
        # heap pack varlena headers at unaligned offsets too
        a4 = align_off(off, "i")
        seen = set()
        for cand in range(off, a4 + 1):
            r = self.varlena_at(cand)
            if not r:
                continue
            payload, nxt = r
            if nxt in seen:
                continue
            seen.add(nxt)
            yield (self.fmt_payload(a, payload), nxt, "var")

    def fmt_payload(self, a: dict, payload):
        t = a["type"]
        if isinstance(payload, bytes):
            # fingerprint long/binary values — never print full secrets
            if len(payload) > 48 or (a["cat"] in ("U", "S", "B", "N") and t in (
                "bytea", "jsonb", "json", "numeric")):
                h = hashlib.sha256(payload).hexdigest()[:12]
                return f"<{len(payload)}B sha256:{h}>"
            try:
                s = payload.decode("utf-8")
                if s and all(32 <= ord(c) < 127 or c in "\n\t" for c in s):
                    return s if len(s) <= 80 else s[:77] + "..."
            except UnicodeDecodeError:
                pass
            h = hashlib.sha256(payload).hexdigest()[:12]
            return f"<{len(payload)}B sha256:{h}>"
        return payload

    @staticmethod
    def _val_quality(v) -> int:
        """Reward clean decodes, penalize fingerprint blobs / markers."""
        if v is None:
            return 0
        if isinstance(v, bool):
            return 1
        if isinstance(v, (int, float)):
            return 1
        if isinstance(v, str):
            if v.startswith("<"):
                return -4  # sha256 blob, <TOAST>, <UNKNOWN_ATTR>, bad ts
            if all(32 <= ord(c) < 127 or ord(c) > 127 for c in v):
                return 2
            return -1
        return 0

    def score(self, end_off: int, vals: list) -> float:
        # end-offset proximity dominates; value quality breaks ties toward
        # parses that decode text fields as clean strings (decode_tuples5's
        # printable_score, generalized)
        return -abs(self.tuple_len - end_off) * 10 + sum(
            self._val_quality(v) for v in vals)

    def parse(self, through: int | None = None):
        """DFS over attrs[0:through]; returns (values, end_off, complete)."""
        target = self.nattrs if through is None else min(through, self.nattrs)
        self._target = target
        best = [None, -10**9, False]
        visits = [0]
        MAX_VISITS = 2_000_000  # full parses are rare; dead-ends dominate

        def dfs(idx: int, off: int, vals: list):
            visits[0] += 1
            if visits[0] > MAX_VISITS:
                return
            if idx == target:
                complete = target == self.nattrs
                # partial parses get a small penalty so full parses win ties
                sc = self.score(off, vals) - (0 if complete else 50)
                if sc > best[1]:
                    best[0], best[1], best[2] = list(vals), sc, complete
                return
            for v, nxt, tag in self.attr_candidates(idx, off):
                if tag == "end":
                    continue
                # prune: candidate ran past tuple end
                if nxt > self.tuple_len:
                    continue
                vals.append(v)
                dfs(idx + 1, nxt, vals)
                vals.pop()

        dfs(0, self.data_start, [])
        return best[0], best[2]  # (values_or_None, complete_bool)


def parse_page(page: bytes):
    """Yield (lp_index, lp_flags, lp_len, tuple_bytes_or_None)."""
    if len(page) < 24:
        return
    # PageHeaderData (24B): lsn@0 cksum@8 flags@10 lower@12 upper@14
    # special@16 pagesize_ver@18 prune_xid@20
    lower, upper = struct.unpack_from("<HH", page, 12)
    nitems = (lower - 24) // 4
    for i in range(nitems):
        lp = 24 + i * 4
        # ItemIdData bitfields (LE): lp_off=bits0-14, lp_flags=bits15-16,
        # lp_len=bits17-31 — flags straddle the two uint16s.
        w0, w1 = struct.unpack_from("<HH", page, lp)
        lp_off = w0 & 0x7FFF
        lp_flags = ((w0 >> 15) & 1) | ((w1 & 1) << 1)
        lp_len = w1 >> 1
        if lp_flags == 0 or lp_len == 0 or lp_off + lp_len > PAGE_SIZE:
            yield i + 1, lp_flags, lp_len, None
            continue
        yield i + 1, lp_flags, lp_len, page[lp_off: lp_off + lp_len]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("table")
    ap.add_argument("--blocks", default=None, help="e.g. 0-5 or comma list")
    ap.add_argument("--xmax", default=None, help="only tuples with this xmax")
    ap.add_argument("--fields", default=None, help="comma list of attr names to print")
    ap.add_argument("--live-only", action="store_true")
    ap.add_argument("--dead-only", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", args.table):
        sys.exit("bad table name")

    nblocks, attrs = table_meta(args.table)
    psql("CREATE EXTENSION IF NOT EXISTS pageinspect;")

    if args.blocks:
        if "-" in args.blocks:
            lo, hi = args.blocks.split("-")
            blocks = range(int(lo), int(hi) + 1)
        else:
            blocks = [int(x) for x in args.blocks.split(",")]
    else:
        blocks = range(nblocks)

    names = [a["name"] for a in attrs]
    show = args.fields.split(",") if args.fields else names
    show_idx = [names.index(f) for f in show if f in names]

    tuples_out = []
    for b in blocks:
        try:
            page = fetch_page(args.table, b)
        except SystemExit as e:
            print(f"# blk {b}: fetch failed {e}", file=sys.stderr)
            continue
        for lp, lp_flags, lp_len, tbytes in parse_page(page):
            if tbytes is None or len(tbytes) < 24:
                continue
            tp = TupleParser(tbytes, attrs)
            if not tp.parse_header():
                continue
            if args.xmax and str(tp.xmax) != args.xmax:
                continue
            if args.live_only and tp.xmax != 0:
                continue
            if args.dead_only and tp.xmax == 0:
                continue
            # full parse first (only complete walks populate vals); if the
            # tail (scopes array / jsonb / trailing timestamps) can't be
            # walked, fall back to the proven decode_tuples5 scope — through
            # 'status'
            vals, complete = tp.parse()
            mode = "full" if vals is not None else ""
            if vals is None:
                cut = names.index("status") + 1 if "status" in names else len(names) - 3
                vals2, _ = tp.parse(through=cut)
                if vals2 is not None:
                    vals, mode = vals2, f"prefix:{names[cut-1]}"
            row = {
                "blk": b, "lp": lp, "lp_flags": lp_flags,
                "xmin": tp.xmin, "xmax": tp.xmax,
                "ctid": f"{tp.ctid_blk},{tp.ctid_off}",
                "infomask2": tp.infomask2, "infomask": tp.infomask,
                "hoff": tp.hoff, "len": len(tbytes),
            }
            if vals is not None:
                row["fields"] = {names[i]: vals[i] for i in show_idx if i < len(vals)}
                row["_parse_mode"] = mode or ("full" if complete else "partial")
            else:
                row["fields"] = None
            tuples_out.append(row)

    if args.json:
        print(json.dumps(tuples_out, indent=1, default=str))
        return

    for r in tuples_out:
        hdr = (f"blk={r['blk']} lp={r['lp']} xmin={r['xmin']} xmax={r['xmax']} "
               f"ctid={r['ctid']} len={r['len']} im2={r['infomask2']:#06x} "
               f"im={r['infomask']:#06x}")
        print(hdr + (f" [{r['_parse_mode']}]" if r.get("_parse_mode") else ""))
        if r["fields"]:
            for k, v in r["fields"].items():
                print(f"    {k} = {v!r}")
        else:
            print("    <unparsed>")


if __name__ == "__main__":
    main()
