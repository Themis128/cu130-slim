---
name: deepwiki-docs
description: Query DeepWiki's free public MCP (server `deepwiki`, https://mcp.deepwiki.com/mcp) for AI-generated docs on any public GitHub repo — read_wiki_structure, read_wiki_contents, ask_wiki_question. Use when researching how an external library/service works instead of reading raw source.
---

# DeepWiki Docs MCP

Free, no-auth MCP server by Cognition — already registered in
`.devin/mcp_config.json` as `deepwiki` with `"url": "https://mcp.deepwiki.com/mcp"`
(streamable HTTP; `/sse` exists but is deprecated).

## Tools worth using

- `ask_wiki_question(repo, question)` — fastest path; AI answer grounded in the
  repo's generated wiki.
- `read_wiki_structure(repo)` — topic list for a repo.
- `read_wiki_contents(repo)` — full wiki.
- `devin_knowledge_manage` etc. are private-mode only (need
  `https://mcp.devin.ai/mcp` + Bearer key) — ignore them on the public endpoint.

## When to reach for it

- Before writing integration code against a third-party repo (e.g. an n8n node,
  a Go/Rust service) — ask DeepWiki for architecture/usage instead of cloning.
- Repo must be public and indexed; unindexed repos can be triggered via
  deepwiki.com (public index), no API needed.

## Pitfalls

- Endpoint returns **SSE-framed** responses (`event: message\ndata: {...}`) —
  a curl probe looks like it hangs; it isn't, it's just streaming.
- Config field is `url` (not `serverUrl` — that's Devin Desktop-specific).
