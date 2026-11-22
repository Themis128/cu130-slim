# linkedin-debug-lane — LinkedIn via the Windows debug Chrome (:9229)

Fallback browser lane for LinkedIn UI work while
`linkedin-browser-sidecar` (:9225) is in rate-limit cooldown. Drives the
`chrome-debug-profile` Chrome on the Windows host — the same logged-in
profile used by `linkedin-devapp-ops`, `linkedin-portal-ops`,
`qualtrics-ops`, and `li_grant_watch` portal probes.

## Lane map (probe before assuming)

```bash
python3 scripts/li_lane_status.py          # sidecar / cdp / api verdict
python3 scripts/li_lane_status.py --nav    # + live feed classification
python3 scripts/li_lane_status.py --tg     # TG #ops on usable-set change
```

| Lane | Endpoint | Covers | 429 behaviour |
|---|---|---|---|
| sidecar | `localhost:9225` `/health`, `/debug/*` | feed, DMs, campaign UI, session | container-level cooldown, auto-heal backoff |
| **cdp (this skill)** | `172.22.144.1:9229` (fallback `localhost:9229`) | dev portal, feed, any linkedin.com page | **shares the same IP-level 429** |
| api | `api.linkedin.com` + SocialAuto tokens | posting, analytics, ads, conversions, lead sync, DMP | unaffected — keep using `linkedin_tool.py` / tasks |

## The hard truth (verified 2026-11-16)

LinkedIn's 429 is **IP+account level, not per-browser**. During the
sidecar cooldown the debug Chrome returned `HTTP ERROR 429` on
linkedin.com/feed identically. Edge (:9222 lane) would hit the same
wall — do not burn time reviving it for LinkedIn pages. What keeps
working through a cooldown:

- **Everything API**: `linkedin_tool.py`, all Celery lanes
  (publish/engage/moderation/conversions/lead-sync/analytics/portability)
- **Non-linkedin.com pages** in the debug Chrome (Qualtrics forms live
  on `linkedincustomerops.qualtrics.com` — a DIFFERENT origin, usually
  still reachable during linkedin.com 429s; verify per-form)
- Gmail/mail watchers (approval emails still arrive and auto-process)

## 429 playbook (researched 2026-11-16 — MS Learn + anti-detection literature)

Two DIFFERENT 429s exist — don't confuse them:

| Surface | Mechanism | Reset |
|---|---|---|
| `api.linkedin.com` | Daily quota per endpoint, app-level + member-level counters | **midnight UTC** |
| `linkedin.com` pages (sidecar/CDP/Edge) | Behavioral/IP throttling — no published quota | LinkedIn's schedule, typically minutes–hours; can escalate to 24–72h restriction if poked |

Rules that follow from the research:

- **429 → back off, checkpoint → STOP.** A security check (authwall,
  checkpoint, CAPTCHA) is not a rate limit — never retry-loop it; it
  needs a human (`phone-2fa-ops`).
- **No retry storms.** Every probe is a hit on an already-throttled
  surface. `li_lane_status.py --nav` is the only probe you need; run it
  sparingly (the existing `li-portal-gate-watch.sh` 2h cron already
  detects clearing).
- **Detection is behavioral, not just volumetric.** Uniform delays,
  machine cadence, and parallel lanes are the loudest signals. Keep
  human jitter in any browser automation; never run the same action
  from sidecar AND cdp AND Edge simultaneously.
- **Cooldown state is shared.** `li_lane_status.py --nav` writes
  `~/.local/state/li-429-cooldown.json` on 429 and clears it on ok;
  `li_cdp.py status` prints the remaining window. The file keeps the
  LONGEST outstanding window — repeated probes don't extend it.
- **API quota 429s are different**: check Developer Portal → app →
  Analytics/Usage & Limits for the endpoint's real counters; they
  reset at midnight UTC. LinkedIn emails app admins at 75% usage.
- Escalation path if web-429 persists >24h: it's likely a soft
  restriction on the account/IP pair — stop ALL browser lanes, keep
  API lanes, and let it decay. Do not rotate fingerprints/proxies to
  evade (ToS + makes restrictions worse).

## Tools

`scripts/li_cdp.py` — minimal CDP driver (websocket-client, no deps
beyond it):

```bash
python3 li_cdp.py status                  # nav feed -> ok|loginwall|429|checkpoint
python3 li_cdp.py tabs                    # page targets
python3 li_cdp.py open <url> [wait]       # new background tab + classify
python3 li_cdp.py nav <url> [wait]        # navigate selected tab
python3 li_cdp.py eval <js>               # Runtime.evaluate
python3 li_cdp.py text                    # body innerText
python3 li_cdp.py click-text <label>      # scrollIntoView + real mouse click
python3 li_cdp.py type <sel> <text>       # native setter + input/change/Enter
python3 li_cdp.py shot <path.png>         # screenshot
python3 li_cdp.py close <url-substr>      # close tab
```

Tab selection: prefers a `linkedin.com` tab, else last page. `status`
reconnects the target websocket after navigation (nav can drop it).

`scripts/li_lane_status.py` — the router probe described above.

## Rules

- **Never run two drivers on the same tab** — the sidecar and this lane
  use different profiles so they CAN run concurrently, but two agents on
  :9229 will fight over tabs. Check `tabs` first; prefer `open` for
  isolated work.
- The debug Chrome is **shared** with consent/portal/gate flows — don't
  close tabs you didn't open (`close` matches by URL substring).
- 429 clears on LinkedIn's schedule (minutes–hours). `li_lane_status.py
  --nav` is the ground truth; `/health` on the sidecar does NOT detect
  page-level 429.
- When the sidecar heals, prefer it for feed/DM/campaign work (dedicated
  session + storage state); keep :9229 for portal/devapp work.
- Session in chrome-debug-profile is the same LinkedIn member session —
  treat 2FA/checkpoint screens as human gates (`phone-2fa-ops`).

## Infra notes

- :9229 is bound on the **Windows host** (`172.22.144.1` = WSL→Windows
  relay); `localhost:9229` works only while WSL localhost-forwarding is
  healthy — the script tries both.
- If :9229 is down: `schtasks /run /tn ChromeDebug` via
  `powershell.exe` (the task now runs `restart-chrome-debug.ps1`,
  unified 2026-11-16 — it manages chrome-debug-profile, NOT Edge :9222).
  `~/scripts/cdp-debug-watch.py` (host cron, every 10min) auto-restarts it.
- :9222/Edge lane is deprecated for agent use — LenovoVantage WebView2
  steals the port and agent-spawned launches can't bind it anyway
  (see `debug-launch-ops` for the history).
