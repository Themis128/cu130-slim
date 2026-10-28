# n8n MCP first + create missing tooling

## Always use the n8n MCP server for n8n work

- For anything touching n8n — deploying, importing, publishing, triggering,
  inspecting, or debugging workflows — use the **`n8n` MCP server** tools
  (`mcp_call_tool` on server `n8n`), not raw curl/shell against the n8n API.
- The server is registered in `.devin/mcp_config.json` →
  `.devin/skills/n8n-cloudless/scripts/n8n-mcp-server.py`.
- The `n8n-cloudless` skill (`.devin/skills/n8n-cloudless/`) documents the
  workflow inventory, deploy/publish/trigger scripts, and gotchas
  (N8N_API_KEY 401, publish:workflow, webhook paths). Invoke it for n8n tasks.
- If the MCP server is unreachable, fall back to the skill's scripts, then
  direct API — in that order, and report the failure.

## If a tool or skill doesn't exist, create it — with web research

When a task needs a capability that has no existing MCP tool, skill, or
script:

1. **Check first**: `skill list`/`search`, `mcp_list_tools` across all
   servers, `.devin/skills/*/scripts/`, and `.cursor/skills/*/scripts/`.
2. **Research**: use web search + official docs to get the correct API
   surface, endpoints, auth, and current conventions before writing code —
   never guess API shapes.
3. **Build it**: skills live in `.devin/skills/<name>/SKILL.md` (+ `scripts/`
   for tools), mirrored to `.cursor/skills/` when useful. MCP servers are
   registered in `.devin/mcp_config.json`. Follow existing skill/tool
   conventions (stdlib-first scripts, no hardcoded secrets, env-based config).
4. **Verify live**: test the new tool against the real service before
   reporting done.
5. **Commit**: skills and tools are part of the repo — commit and push.

Prefer extending an existing skill's `scripts/` over creating a parallel
one-off script that rots.
