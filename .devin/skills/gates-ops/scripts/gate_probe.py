#!/usr/bin/env python3
"""Live probe for every owner-gated issue — current state + residual action.

Runs the cheap live checks (container env, SocialAuto DB via the *_tool.py
CLIs, adb devices, gh issue list) and prints one line per gate. Read-only;
never prints secrets. Use with gate_check.py (the static map) — this is the
"what is true right now" companion.

Usage: python3 gate_probe.py [--json]
"""

import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
BACKEND = REPO / "social-automation" / "backend"


def sh(cmd: list[str], timeout: int = 60) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:  # noqa: BLE001
        return 1, str(e)


def docker_tool(script: str, *args: str) -> str:
    rc, out = sh(
        ["docker", "exec", "-w", "/app", "social-api",
         "python3", f"scripts/{script}", *args],
        timeout=90,
    )
    # Strip sqlalchemy INFO noise; keep the payload lines.
    return "\n".join(
        ln for ln in out.splitlines()
        if not ln.startswith(("20", " ", "[")) and "INFO sqlalchemy" not in ln
    )


def adb_online() -> bool:
    rc, out = sh(["adb", "devices"], timeout=10)
    return rc == 0 and re.search(r"^\S+\s+device$", out, re.M) is not None


def gh_issues() -> dict[int, str]:
    rc, out = sh(
        ["gh", "issue", "list", "--state", "open", "--limit", "30",
         "--json", "number,title"],
        timeout=30,
    )
    if rc != 0:
        return {}
    try:
        return {i["number"]: i["title"] for i in json.loads(out)}
    except Exception:  # noqa: BLE001
        return {}


def probe_whatsapp() -> str:
    rc, out = sh(
        ["docker", "exec", "social-api", "sh", "-c",
         "printenv WHATSAPP_PHONE_NUMBER_ID | grep -q . && echo set || echo unset"],
        timeout=15,
    )
    if rc == 0 and out.strip() == "set":
        _, st = sh(
            ["docker", "exec", "-w", "/app", "social-api",
             "python3", "scripts/whatsapp_tool.py", "status"],
            timeout=60,
        )
        m = re.search(r"verified[^\n]*|VERIFIED|NOT_VERIFIED|display_phone[^\n]*", st)
        return f"registered — {m.group(0).strip() if m else st.strip()[:120]}"
    return "NOT registered — WABA assignment + phone verify pending (wa_verify_daemon retries request_code)"


def probe_meta() -> str:
    out = docker_tool("meta_tool.py", "accounts")
    fb = re.search(r"facebook\t(\S+)\t.*status=(\w+)", out)
    expired = [m.group(1) for m in re.finditer(r"\tfacebook\t\S+\t.*status=expired", out)]
    parts = []
    if expired:
        parts.append(f"facebook account(s) expired: {', '.join(expired[:2])}")
    if not parts:
        parts.append("no expired facebook rows in social_accounts")
    parts.append("business verification + leads_retrieval review = Meta console (evidence upload)")
    return "; ".join(parts)


def probe_instagram() -> str:
    out = docker_tool("instagram_tool.py", "accounts")
    m = re.search(r"@(\S+)\s+.*status=(\w+)", out)
    if not m:
        return "no instagram account rows"
    handle, status = m.group(1), m.group(2)
    extra = ""
    if status == "active":
        _, me = sh(
            ["docker", "exec", "-w", "/app", "social-api",
             "python3", "scripts/instagram_tool.py", "me"],
            timeout=60,
        )
        ok = '"username"' in me or "followers" in me
        extra = " — Graph API reachable" if ok else " — Graph API probe failed"
    return f"@{handle} status={status}{extra}; profile-link edit needs supported app flow (INSTAGRAM_PASSWORD → session_bootstrap)"


def probe_tiktok() -> str:
    rc, out = sh(
        ["docker", "exec", "-w", "/app", "social-api",
         "python3", "scripts/tiktok_tool.py", "accounts"],
        timeout=60,
    )
    active = re.search(r"status=active", out) is not None
    adb = "adb device online" if adb_online() else "no adb device (emulator offline only)"
    return (
        f"OAuth {'ACTIVE' if active else 'MISSING'}; pending-share check: "
        f"check-pending.py (0 drafts as of 2026-11-07); profile-website edit "
        f"needs TikTok app — {adb}"
    )


def main() -> int:
    as_json = "--json" in sys.argv
    issues = gh_issues()
    gates = [
        (786, "WhatsApp phone verify", probe_whatsapp,
         "wa_verify_daemon.py (unattended) once WABA assigned"),
        (785, "Meta business verification", probe_meta,
         "Meta console evidence upload → leads_retrieval App Review"),
        (788, "Instagram profile funnel link", probe_instagram,
         "INSTAGRAM_PASSWORD in .env → session_bootstrap.py"),
        (789, "TikTok profile website", probe_tiktok,
         "tiktok_profile_edit.py via ADB on S22+ (needs USB device)"),
        (793, "Partner shortlist approval", lambda: "CRM shortlist live (espocrm-ops)",
         "reply 'approve' or recipient ids on the issue"),
        (794, "LinkedIn newsletter nurture", lambda: "dry-run cohort via linkedin tooling",
         "li_at re-auth first, then reply 'approve'"),
        (795, "AI-governance offer decision", lambda: "positioning draft on the issue",
         "reply 'approve'"),
    ]
    rows = []
    for num, name, probe, residual in gates:
        state = "closed" if num not in issues else "open"
        live = probe() if state == "open" else "—"
        rows.append({
            "issue": num, "gate": name, "state": state,
            "live": live, "residual": residual if state == "open" else "",
        })
    if as_json:
        print(json.dumps(rows, indent=2))
        return 0
    for r in rows:
        mark = "OPEN " if r["state"] == "open" else "done "
        print(f"#{r['issue']:<4} {mark} {r['gate']}")
        if r["state"] == "open":
            print(f"      live: {r['live']}")
            print(f"      → {r['residual']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
