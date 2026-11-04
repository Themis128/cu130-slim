# Playwright path — tailnet ACL via admin console

Use when no TAILSCALE_API_KEY exists. The playwright MCP profile keeps the Google/Microsoft session.

1. `browser_navigate` → `https://login.tailscale.com/admin/acls`
2. If a login wall appears → ask the user to sign in (same pattern as Ubuntu One/CF).
3. `browser_evaluate` on the code editor: fetch current JSON via the page's own
   fetch to `/admin/api/acl`, merge tagOwners+grant, write back via the editor
   textarea + Save button (snapshot for the save ref).
4. Alternative zero-UI path from an authenticated page: the admin console
   accepts POST to `/admin/api/acl` with the JSON body — evaluate a fetch() POST.

Merge block (keep existing content — never replace the whole ACL):
{"tagOwners":{"tag:authentik-admin":["autogroup:admin"]},
 "grants":[{"src":["tag:authentik-admin"],"dst":["omv","omv-ha"],"ip":["tcp:22","tcp:443","tcp:6443","tcp:30910"]}]}
