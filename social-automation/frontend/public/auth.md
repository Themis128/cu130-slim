# auth.md — Agent Authentication for social.cloudless.gr

## Overview

SocialAuto exposes a REST API at `https://social.cloudless.gr/api/v1`.
Interactive access is authenticated; agent access is issued per-request
through the account owner.

## How agents authenticate

1. **Human signup** — create an account at `/signup` (email + password).
2. **Token** — `POST /api/v1/auth/login` with `{"email","password"}`
   returns a bearer token. Send it as `Authorization: Bearer <token>`.
3. **Agent/service access** — request scoped credentials by contacting the
   owner via `/contact` or emailing tbaltzakis@cloudless.gr. Include your
   operator, purpose, and the scopes you need.

## Discovery documents

- `/.well-known/openid-configuration` — OIDC-style discovery metadata
- `/.well-known/oauth-authorization-server` — RFC 8414 metadata
- `/.well-known/oauth-protected-resource` — RFC 9728 protected-resource metadata
- `/.well-known/mcp/server-card.json` — MCP server card
- `/.well-known/agent.json` — A2A agent card

## Rate limits

Standard API rate limits apply; abusive automation is blocked at the edge.
