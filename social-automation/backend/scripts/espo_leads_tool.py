"""EspoCRM lead queries — owner-gate review support (partner shortlist etc.).

Usage:
  python scripts/espo_leads_tool.py by-source partner
  python scripts/espo_leads_tool.py by-status New
  python scripts/espo_leads_tool.py search <text>   # description contains
"""
from __future__ import annotations

import asyncio
import sys

import httpx

from app.core.config import get_settings


async def _query(where_attr: str, where_type: str, value: str, limit: int = 40):
    s = get_settings()
    async with httpx.AsyncClient(
        base_url=s.ESPOCRM_BASE_URL,
        headers={"X-Api-Key": s.ESPOCRM_API_KEY},
        timeout=20,
    ) as c:
        r = await c.get(
            "/api/v1/Lead",
            params={
                "maxSize": limit,
                "where[0][type]": where_type,
                "where[0][attribute]": where_attr,
                "where[0][value]": value,
            },
        )
        r.raise_for_status()
        return r.json().get("list", [])


def _print(rows):
    for x in rows:
        desc = (x.get("description") or "").replace("\n", " ")[:90]
        print(f"{x.get('name')} | {x.get('status')} | {x.get('industry') or '-'} | {desc}")


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    arg = sys.argv[2] if len(sys.argv) > 2 else ""
    if cmd == "by-source":
        rows = asyncio.run(_query("source", "equals", arg))
    elif cmd == "by-status":
        rows = asyncio.run(_query("status", "equals", arg))
    elif cmd == "search":
        rows = asyncio.run(_query("description", "contains", arg))
    else:
        print(__doc__)
        return 1
    _print(rows)
    print(f"-- {len(rows)} leads")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
