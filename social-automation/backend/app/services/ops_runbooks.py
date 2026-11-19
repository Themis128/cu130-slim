"""Curated ops runbooks for the ops-agent RCA prompt.

Distilled from the repo's operational documentation (AGENTS.md, the
*.devin/skills operator guides, and incident post-mortems). The baked
container image does not carry the repo docs, so the knowledge the
agent needs travels as code — each entry is matched against the
incident kind/target/title and the top hits are injected into the LLM
prompt. Keep entries short, factual, and action-oriented; this is the
agent's "documentation and best practices" layer, not prose.
"""

from __future__ import annotations

import re

# (keywords, runbook text) — first match wins per entry, cap applied by caller.
_RUNBOOKS: list[tuple[tuple[str, ...], str]] = [
    (
        ("fsbridge", "bind mount", "bind-mount", "docker-desktop", "127", "empty dir"),
        "WSL2 fsbridge class: Docker Desktop's dd-user-distro-proxy dies on a "
        "vsock flap; NEW bind-mounts materialize as EMPTY dirs -> containers "
        "exit 127 / crash-loop on unreadable configs. Detection: proxy pgrep + "
        "empty hashed mount dirs under /run/desktop/mnt. Durable heal is ONLY "
        "`wsl --shutdown` (docker desktop restart from Windows). Interim: run "
        "services from baked images without bind mounts (stack-rescue lane) or "
        "inject files with docker cp (ephemeral, dies on recreate). "
        "See wsl-fsbridge-ops / stack-rescue-ops skills.",
    ),
    (
        ("docker.sock", "socket proxy", "socket-proxy", "docker socket"),
        "docker-socket-proxy stale-inode class: /var/run/docker.sock is a "
        "single-file bind mount — across a daemon restart it follows the OLD "
        "inode and the proxy serves 503s forever. Fix: restart the proxy "
        "container (autoheal=true label now converges it). Same class hits "
        "compose-healer. Never re-create; restart is enough.",
    ),
    (
        ("unhealthy", "healthcheck", "health check"),
        "Healthcheck discipline: the backend image has NO curl/wget — use a "
        "python3 urllib one-liner as the check. A container marked unhealthy "
        "blocks `depends_on: condition: service_healthy` dependents. If a "
        "service answers 200 manually but stays unhealthy, suspect the check "
        "binary before the app.",
    ),
    (
        ("database_url", "psycopg2", "asyncpg", "database", "postgres"),
        "DB driver rule: the baked backend image ships asyncpg ONLY — "
        "DATABASE_URL must be postgresql+asyncpg://... ; a bare "
        "postgresql:// DSN makes every service crash-loop on "
        "ModuleNotFoundError: psycopg2. Standalone compose app DB is `social` "
        "with POSTGRES_* creds; the main stack uses db `social_automation` "
        "with SOCIAL_POSTGRES_* creds. Raw SQL inserts into tables whose ORM "
        "uses client-side uuid defaults need explicit gen_random_uuid().",
    ),
    (
        ("530", "tunnel", "cloudflared", "edge conn"),
        "Cloudflare Tunnel class: HTTP 530 = zero edge connections on the "
        "named tunnel (connector dead/absent). Ensure a cloudflared service "
        "exists in the active compose file and uses protocol http2 — QUIC/UDP "
        "is blackholed by WSL2 NAT on this host. Verify registrations in the "
        "connector logs; the dashboard 'healthy' flag lags.",
    ),
    (
        ("restart", "crash", "exit", "loop"),
        "Container restart policy: never `docker compose restart` a service "
        "while a deploy lock or rescue lane may be active — check "
        "deploy/socialauto-standalone + lock files first. Crash-loop triage "
        "order: logs -> env drift (.env vs container env) -> missing baked "
        "files (image older than checkout -> docker cp is ephemeral only) -> "
        "bind-mount health (fsbridge).",
    ),
    (
        ("expired", "token", "credential", "unauthorized", "401", "403"),
        "Credential class: check social_accounts.status + the per-platform "
        "*_tool.py diagnostic (meta_tool/linkedin_tool/x_tool/tiktok_tool) "
        "before assuming code failure. OAuth tokens die on a schedule — the "
        "re-auth lanes (meta-oauth-ops, linkedin-reauth-ops, x-oauth-wire) "
        "own refresh; report_incident should escalate, not improvise new "
        "token paths.",
    ),
    (
        ("n8n",),
        "n8n class: crash-loop on 'database n8n does not exist' means the "
        "postgres db was never created — create it in the stack's postgres. "
        "2.x publish semantics: imported workflows are INACTIVE until "
        "published via API/MCP; N8N_API_KEY 401s on stale keys.",
    ),
    (
        ("dmr", "model runner", "inference", "llm"),
        "DMR class: /models 200 is NOT proof a model serves — engines accept "
        "then hang on /chat/completions for unloadable models (dmr-ops "
        "doctor probes per-engine). VRAM pressure unloads models silently; "
        "the inference chain falls back DMR -> Cloudflare Workers AI.",
    ),
    (
        ("telegram", "channel_post", "webhook", "bot"),
        "Telegram class: bots CANNOT read channel history (Bot API limit) — "
        "reads flow through telegram_channel_messages (send-time archive for "
        "bot posts + channel_post webhook for external posts + Telethon "
        "backfill for history). If alerts send but archive stays empty, "
        "check tg_alert._archive DSN; if webhook posts never arrive, check "
        "allowed_updates registration (channel_post, edited_channel_post) "
        "and the webhook secret token.",
    ),
    (
        ("disk", "space", "pressure", "prune"),
        "Disk class: WSL vhdx growth + overlay2 accumulation — check "
        "`docker system df`; the host cron prunes on schedule. OOM-ish "
        "symptoms on workers often trace to disk pressure evicting caches "
        "before memory limits.",
    ),
    (
        ("oom", "memory", "killed"),
        "OOM class: celery workers get SIGKILL'd by the kernel OOM killer on "
        "media tasks (ffmpeg/ComfyUI hand-offs). Check `docker inspect "
        "--format '{{.State.OOMKilled}}'` — a 137 exit with OOMKilled=true "
        "means raise the limit or move the task to the GPU/media queue, not "
        "retry forever.",
    ),
    (
        ("celery", "queue", "worker", "task", "backlog"),
        "Worker class: queued-but-never-consumed means the task name is "
        "unregistered in the worker image (celery-delivery-ops probe) or the "
        "beat schedule points at a module the image lacks. Baked-image "
        "gotcha: new task files do not exist inside running containers — "
        "register in celery_app.py (include list + beat_schedule + route) "
        "AND rebuild the image; docker cp is a dev-only stopgap.",
    ),
]

_MAX_RUNBOOKS = 3


def _haystack(kind: str, target: str, title: str, detail: str) -> str:
    return f"{kind} {target} {title} {detail}".lower()


def runbook_context(kind: str, target: str, title: str = "", detail: str = "") -> str:
    """Return the most relevant runbook snippets for an incident, or ''."""
    hay = _haystack(kind, target, title, detail)
    hits: list[str] = []
    for keywords, text in _RUNBOOKS:
        if any(re.search(re.escape(k), hay) for k in keywords):
            hits.append(text)
        if len(hits) >= _MAX_RUNBOOKS:
            break
    if not hits:
        return ""
    body = "\n\n".join(f"- {t}" for t in hits)
    return f"Relevant ops runbooks (follow these over generic advice):\n{body}\n"
