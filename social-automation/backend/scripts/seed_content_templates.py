#!/usr/bin/env python3
"""Seed content_prompt_templates from deploy/seeds/content_prompt_templates.json.

Idempotent deploy step — run inside the social-api container (or any env with
DB access) after alembic migrations:

    python scripts/seed_content_templates.py                 # first team found
    python scripts/seed_content_templates.py --team-id <uuid>

Behaviour:
- Templates are matched by (team_id, name). Existing rows are UPDATED to the
  seed content; missing rows are INSERTED with the production UUID so a
  reseed of prod is a no-op update rather than a duplicate.
- pillar_name is resolved to a team-scoped pillars row (created if missing) —
  pillar ids differ between installs, so the seed never hardcodes them.
- Prints a one-line diff summary per template (created / updated / unchanged).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

# Self-locating: repo layout .../backend/scripts/x.py and container layout
# /app/scripts/x.py both resolve to the dir that owns the `app` package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.db.session import async_session_maker
from app.models.content import Pillar
from app.models.workflow import ContentPromptTemplate


def _default_seed_file() -> Path:
    # In the container the compose volume mounts deploy/seeds at
    # /app/deploy/seeds; locally the repo root holds deploy/.
    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "deploy" / "seeds" / "content_prompt_templates.json"
        if cand.exists():
            return cand
    return Path("deploy/seeds/content_prompt_templates.json")


SEED_FILE = _default_seed_file()

FIELDS = (
    "pillar_id",
    "platform",
    "tone",
    "system_prompt",
    "user_prompt_template",
    "variables",
    "is_default",
)


async def _resolve_team_id(db, wanted: str | None) -> uuid.UUID:
    if wanted:
        return uuid.UUID(wanted)
    row = (await db.execute(select(Pillar.team_id).limit(1))).first()
    if row:
        return row[0]
    # fall back: any team id already owning templates, else first team
    row = (await db.execute(select(ContentPromptTemplate.team_id).limit(1))).first()
    if row:
        return row[0]
    from app.models.team import Team

    row = (await db.execute(select(Team.id).limit(1))).first()
    if not row:
        raise SystemExit("no teams exist — create one before seeding templates")
    return row[0]


async def _pillar_id(db, team_id: uuid.UUID, name: str) -> uuid.UUID:
    row = (await db.execute(select(Pillar.id).where(Pillar.team_id == team_id, Pillar.name == name))).first()
    if row:
        return row[0]
    p = Pillar(team_id=team_id, name=name)
    db.add(p)
    await db.flush()
    print(f"  + pillar '{name}' created ({p.id})")
    return p.id


async def seed(team_id_arg: str | None, seed_path: Path) -> None:
    templates = json.loads(seed_path.read_text())["templates"]
    async with async_session_maker() as db:
        team_id = await _resolve_team_id(db, team_id_arg)
        for t in templates:
            pillar_id = await _pillar_id(db, team_id, t["pillar_name"])
            row = (
                await db.execute(
                    select(ContentPromptTemplate).where(
                        ContentPromptTemplate.team_id == team_id,
                        ContentPromptTemplate.name == t["name"],
                    )
                )
            ).scalar_one_or_none()
            values = {f: t[f] if f != "pillar_id" else pillar_id for f in FIELDS}
            if row is None:
                db.add(
                    ContentPromptTemplate(
                        id=uuid.UUID(t["id"]),
                        team_id=team_id,
                        name=t["name"],
                        **values,
                    )
                )
                print(f"  + {t['name']}  (created, id={t['id']})")
                continue
            changed = [f for f in FIELDS if getattr(row, f) != values[f]]
            for f in changed:
                setattr(row, f, values[f])
            print(f"  {'~' if changed else '='} {t['name']}  ({'updated: ' + ','.join(changed) if changed else 'unchanged'})")
        await db.commit()
    print(f"seeded {len(templates)} templates for team {team_id}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--team-id", default=None, help="target team UUID (default: first team)")
    ap.add_argument("--file", default=str(SEED_FILE))
    args = ap.parse_args()
    asyncio.run(seed(args.team_id, Path(args.file)))


if __name__ == "__main__":
    main()
