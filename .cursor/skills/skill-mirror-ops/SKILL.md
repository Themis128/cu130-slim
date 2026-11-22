# skill-mirror-ops

Audit and heal drift between the two skill mirrors — `.devin/skills/`
(canonical) and `.cursor/skills/` (Cursor agents). Agents create files on
whichever side they're operating in and silently leave the mirror
incomplete; stale copies then feed wrong guidance to the other side
(2026-11-22: `pr-watch-ops` .cursor copy still called CodeQL "advisory"
after the .devin copy + script made it actionable).

## Tool

```bash
python3 .devin/skills/skill-mirror-ops/scripts/skill_mirror_check.py          # report
python3 .devin/skills/skill-mirror-ops/scripts/skill_mirror_check.py --sync   # fill tracked gaps both ways
```

Classes:

| Class | Meaning | Action |
|---|---|---|
| `doc-drift` | SKILL.md differs between mirrors — dangerous stale guidance | resolve manually (can't auto-pick the truth), exit 1 |
| `script-gap` | a **tracked** file exists on only one side | `--sync` copies it to the missing side |
| `devin-only` / `cursor-only` | whole skill single-sided | expected — "mirror when useful" rule; informational |
| `ok` | mirrors consistent | — |

- Untracked files are skipped everywhere — live agent WIP, never touched.
- `__pycache__` / `.pyc` excluded — bytecode noise isn't drift.
- Run at session end after editing skill files, or wire into the weekly
  sweep alongside `pr-sweep`.

## Direction of truth

Neither side is authoritative for content — drift resolution is manual.
`--sync` only fills *missing* files; it never overwrites an existing
mirror file.
