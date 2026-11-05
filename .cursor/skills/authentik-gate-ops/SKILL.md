# authentik Gate Ops

Recipe for putting ANY internal web service behind authentik SSO+MFA on a public hostname — the same pattern used for infisical (`secrets.cloudless.gr`, deployed 2026-11-05). Use when a service has no native OIDC, or its OIDC is paywalled (e.g. Infisical free tier).

## The gate recipe (five parts, all verified live)

1. **authentik proxy provider** — create via API: `POST /api/v3/providers/proxy/` name `secrets.cloudless.gr`, external_host `https://<host>`, internal_host `http://<reachable-from-outpost>:<port>`, mode `proxy`.
2. **Application** — `POST /api/v3/core/applications/` {name, slug, provider:<pk>}.
3. **Assign to outposts** — `PATCH /api/v3/outposts/instances/{id}` add provider pk to `providers[]`. Both: Embedded Outpost + `cu130` proxy outpost (`authentik-outpost` docker container, :9000 on cu130 networks).
4. **DNS** — CNAME `<host>` -> `<tunnel-id>.cfargotunnel.` (tunnel 2efdd26e serves the cloudless.gr authentik-gated hosts).
5. **Tunnel ingress** — tunnel is remotely managed: `GET/PUT /accounts/{acct}/cfd_tunnel/{id}/configurations`, prepend rule `{"hostname":"<host>","service":"http://authentik-outpost:9000"}` before the catchall. cloudflared polls config (~60s); if the connector is exited, `docker compose up -d --force-recreate --no-deps cloudflared` (service name is `cloudflared`, container `social-cloudflared`).

## Verify

`curl -skI https://<host>/` -> 302 to `<host>/outpost.goauthentik.io/start?rd=...` = gate working. 530 = tunnel/origin broken (check `social-cloudflared` state + logs for `dial tcp ... connect: connection refused`). 404 after auth = internal_host unreachable from outpost container.

## authentik API basics

- Base `https://auth.cloudless.gr`, token `AUTHENTIK_ADMIN_TOKEN` in `.env`.
- List outposts: `GET /api/v3/outposts/instances/`; providers: `GET /api/v3/providers/proxy/` (field pk).
- Cloudflare user-scope token roll has NO API path — dashboard only. Account-scope tokens are minted via cloudflare-ops token tooling.

## WSL flap rule (from wsl-resilience-ops)

NEVER terminate the WSL distro or bounce the VM to fix `WSAETIMEDOUT` — each terminate triggers a ~50-container bootstorm that seeds the next flap. Retry 3-6x with sleeps; escalate only after >10min continuous failure.
