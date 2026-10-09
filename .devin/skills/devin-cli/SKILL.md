---
name: devin-cli
description: Locate and use the bundled `devin` CLI (plugins install/list/update/remove, auth login/status, sessions) inside the Windsurf Devin extension host. Use when the user runs `devin ...` commands, manages plugins (e.g. AgentMemoryRepo), or the CLI reports "command not found" / "not logged in".
---

# Devin CLI (bundled)

`devin` is NOT on PATH. The binary ships inside the Windsurf Devin extension:

```
~/.devin-server/bin/<build-hash>/extensions/windsurf/devin/bin/devin
```

Resolve the current build dynamically:

```bash
DEVIN=$(ls -d ~/.devin-server/bin/*/extensions/windsurf/devin/bin/devin | head -1)
```

Man pages ship alongside: `.../devin/share/man/man1/devin-*.1`
(`devin plugins --help`, `devin-plugins-install.1`, etc.).

## Auth

- CLI auth is separate from the IDE session. Credentials live at
  `~/.local/share/devin/credentials.toml`.
- `devin auth status` → `Not logged in` means plugin/session commands will fail
  with `You must be logged in to manage plugins`.
- `devin auth login` (browser) or `devin auth login --force-manual-token-flow`
  (SSH/headless) — **the user must run this interactively**; an agent cannot
  complete the OAuth/token paste step.

## Plugins

```
devin plugins install <owner/repo>   # e.g. AgentMemoryRepo/agentmemoryrepo
devin plugins list / update / remove
```

Installed plugins land in `~/.local/share/devin/cli/plugins/`. The marketplace
catalog is `github.com/CognitionAI/devin-marketplace`.

## Agent checklist for `devin plugins install` requests

1. Resolve binary path (above).
2. `devin auth status` — if not logged in, tell the user to run
   `devin auth login` themselves; don't retry the install in a loop.
3. After login, run the install, then confirm with `devin plugins list` and by
   checking `~/.local/share/devin/cli/plugins/`.
