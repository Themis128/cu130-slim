# tailnet-ops

Administer the office tailnet (tail4ecae1 — MagicDNS `*.tail4ecae1.ts.net`): ACL edits, serve config, device/tag inventory. Use for any Tailscale access-policy work — e.g. the `tag:authentik-admin` grant gating authentik admin to admins-only.

## Two automation paths (try in order)

1. **API path** — needs `TAILSCALE_API_KEY` (from admin console → Settings → Keys, or OAuth client). Store in `.env` or `~/.config/secrets/tailscale-api`. `scripts/apply_acl.py` reads it, merges tag+grant into the LIVE ACL JSON (never overwrite — fetch → merge → PUT).
2. **Playwright console path** — when no API key exists: drive `login.tailscale.com/admin/acls` with the playwright MCP (persistent profile keeps Google/Microsoft session). Steps in `scripts/console-acl.md` — opens editor, injects the tag+grant JSON via `browser_evaluate`, saves.

## The ACL block this stack wants

```json
"tagOwners": { "tag:authentik-admin": ["autogroup:admin"] },
"grants": [
  { "src": ["tag:authentik-admin"], "dst": ["omv", "omv-ha"], "ip": ["tcp:22", "tcp:443", "tcp:6443", "tcp:30910"] }
]
```

- `tag:authentik-admin` on devices that may administer authentik (office-1).
- Grant restricts SSH/admin/API to tagged devices (defense-in-depth on top of CF Access + tailscale serve).

## Gotchas hit live
- WSL `wsl -e` runs as root → ssh identity is `/root/.ssh`, NOT `/home/tbaltzakis/.ssh` — copy keys or use `-u tbaltzakis`.
- OMV `StrictModes` rejects home dirs with ANY path component writable (777 home-migration dir broke key auth).
- `tailscale serve --bg --set-path admin` worked for `/if/admin` proxying to NodePort.
- API key creation is console-only the FIRST time; afterwards everything is scriptable.
