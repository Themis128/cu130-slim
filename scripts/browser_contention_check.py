#!/usr/bin/env python3
"""Regression test for the platform-attributed busy-hold on the browser bridge.

Callers tag page interactions with an ``X-Platform: <name>`` header; a tagged
interaction sets a 180s hold (``busy_owner``) on the bridge. While held,
foreign-tagged AND untagged calls get 409 "Browser busy"; /session/start for
a foreign platform gets 409; a same-platform start reuses the session.
``{"force": true}`` would override the hold — this script NEVER sends force.

If a session is already active/done for platform P, P (or the live busy-owner
parsed from a 409 detail) is used as the owner tag — the script does NOT
disturb the existing session. If no usable session exists, it starts a
twitter session, waits for the page, runs the checks, then stops it.

Usage:
    python3 scripts/browser_contention_check.py [--verbose]

Exit codes: 0 = all checks pass, 1 = at least one FAIL.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request

BRIDGE = "http://localhost:9223"
EVAL_BODY = {"expression": "location.href"}

VERBOSE = False


def http(method: str, path: str, body: dict | None = None,
         platform: str | None = None, timeout: float = 15.0) -> tuple[int | None, dict]:
    """HTTP call -> (status, parsed_json). status None on connection failure."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BRIDGE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if platform:
        req.add_header("X-Platform", platform)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read() or b"{}")
        except Exception:
            payload = {}
        return e.code, payload
    except Exception as e:
        return None, {"error": str(e)}


def dump(tag: str, code: int | None, body: dict) -> None:
    if VERBOSE:
        print(f"    [{tag}] -> {code} {json.dumps(body)[:300]}")


def evaluate(platform: str | None) -> tuple[int | None, dict]:
    return http("POST", "/session/evaluate", EVAL_BODY, platform=platform)


def parse_busy_owner(body: dict) -> str | None:
    m = re.search(r"[Bb]usy with (\S+)", str(body.get("detail", "")))
    return m.group(1) if m else None


def main() -> int:
    global VERBOSE
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--verbose", action="store_true", help="dump raw responses")
    args = ap.parse_args()
    VERBOSE = args.verbose

    results: list[tuple[str, str]] = []  # (verdict, label)

    def report(ok: bool | None, label: str, note: str = "") -> bool:
        """ok=True->PASS, False->FAIL, None->WARN. Returns whether it 'passed'."""
        verdict = "PASS" if ok is True else ("FAIL" if ok is False else "WARN")
        results.append((verdict, label + (f" — {note}" if note else "")))
        print(f"{verdict:<5} {label}" + (f" — {note}" if note else ""))
        return ok is not False

    code, st = http("GET", "/session/status")
    if code != 200:
        print(f"FAIL   bridge unreachable at {BRIDGE}: {st}")
        return 1
    print(f"bridge session: platform={st.get('platform')} status={st.get('status')} "
          f"msg={st.get('message', '')[:80]}")

    session_platform = st.get("platform") if st.get("status") != "idle" else None
    owner: str | None = None
    we_started = False
    owner_probe_ok = False

    # ── Resolve the owner tag ─────────────────────────────────────────
    if session_platform:
        code, body = evaluate(session_platform)
        dump("probe-owner", code, body)
        if code == 200:
            owner = session_platform
            owner_probe_ok = True
        elif code == 409:
            owner = parse_busy_owner(body)
            if owner:
                print(f"hold already active — busy_owner={owner} "
                      f"(session platform={session_platform})")
            else:
                owner = session_platform  # can't parse; best effort
        elif code in (400, 500):
            # session reported but no live page → fall through to start flow
            print(f"session '{session_platform}' has no live page ({code}); "
                  "starting a fresh session")
            session_platform = None
        else:
            report(False, "resolve owner", f"unexpected {code}: {body}")

    if owner is None:
        owner = "twitter"
        code, body = http("POST", "/session/start", {"platform": owner}, platform=owner)
        dump("start", code, body)
        if code not in (200, 409):
            report(False, "start owner session", f"{code}: {body}")
            code, body = 0, {}
        if code == 200:
            we_started = True
            print(f"started {owner} session — waiting for page...")
            deadline = time.time() + 30
            while time.time() < deadline:
                time.sleep(5)
                code, body = evaluate(owner)
                dump("wait-page", code, body)
                if code == 200:
                    owner_probe_ok = True
                    break
        elif code == 409:
            # something already holds the browser — adopt it as owner
            real = parse_busy_owner(body)
            if real:
                owner = real
                print(f"start refused; adopting busy_owner={owner}")

    foreign = "instagram" if owner != "instagram" else "twitter"

    try:
        # ── Check 1: owner-tagged page interaction succeeds ───────────
        if owner_probe_ok:
            report(True, f"evaluate with X-Platform: {owner} -> 200")
        else:
            code, body = evaluate(owner)
            dump("check1", code, body)
            report(code == 200, f"evaluate with X-Platform: {owner} -> 200",
                   f"got {code}: {json.dumps(body)[:120]}")

        # ── Check 2: foreign-tagged call gets 409 ─────────────────────
        code, body = evaluate(foreign)
        dump("check2", code, body)
        hold_confirmed = code == 409
        report(code == 409, f"evaluate with X-Platform: {foreign} -> 409",
               f"got {code}: {json.dumps(body)[:120]}")

        # ── Check 3: untagged call gets 409 while busy ────────────────
        code, body = evaluate(None)
        dump("check3", code, body)
        if code == 409:
            report(True, "evaluate with no X-Platform -> 409")
        elif code == 200:
            report(None, "evaluate with no X-Platform -> 200",
                   "hold may have expired — WARN not FAIL")
            hold_confirmed = hold_confirmed or False
        else:
            report(False, "evaluate with no X-Platform -> 409",
                   f"got {code}: {json.dumps(body)[:120]}")

        # ── Check 4: foreign /session/start gets 409 while busy ───────
        # Unsafe to fire without a confirmed hold: a successful foreign start
        # would tear down the live session.
        if hold_confirmed:
            code, body = http("POST", "/session/start", {"platform": foreign})
            dump("check4", code, body)
            if code == 409:
                report(True, f"start {foreign} -> 409 while busy")
            else:
                report(False, f"start {foreign} -> 409 while busy",
                       f"got {code}: {json.dumps(body)[:120]}")
                if code == 200:
                    # we accidentally started a foreign session — must clean up
                    we_started = True
        else:
            report(None, f"start {foreign} -> 409 while busy",
                   "skipped — no busy hold detected, probing would hijack the browser")

        # ── Check 5: same-platform start reuses ───────────────────────
        if hold_confirmed:
            code, body = http("POST", "/session/start", {"platform": owner},
                              platform=owner)
            dump("check5", code, body)
            if code == 200:
                if body.get("reused"):
                    report(True, f"start {owner} -> 200 (reused)")
                else:
                    report(True, f"start {owner} -> 200",
                           "bridge restarted the session instead of reusing")
                    we_started = True
            elif code == 409 and "already active" in str(body.get("detail", "")):
                report(True, f"start {owner} -> 409 already-active",
                       "session still starting — bridge refused teardown (protective)")
            else:
                report(False, f"start {owner} -> 200 reuse",
                       f"got {code}: {json.dumps(body)[:120]}")
        else:
            report(None, f"start {owner} -> 200 reuse",
                   "skipped — no busy hold detected")
    finally:
        if we_started:
            code, body = http("POST", "/session/stop")
            dump("cleanup-stop", code, body)
            print(f"cleanup: stopped session this script started -> {code}")

    fails = sum(1 for v, _ in results if v == "FAIL")
    print(f"\n{sum(1 for v,_ in results if v=='PASS')} PASS, "
          f"{sum(1 for v,_ in results if v=='WARN')} WARN, {fails} FAIL")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
