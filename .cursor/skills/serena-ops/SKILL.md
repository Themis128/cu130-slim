# Serena Ops

Query the Serena code-intelligence index for `cu130-slim` from scripts and
agents — symbol search, pattern search, callers — when the MCP server is
down or a quick one-off lookup beats a full REPL session.

## When to use

- Locate which file defines a string, symbol, or regex (e.g. "where does
  `🤖 Auto-reply:` come from").
- Class-audit: enumerate every `for…await external-call` site, every
  `*_sweep` function, every caller of a changed signature.
- Verify a change set's blast radius (find referencing call sites before
  editing).

## Endpoints

| Server | Port | Transport | State |
|--------|------|-----------|-------|
| Facade bridge | **9123** | HTTP POST `call_facade_method` | Usually up — use `scripts/serena_call.py` |
| MCP REPL | 9121 | streamable-http SSE | Flaky — dies; `serena` not on PATH for relaunch |

### Facade bridge (:9123) — the reliable lane

```bash
python3 .devin/skills/serena-ops/scripts/serena_call.py fs search_for_pattern '["_PRICING_RESPONSES"]'
python3 .devin/skills/serena-ops/scripts/serena_call.py fs get_symbols_overview '["social-automation/backend/app/services/messenger_chatbot.py"]'
```

- POST `http://127.0.0.1:9123/call_facade_method`
- Header `Authorization: Bearer fdab942c-eca4-41ae-ad34-2563152d3613`
- JSON body: `{project_name:"cu130-slim", facade_name, method_name, args, kwargs}`
- **Facade names take NO `s.` prefix.** Valid: `cfg`, `fs`, `edit`, `mem`,
  `shell`, `lsp`. `facade_name:"s.fs"` → `Unknown facade`.
- Response body is a **pickled** `serena.repl.*` object. Without serena on
  PYTHONPATH it cannot be unpickled — `serena_call.py` extracts file paths
  and strings from the payload, which covers locate/class-audit use cases.

### MCP REPL (:9121) — when it's up

- Only tools: `initial_instructions` + `serena_repl`. Call
  `initial_instructions` first — it returns a `session` id that is a
  **required** param on every `serena_repl` call.
- `search_for_pattern` returns `PatternMatches` — iterate `.matches`; each
  match has `.source_file_path` + `.start_line` (NOT `.file_path`).
- `get_symbols_overview` returns a non-subscriptable `LspSymbolCollection`.
- `find_referencing_symbols` has been observed to 500 — fall back to
  `search_for_pattern` on the callee name.
- Newline escapes in Python code passed to the REPL are fragile — build
  strings with `chr(10)` joins.

## Index caveat

Serena indexes `~/cu130-slim` — the **shared checkout** that other agents
check out and reset. When Serena results disagree with what you committed,
**pushed branches and worktrees are truth**, not the index view. Do your own
work in a dedicated worktree (`~/cu130-slim-wt-*`).

## Scripts

- `scripts/serena_call.py` — facade bridge CLI (see usage in file docstring).

## Related skills

- `pr-sweep-ops` — after locating a bug class, the fix lands through a PR.
- `external-call-budget` — the class-audit pattern (unbounded external
  calls inside Celery loops) this skill was built to enumerate.
