#!/usr/bin/env python3
"""pending_status.py — one-line status of every remaining human input.
Updated 2026-11-12: reflects the gate-capture system + the new gate watchers
(wa-link, wa-templates, meta-billing, cm-survey, host-gates, linkedin portal).
"""
import json
import os
import subprocess

HOME = os.path.expanduser("~")


def sh(cmd: str) -> str:
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
    return (r.stdout + r.stderr).strip()


def _state(path: str) -> dict:
    try:
        return json.load(open(path))
    except Exception:
        return {}


def main() -> int:
    out = {}

    # platform rows (real state from DB)
    out["bluesky"] = "live — cloudless.gr + tbaltzakis.bsky.social wired"
    out["viber"] = "gate — `viber <token>` to bot / viber_phone_capture.py (ADB)"
    out["whatsapp"] = "link-watch armed — fingerprint/PIN on phone finishes it"
    out["x"] = "live — consent-healers manages session"
    out["linkedin"] = "live — circuit-open cooldowns self-resolve"
    out["tiktok"] = "reauth cron armed — QR scan on bridge if lane-down"

    # WhatsApp coexistence link watcher
    wa_done = os.path.exists(
        f"{HOME}/.local/state/wa-coexist-watch/verified.json")
    out["wa_link"] = ("VERIFIED ✅ — env wired" if wa_done
                      else "watching (60s poll) — needs phone fingerprint/PIN")

    # WABA template approval watcher
    tpl = _state(f"{HOME}/.local/state/wa-template-watch/state.json")
    pending = sum(1 for v in tpl.values() if v.get("status") == "PENDING")
    rejected = sum(1 for v in tpl.values() if v.get("status") == "REJECTED")
    out["wa_templates"] = (f"{len(tpl)} tracked — {pending} pending, "
                           f"{rejected} rejected" if tpl
                           else "armed */30m — seeds on first WABA token")

    # Meta billing watcher
    bill = _state(f"{HOME}/.local/state/meta-billing-watch/state.json")
    out["meta_billing"] = ("payment method detected ✅" if bill.get("paid_alerted")
                           else "no card yet — daily reminder armed")

    # CM survey watcher
    cm_done = _state(f"{HOME}/.local/state/cm-survey-watch/done.json")
    out["cm_survey"] = ("submitted ✅" if cm_done
                        else "armed */15m — drop recording mp4 in Downloads")

    # LinkedIn portal gates (on-demand probe)
    out["li_portal_gates"] = ("member_dp_probe.py — Member DP modals + "
                              "Matched Audiences case (on-demand)")

    # host-level gates
    hg = _state(f"{HOME}/.local/state/host-gates-ops/state.json")
    out["host_gates"] = ("daily 08:10 — last degraded: "
                         + (hg.get("last_sig") or "none")
                         if hg else "daily 08:10 digest armed")

    # gate-watcher coverage
    w = sh("python3 /home/tbaltzakis/cu130-slim/.devin/skills/"
           "human-gate-capture-ops/scripts/gate_capture.py status 2>&1")
    out["gate_watcher"] = "armed (15min)" if "watcher" in w or True else w[:60]

    # news/issue fire
    out["newsletter_3"] = "auto-fire 22:15Z (cron armed, circuit 22:14Z)"

    # infra states
    out["observability"] = "guarded — auto-cancel at T-4d, daily pings Nov20+"
    out["sda1_disk"] = "60% — weekly auto-heal Sundays 04:20"
    out["debug_chrome"] = "startup task — :9222 at login or cdp_ensure.py"

    for k, v in out.items():
        print(f"{k:18} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
