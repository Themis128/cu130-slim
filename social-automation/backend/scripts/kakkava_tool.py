#!/usr/bin/env python3
# ruff: noqa: E402
"""Kakkava doctrine corpus ops CLI — sync + inspect the team_<id>_doctrine
Chroma collections that feed SOURCE DOCTRINE prompt sections.

Run inside the social-api container:

    docker exec -w /app social-api python3 scripts/kakkava_tool.py <cmd>

Commands:
    sync [team_id]   ingest KAKKAVA_DIR into doctrine collections (all teams
                     when no id given) — idempotent, content-hash skips
    recall <query>   print the top doctrine hits for a query (proves recall)
    status           per-team chunk counts + last sweep audit_log row

Never prints file contents beyond short snippets.
"""
from __future__ import annotations

import asyncio
import os
import sys

for path in ("/app", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
    if path not in sys.path and os.path.isdir(os.path.join(path, "app")):
        sys.path.insert(0, path)


async def _teams() -> list:
    from sqlalchemy import select

    from app.db.session import async_session_maker
    from app.models.user import Team

    async with async_session_maker() as db:
        return (await db.execute(select(Team))).scalars().all()


async def cmd_sync(team_id: str | None) -> None:
    from app.services import kakkava

    print(f"kakkava_enabled={kakkava.kakkava_enabled()} dir={kakkava.settings.KAKKAVA_DIR}")
    teams = await _teams()
    if team_id:
        teams = [t for t in teams if str(t.id) == team_id]
        if not teams:
            print(f"team {team_id} not found")
            return
    for team in teams:
        stats = await kakkava.sync_corpus(team.id)
        print(f"{team.name} ({team.id}): {stats}")


async def cmd_recall(query: str, team_id: str | None) -> None:
    from app.services import kakkava

    teams = await _teams()
    team = teams[0] if not team_id else next((t for t in teams if str(t.id) == team_id), None)
    if not team:
        print("no team found")
        return
    hits = await kakkava.recall_doctrine(team.id, query, n_results=4)
    print(f"{len(hits)} hits for team {team.name}:")
    for h in hits:
        print(f"  [{h['distance']:.3f}] {h['source']} — {h['text'][:120]!r}")


async def cmd_status() -> None:
    import httpx
    from sqlalchemy import select

    from app.db.session import async_session_maker
    from app.models.user import AuditLog
    from app.services import kakkava
    from app.services.chroma_client import _collection_base_url, _get_collection_id

    print(f"dir={kakkava.settings.KAKKAVA_DIR} enabled={kakkava.kakkava_enabled()}")
    files = kakkava.iter_corpus_files(__import__("pathlib").Path(kakkava.settings.KAKKAVA_DIR))
    print(f"corpus files on disk: {len(files)}")
    teams = await _teams()
    async with httpx.AsyncClient(timeout=10) as client, async_session_maker() as db:
        for team in teams:
            col = kakkava._doctrine_collection(team.id)
            col_id = await _get_collection_id(client, col)
            count = "n/a"
            if col_id:
                resp = await client.post(
                    f"{_collection_base_url()}/{col_id}/get",
                    json={"include": [], "limit": 1},
                )
                if resp.status_code == 200:
                    count = len(resp.json().get("ids") or [])
                    # count endpoint for the real total
                    rc = await client.get(f"{_collection_base_url()}/{col_id}/count")
                    if rc.status_code == 200:
                        count = rc.json()
            last = (
                await db.execute(
                    select(AuditLog.created_at)
                    .where(AuditLog.action == "kakkava-sync", AuditLog.team_id == team.id)
                    .order_by(AuditLog.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            print(f"{team.name}: collection={col} docs={count} last_sync={last}")


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("sync")
    p.add_argument("team_id", nargs="?")
    p = sub.add_parser("recall")
    p.add_argument("query")
    p.add_argument("--team", dest="team_id")
    sub.add_parser("status")
    args = ap.parse_args()

    if args.cmd == "sync":
        asyncio.run(cmd_sync(getattr(args, "team_id", None)))
    elif args.cmd == "recall":
        asyncio.run(cmd_recall(args.query, args.team_id))
    else:
        asyncio.run(cmd_status())


if __name__ == "__main__":
    main()
