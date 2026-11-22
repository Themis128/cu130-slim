# ntfy-triage-ops

The READ side of the self-hosted ntfy alert stack. `ntfy-ops` covers
server-side grants/tokens and publisher health; this skill covers the
question "what actually fired in the last N days and which skill owns it".

Pairs with **ntfy-ops** (server ACLs), **ntfy-phone-ops** (device side),
and **telegram-alert-ops** (the severity routing that produces most of
these messages).

## When to use

- "Check my ntfy messages / alerts" — read history, cluster issues
- "What broke this week" — digest + route each recurring alert to its
  owning skill
- A publisher's alerts arrive but an agent needs to *review* them — all
  publish users (`alert-relay`, `grafana-cloud-pub`, `kuma`,
  `cloudless-app`, `devoptimizer`) are **write-only**; they cannot read.

## Tool

`scripts/ntfy_triage.py` — zero deps (stdlib), works from repo root or
standalone. Same file is mirrored in `.cursor/skills/`.

```bash
python3 .devin/skills/ntfy-triage-ops/scripts/ntfy_triage.py            # 7d digest
python3 .devin/skills/ntfy-triage-ops/scripts/ntfy_triage.py digest --days 2
python3 .devin/skills/ntfy-triage-ops/scripts/ntfy_triage.py read --topic cloudless-alerts --days 1
python3 .devin/skills/ntfy-triage-ops/scripts/ntfy_triage.py routes     # signature -> skill table
python3 .devin/skills/ntfy-triage-ops/scripts/ntfy_triage.py probe      # digest + run safe probes for OPEN issues
python3 .devin/skills/ntfy-triage-ops/scripts/ntfy_triage.py --json     # machine-readable
python3 .devin/skills/ntfy-triage-ops/scripts/ntfy_triage.py mint-token # mint + cache read token
```


`scripts/ntfy_purge.py` — the write side: deletes cached messages matching
regex(es) once the underlying issue is verified resolved. Dry-run by
default; `--apply` deletes; 429-backoff built in; `--ids` for exact ids.

```bash
python3 .devin/skills/ntfy-triage-ops/scripts/ntfy_purge.py \
    --match "database system is starting up"            # dry-run list
python3 .devin/skills/ntfy-triage-ops/scripts/ntfy_purge.py \
    --match "IG business_login token dead" --apply       # delete resolved
```

Rule: purge ONLY after live verification (workers online, token probe
green, service healthy) — never delete an unresolved platform gate just
to silence it.

`probe` runs only read-only diagnostics (host_gates_check, dmr_doctor,
docker_engine_guard, mail_pipeline_probe --no-alert, docker ps, curl
status checks). It never restarts, heals, or pays anything.

## Auth model (verified 2026-11-18)

- Server: k3s `ntfy` ns on `github-omv` (pod `deploy/ntfy`), NodePort
  `:30080`, `auth_default_access: deny-all`.
- The `phone` user has **read-write** on every topic — it's the reader.
- Read token resolution chain:
  `$NTFY_READ_TOKEN` → `~/.cache/ntfy-reader-token` → auto-mint via
  `ssh -i ~/.ssh/omv_id_rsa github-omv "sudo k3s kubectl -n ntfy exec -i deploy/ntfy -- ntfy token add phone"`.
- Base URLs tried in order: `https://ntfy.cloudless.gr` then
  `http://github-omv:30080` (tailnet). Public hostname works for API
  reads — Cloudflare 403s bare python UAs, so the tool sends a
  browser-ish `User-Agent`. Both are enforced by the script.
- `.env` `NTFY_ALERT_TOKEN` / `NTFY_TOKEN` are write-only — they 403 on
  reads. Do not "fix" by granting read to `alert-relay`; mint a `phone`
  token instead.

## Topics

| topic               | producers                                   | typical content                        |
| ------------------- | ------------------------------------------- | -------------------------------------- |
| `cloudless-alerts`  | tg_alert fan-out, grafana, kuma             | ops/P2/critical alerts + digests       |
| `cloudless-ops`     | backend Celery alerts, phone notification mirror | worker failures, [phone] forwards  |
| `nextcloud-ops`     | nextcloud watchers                          | feed probes, auto-restarts             |
| `devoptimizer-alerts` | Windows host guards (devoptimizer)        | RAM guard, docker/WSL flaps, host gates|

## Noise floor

Filtered by default (still counted): `canary ok`, `devopt token ok`,
`[phone] *` notification mirrors, `You received a file`, `deep health
ok`, and `endpoint-watch: * recovered` lines. `--open-only` drops
clusters whose latest event looks resolved (recovered/back/ok/healed).

## Issue routes (as of 2026-11-18 sweep)

The script's ROUTES table maps alert signatures to owning skills —
`ntfy_triage.py routes` prints it. Recurring classes seen in the last
week and where they go:

- **RAM pressure / DMR evictions** → `host-gates-ops` +
  `dmr-ops`/`dmr_doctor.py`
- **Docker/WSL flaps** (engine-guard, wsl-guardian, Ubuntu terminated,
  port proxies) → `docker-engine-ops`, `wsl-docker-doctor`,
  `wsl-fsbridge-ops`
- **social-worker / celery-beat DOWN** → `social-stack-ops` (usually a
  docker/WSL flap side-effect)
- **CDP :9229 wedged/down, FB/IG re-auth gates** → `consent-root-ops`,
  `cdp-bind-pattern`, `meta-oauth-ops` (human gates)
- **MAIL PIPELINE FAIL** → `omv-ha-mail` +
  `scripts/mail_pipeline_probe.py`
- **cloud/office/signal/push endpoint flaps** → `omv-nextcloud-ops`
  (social-cloudflared tunnel)
- **fleet-cloudless-gr FAIL** → SafeDeploy watchdog (cloudless.gr repo)
- **report-delivery MISSING** → `report-delivery-ops`
- **media-heal / media-integrity / workforce escalations** →
  `agent-workforce`, `media-qa-jupyter`
- **generate_video_asset 503 (stack-ops:8199)** → `ai-video-ops` —
  ComfyUI backend down
- **SoftTimeLimit/TimeLimitExceeded task failures** →
  `external-call-budget`
- **Termux sshd down** → `phone-net-ops`, `phone-access`
- **C: drive critical** → `windows-host-ops`
- **pi-host omv systemd-degraded** → `omv-ops`, `analytics-stack`
- **Funnel audit broken stage** → `funnel-ops`
- **CRM engagement sweep errors** → `espocrm-ops`
- **synigoros poll failures** → `synigoros-ops`
- **MCP health change** → `mcp-ops` (mostly informational)

## Traps

- `ssh github-omv` needs `-i ~/.ssh/omv_id_rsa` — the ssh-agent isn't
  loaded in agent sessions; `ntfy_ops.py` (both copies) now passes it.
- ntfy cached-message history is only as deep as the server cache
  (default retention); beyond that, Slack/Telegram are the audit trail —
  the same alerts fan out there (`telegram-alert-ops`).
- Do not create extra read users; reuse `phone` (it already exists for
  the Android app). One `tk_` per agent lane, labeled
  `ntfy-triage-reader`.
