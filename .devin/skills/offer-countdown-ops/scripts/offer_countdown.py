#!/usr/bin/env python3
"""offer_countdown.py — operator CLI for the {countdown} publish token.

  offer_countdown.py preview "Only {countdown:2026-11-27T23:59:00+02:00} left"
      Render the token now, exactly as the publish lane will.
  offer_countdown.py set <post_id> <ISO-8601 deadline>
      Store meta_data.offer_ends_at so bare {countdown} resolves.
  offer_countdown.py show <post_id>
      Print the post's content_text + meta_data.

Runs on the WSL host; DB access via `docker exec social-postgres`.
"""
import json
import re
import subprocess
import sys
from datetime import datetime, timezone

TOKEN = re.compile(r"\{countdown(?::([^}\s]+))?\}")


def expand(text: str, meta: dict | None = None, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)

    def _render(m: re.Match) -> str:
        iso = m.group(1) or str((meta or {}).get("offer_ends_at") or "")
        if not iso:
            return m.group(0)
        try:
            dl = datetime.fromisoformat(iso.strip())
        except ValueError:
            return m.group(0)
        if dl.tzinfo is None:
            dl = dl.replace(tzinfo=timezone.utc)
        d = dl - now
        if d.total_seconds() <= 0:
            return "expired"
        return f"{d.days} days, {d.seconds // 3600} hours, {(d.seconds % 3600) // 60} minutes"

    return TOKEN.sub(_render, text)


def _psql(sql: str, tuples_only: bool = True) -> str:
    flag = "-tA" if tuples_only else ""
    r = subprocess.run(
        ["docker", "exec", "social-postgres", "psql",
         "-U", "social_user", "-d", "social_automation", flag, "-c", sql],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"psql failed: {r.stderr.strip()}")
    return r.stdout.strip()


def cmd_preview(text: str) -> None:
    print(expand(text))


def cmd_show(post_id: str) -> None:
    row = _psql(
        f"SELECT COALESCE(content_text, '<no caption>') || E'\\n---META---\\n' || "
        f"COALESCE(meta_data::text, '{{}}') FROM posts WHERE id='{post_id}'")
    print(row or f"post {post_id} not found")


def cmd_set(post_id: str, deadline: str) -> None:
    try:
        dl = datetime.fromisoformat(deadline)
    except ValueError:
        raise SystemExit(f"bad ISO-8601 deadline: {deadline}")
    if dl.tzinfo is None:
        raise SystemExit("deadline must carry an offset (e.g. +02:00 EET)")
    out = _psql(
        f"UPDATE posts SET meta_data = COALESCE(meta_data,'{{}}'::jsonb) || "
        f"'{json.dumps({'offer_ends_at': deadline})}'::jsonb "
        f"WHERE id='{post_id}' RETURNING id")
    if not out:
        raise SystemExit(f"post {post_id} not found")
    print(f"offer_ends_at={deadline} set on {post_id}")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cmd = sys.argv[1]
    if cmd == "preview" and len(sys.argv) == 3:
        cmd_preview(sys.argv[2])
    elif cmd == "show" and len(sys.argv) == 3:
        cmd_show(sys.argv[2])
    elif cmd == "set" and len(sys.argv) == 4:
        cmd_set(sys.argv[2], sys.argv[3])
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
