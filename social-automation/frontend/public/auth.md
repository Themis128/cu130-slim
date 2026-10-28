# auth.md — Agent Authentication for social.cloudless.gr

SocialAuto exposes a REST API at `https://social.cloudless.gr/api/v1`.

```json agent_auth
{
  "register_uri": "https://social.cloudless.gr/signup",
  "token_uri": "https://social.cloudless.gr/api/v1/auth/login",
  "identity_types_supported": ["oauth2_client", "bot_token", "email_password"],
  "credential_types_supported": ["bearer"],
  "authorization_servers": ["https://social.cloudless.gr"],
  "protected_resource": "https://social.cloudless.gr/.well-known/oauth-protected-resource",
  "revocation_uri": "https://social.cloudless.gr/api/v1/auth/logout",
  "contact": "tbaltzakis@cloudless.gr"
}
```

## How agents authenticate

1. **Human signup** — create an account at `/signup`.
2. **Token** — `POST /api/v1/auth/login` with `{"email","password"}` → bearer token; send as `Authorization: Bearer <token>`.
3. **Scoped service access** — request via `/contact` or email tbaltzakis@cloudless.gr with operator, purpose, and scopes needed.

## Discovery
`/.well-known/oauth-authorization-server` · `/.well-known/oauth-protected-resource` · `/.well-known/mcp/server-card.json` · `/.well-known/agent.json`
