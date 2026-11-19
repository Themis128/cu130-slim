#!/usr/bin/env python3
"""ntfy_triage.py — read, cluster, and route ntfy alert history.

The publishing side is covered (alert-relay, grafana, kuma → write-only
topics). This tool is the READ side: it fetches cached messages from the
self-hosted ntfy, filters keepalive noise, clusters recurring issues, and
maps each signature to the owning skill + a safe diagnostic probe.

Topics: cloudless-alerts, cloudless-ops, nextcloud-ops, devoptimizer-alerts
Auth:   publish users are write-only. Reads need the `phone` user's token.
        Resolution chain: $NTFY_READ_TOKEN -> ~/.cache/ntfy-reader-token
        -> auto-mint via `ssh github-omv` (needs ~/.ssh/omv_id_rsa).

Usage:
    ntfy_triage.py                  # digest of the last 7 days (default)
    ntfy_triage.py digest --days 2
    ntfy_triage.py read --topic cloudless-alerts --days 1
    ntfy_triage.py routes           # print the signature->skill table
    ntfy_triage.py probe            # digest + run read-only probes for open issues
    ntfy_triage.py mint-token       # mint + cache a phone read token
    ntfy_triage.py --json           # machine-readable output
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HOME = Path.home()
REPO = Path(__file__).resolve().parents[4]
TOKEN_CACHE = HOME / ".cache" / "ntfy-reader-token"
SSH_HOST = "github-omv"
SSH_KEY = HOME / ".ssh" / "omv_id_rsa"
SSH = ["ssh", "-i", str(SSH_KEY), "-o", "BatchMode=yes",
       "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10", SSH_HOST]

TOPICS = ["cloudless-alerts", "cloudless-ops", "nextcloud-ops",
          "devoptimizer-alerts"]
BASES = ["https://ntfy.cloudless.gr", "http://github-omv:30080"]
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) ntfy-triage/1.0"}

# Probes that can mutate host/service state — `probe` skips them unless
# --mutate is passed (triage's default posture is read-only).
MUTATING_PROBES = ("host_gates_check", "mail_pipeline_probe", "docker_engine_guard")

# Keepalive/noise signatures (title or first line of body).
NOISE = [
    r"canary ok", r"token ok", r"^\[phone\]", r"You received a file",
    r"deep health ok",
]
RECOVERY = re.compile(r"\brecover(?:ed|y)?\b|\bhealed\b|healthy again|back online|\bis back\b|\bresolved\b|\bfixed\b", re.I)

# Ordered (regex on normalized signature, skill, probe, note).
# probe=None means the lane is a human gate or external dashboard.
ROUTES = [
    (r"RAM guard|Host Guard|host-gates|RAM (low|critical)|models evicted",
     "host-gates-ops",
     "python3 .devin/skills/host-gates-ops/scripts/host_gates_check.py --json",
     "RAM pressure on Windows host evicts DMR models; check ram gate first"),
    (r"DMR|dmr-endpoint|dmr serve|chat hangs",
     "dmr-ops",
     "python3 .devin/skills/dmr-ops/scripts/dmr_doctor.py",
     "engine accepts but cannot serve = cold-load/hang; dmr_doctor probes each engine"),
    (r"docker-engine-guard|Docker port|Docker-WSL|wsl-guardian|Ubuntu-24.04 terminated|mcp-docker|vsock|Docker Port Proxy",
     "docker-engine-ops + wsl-docker-doctor",
     "python3 .cursor/skills/docker-engine-ops/scripts/docker_engine_guard.py --json",
     "wrong-daemon / dead proxy class; durable heal may need wsl --shutdown"),
    (r"worker-health:|starting exited containers|celery-beat.*DOWN",
     "social-stack-ops",
     "docker ps -a --format '{{.Names}}\t{{.Status}}' | grep -E 'social-worker|celery|social-api'",
     "workers down with restarts=0 usually follows a docker/WSL flap"),
    (r"CDP debug Chrome|:9229|debug-browser|chrome-debug",
     "consent-root-ops + cdp-bind-pattern",
     None,
     "chrome-debug-profile on the Windows desktop; restart-chrome-debug.ps1 lane"),
    (r"Instagram re-auth|FB sidecar session|Meta.*re-auth|no-login-form",
     "meta-oauth-ops + meta-account-lock-ops",
     None,
     "human gate (2FA/CAPTCHA/login-approval) — see logs/instagram_reauth"),
    (r"MAIL PIPELINE|dovecot|mail-outbound",
     "omv-ha-mail",
     "python3 scripts/mail_pipeline_probe.py --no-alert --json",
     "hop-by-hop probe; dovecot refused = postfix/dovecot down on omv-ha"),
    (r"endpoint-watch:.*(cloud|office|signal|push)\.cloudless",
     "omv-nextcloud-ops",
     "for h in cloud office signal push; do echo -n \"$h: \"; curl -s -o /dev/null -w '%{http_code}' --max-time 8 https://$h.cloudless.gr/; echo; done",
     "Nextcloud workspace tunnel flap on cu130 social-cloudflared"),
    (r"cf-workers-health|fleet-cloudless",
     "safedeploy-watchdog (cloudless.gr repo)",
     "curl -s -o /dev/null -w '%{http_code}' --max-time 10 https://cloudless.gr/",
     "edge path: cloudless2 proxy -> pi-origin tunnel -> k3s cloudless-app"),
    (r"report-delivery: MISSING",
     "report-delivery-ops",
     "python3 .devin/skills/report-delivery-ops/scripts/report_delivery_watch.py --status",
     "expected daily reports not in tbaltzakis@cloudless.gr mailbox"),
    (r"media-heal|media-integrity|Agent workforce|missing media",
     "agent-workforce + media-qa-jupyter",
     "docker compose exec -T social-api python scripts/workforce_audit.py --json",
     "escalated workers exhausted retries; media objects may need regen"),
    (r"generate_video_asset|stack-ops:8199|ComfyUI|Wan2",
     "ai-video-ops + social-stack-ops",
     "curl -s -o /dev/null -w '%{http_code}' --max-time 8 http://stack-ops:8188/system_stats || echo unreachable",
     "ComfyUI backend 503 = stack-ops compose service down"),
    (r"Funnel audit|broken stage",
     "funnel-ops",
     "cd social-automation/backend && python3 scripts/funnel_audit.py 2>&1 | tail -30",
     "stage-to-stage conversion check against Postgres + EspoCRM"),
    (r"Termux service down|termux|phone-adb|galaxy-s22",
     "phone-net-ops + phone-access",
     "adb devices",
     "sshd down on S22+ kills SMS-OTP + ntfy relay lanes"),
    (r"C: drive|disk.*(CRITICAL|free)",
     "windows-host-ops + omv-disk-ops",
     "df -h / /mnt/c 2>/dev/null | head -5",
     "host C: fill breaks WSL; omv sda1 fill breaks k3s"),
    (r"pi-host|omv:systemd|omv-main-heartbeat|failed-unit",
     "omv-ops + analytics-stack",
     "ssh -i ~/.ssh/omv_id_rsa github-omv 'systemctl --failed --no-pager | head -10'",
     "nas-auto-remediate owns single restarts; persistent fails escalate"),
    (r"MCP health change",
     "mcp-ops",
     None,
     "usually informational; engine-down on mcp-docker = docker guard's turf"),
    (r"synigoros",
     "synigoros-ops",
     "cd social-automation/backend && python3 scripts/synigoros_status.py 2>&1 | tail -20",
     "Ombudsman status poll; ClientError = site/bridge hiccup"),
    (r"SoftTimeLimit|TimeLimitExceeded|Worker task failed|run_central_brain|auto_tag_asset|notebook_report",
     "agent-workforce + external-call-budget",
     None,
     "post-success timeout class: persist before optional post-processing"),
    (r"CRM engagement sweep|espocrm",
     "espocrm-ops",
     None,
     "sweep metrics; errors field >0 = lead lane issue"),
    (r"Nextcloud feed|Nextcloud auto-restart|nextcloud-ops",
     "omv-nextcloud-ops",
     "ssh -i ~/.ssh/omv_id_rsa github-omv 'cd /srv/dev-disk-by-uuid-fa6231ab-eae7-40ea-a4b6-400f767a89d7/workspace && docker compose ps 2>/dev/null | head'",
     "feed timeouts = Nextcloud cron/redis hiccup; auto-restart already fired"),
    (r"Viber|viber_business",
     "viber-bot-ops",
     "cd social-automation/backend 2>/dev/null; python3 .cursor/skills/viber-bot-ops/scripts/viber_token.py --check 2>/dev/null | tail -5",
     "viber_business_setup.py gate; ADB back = lane live"),
    (r"log triage",
     "log-triage digest (informational)",
     None,
     "sampled k8s/app logs — inner patterns route to their own skills"),
    (r"LinkedIn|linkedin",
     "linkedin-graph-ops",
     None,
     "scope/portal notices; usually informational"),
]


def norm_sig(title: str, msg: str) -> str:
    s = (title + " " + (msg or "")).strip()
    s = re.sub(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?Z?", "", s)
    s = re.sub(r"[0-9a-f]{8}-[0-9a-f-]{27,}", "<id>", s)
    s = re.sub(r"\d+(?:\.\d+)?\s*(?:MB|GB|KB|TB|MB/s|%)", "<n>", s)
    s = re.sub(r"\b\d+\s*AI model", "<n> AI model", s)
    s = re.sub(r"\b\d{2,}\b", "<n>", s)
    s = re.sub(r"\s+", " ", s)
    # merge "X recovered" into the "X DOWN" cluster so the cluster's
    # latest event decides open vs resolved
    if re.search(r"endpoint-watch|cf-workers-health|dmr-endpoint-watch|host ", s, re.I):
        s = re.sub(r"\b(recovered|healthy again|is back)\b", "down", s, flags=re.I)
    return s[:110]


def is_noise(title: str, msg: str) -> bool:
    t = f"{title} {msg}".lower()
    return any(re.search(p, t, re.I | re.M) for p in NOISE)


def route_for(sig: str):
    for pat, skill, probe, note in ROUTES:
        if re.search(pat, sig, re.I):
            return {"skill": skill, "probe": probe, "note": note}
    return {"skill": None, "probe": None, "note": "unrouted — inspect manually"}


def mint_token(user: str = "phone") -> str:
    out = subprocess.run(
        SSH + ["sudo k3s kubectl -n ntfy exec -i deploy/ntfy -- "
               f"ntfy token add {user} --label ntfy-triage-reader"],
        capture_output=True, text=True, timeout=60)
    m = re.search(r"token (tk_[a-z0-9]+)", out.stdout + out.stderr)
    if not m:
        raise SystemExit(f"token mint failed: {out.stderr.strip() or out.stdout.strip()}")
    TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_CACHE.write_text(m.group(1) + "\n")
    TOKEN_CACHE.chmod(0o600)
    return m.group(1)


def get_token() -> str:
    if os.environ.get("NTFY_READ_TOKEN"):
        return os.environ["NTFY_READ_TOKEN"]
    if TOKEN_CACHE.exists():
        t = TOKEN_CACHE.read_text().strip()
        if t.startswith("tk_"):
            return t
    return mint_token()


def fetch(topic: str, since: int, token: str) -> list[dict]:
    last_err = ""
    for base in BASES:
        url = f"{base}/{topic}/json?poll=1&since={since}"
        req = urllib.request.Request(url, headers=UA)
        req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                out = []
                for line in r.read().decode().splitlines():
                    if not line.strip():
                        continue
                    try:
                        m = json.loads(line)
                    except Exception:
                        continue
                    if m.get("event") == "message":
                        out.append(m)
                return out
        except urllib.error.HTTPError as e:
            last_err = f"{base} HTTP {e.code}"
        except Exception as e:
            last_err = f"{base} {type(e).__name__}: {e}"
    print(f"warn: {topic} unreadable ({last_err})", file=sys.stderr)
    return []


def collect(days: float, topics: list[str]) -> dict[str, list[dict]]:
    token = get_token()
    since = int(time.time() - days * 86400)
    return {t: fetch(t, since, token) for t in topics}


def cluster(by_topic: dict[str, list[dict]]):
    issues: dict[str, dict] = {}
    noise = 0
    for topic, msgs in by_topic.items():
        for m in msgs:
            title, msg = m.get("title", ""), m.get("message", "") or ""
            if is_noise(title, msg):
                noise += 1
                continue
            sig = norm_sig(title, msg)
            c = issues.setdefault(sig, {
                "sig": sig, "count": 0, "topics": set(), "first": None,
                "last": None, "prio": 0, "sample": (msg or title)[:300],
            })
            c["count"] += 1
            c["topics"].add(topic)
            c["prio"] = max(c["prio"], m.get("priority") or 0)
            t = m.get("time", 0)
            c["first"] = t if c["first"] is None else min(c["first"], t)
            if t >= (c["last"] or 0):
                c["last"] = t
                c["last_body"] = (msg or title)[:200]
    out = []
    for c in issues.values():
        r = route_for(c["sig"])
        resolved = bool(RECOVERY.search(c.get("last_body", "")))
        out.append({
            "sig": c["sig"], "count": c["count"],
            "topics": sorted(c["topics"]), "prio": c["prio"],
            "first": time.strftime("%m-%d %H:%M", time.localtime(c["first"])),
            "last": time.strftime("%m-%d %H:%M", time.localtime(c["last"])),
            "status": "resolved" if resolved else "open",
            "sample": c["sample"], **r,
        })
    out.sort(key=lambda c: (c["status"] == "resolved", -c["prio"], -c["count"]))
    return out, noise


def run_probe(cmd: str, timeout: int = 120) -> str:
    try:
        r = subprocess.run(cmd, shell=True, cwd=REPO, capture_output=True,
                           text=True, timeout=timeout)
        tail = (r.stdout + r.stderr).strip().splitlines()[-8:]
        status = f"rc={r.returncode}"
    except subprocess.TimeoutExpired:
        tail, status = ["<timeout>"], "timeout"
    except Exception as e:
        tail, status = [str(e)], "error"
    return status + "\n  " + "\n  ".join(tail)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", nargs="?", default="digest",
                    choices=["digest", "read", "routes", "probe", "mint-token"])
    ap.add_argument("--days", type=float, default=7)
    ap.add_argument("--topic", action="append", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--open-only", action="store_true")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--mutate", action="store_true",
                    help="allow probes that can change host/service state")
    args = ap.parse_args()

    if args.cmd == "mint-token":
        print(f"token cached at {TOKEN_CACHE}")
        mint_token()
        return 0

    if args.cmd == "routes":
        for pat, skill, probe, note in ROUTES:
            print(f"{pat}\n  -> {skill}\n     probe: {probe or '(human/external)'}\n     {note}")
        return 0

    topics = args.topic or TOPICS
    by_topic = collect(args.days, topics)

    if args.cmd == "read":
        rows = []
        for t, msgs in by_topic.items():
            for m in msgs:
                rows.append({
                    "topic": t,
                    "t": time.strftime("%Y-%m-%d %H:%M", time.localtime(m["time"])),
                    "title": m.get("title", ""), "prio": m.get("priority"),
                    "msg": (m.get("message") or "")[:300],
                    "noise": is_noise(m.get("title", ""), m.get("message", "") or ""),
                })
        rows.sort(key=lambda r: r["t"])
        print(json.dumps(rows, indent=1) if args.json else
              "\n".join(f"[{r['t']}] {r['topic']} p{r['prio']} {r['title']} :: {r['msg'][:120]}" for r in rows))
        return 0

    clusters, noise = cluster(by_topic)
    if args.open_only:
        clusters = [c for c in clusters if c["status"] == "open"]

    if args.cmd == "probe":
        for c in clusters:
            if c["status"] == "open" and c["probe"]:
                if not args.mutate and any(m in c["probe"] for m in MUTATING_PROBES):
                    c["probe_out"] = "(mutating probe skipped — rerun with --mutate to execute)"
                else:
                    c["probe_out"] = run_probe(c["probe"], args.timeout)

    if args.json:
        print(json.dumps({"noise_filtered": noise, "issues": clusters}, indent=1))
        return 0

    total = sum(len(v) for v in by_topic.values())
    print(f"ntfy digest — last {args.days:g}d | {total} msgs, {noise} noise, {len(clusters)} issues\n")
    for c in clusters:
        mark = "OPEN " if c["status"] == "open" else "done "
        print(f"[{mark}] x{c['count']:>3} p{c['prio']} {c['first']}..{c['last']}  {c['sig'][:95]}")
        print(f"       route: {c['skill'] or '???'} — {c['note']}")
        if c.get("probe_out"):
            print(f"       probe ({c['probe'].split(' ')[0]}):")
            print("         " + c["probe_out"].replace("\n", "\n         "))
    return 0


if __name__ == "__main__":
    sys.exit(main())
