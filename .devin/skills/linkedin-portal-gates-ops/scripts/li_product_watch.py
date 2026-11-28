#!/usr/bin/env python3
"""li_product_watch.py — diff LinkedIn dev-portal product states per app.

The grant lanes in li_grant_watch.py cover products with a probeable REST
surface (Matched Audiences, Member DP). Products like Live Events have no
API probe — the portal product card is the only signal. This watcher
reads every product card on each app's /products page (same extraction as
li_product_state.py) and TG-alerts #ops on ANY state transition, in both
directions (DENIED→REVIEW/added = appeal resolved; added→DENIED = new
denial).

    li_product_watch.py           # probe + diff + alert
    li_product_watch.py --json    # machine-readable report, no side effects
    li_product_watch.py --status  # show last recorded state

Transport reuses member_dp_probe's lanes: linkedin-browser-sidecar
/debug/navigate + /debug/eval first (wakes the idle-slept sidecar via
stack-ops), Windows CDP :9229/:9222 fallback. Skips cleanly when both are
down — never blocks the cron lane.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import member_dp_probe as mdp  # noqa: E402

REPO = Path("/home/tbaltzakis/cu130-slim")
STATE_DIR = Path.home() / ".local/state/li-product-watch"
STATE_FILE = STATE_DIR / "state.json"
TG = REPO / ".devin/skills/telegram-alert-ops/scripts/tg_alert.py"

APPS = ["265077368", "264925843", "265077464", "265081467", "227354605"]
PRODUCTS_URL = "https://www.linkedin.com/developers/apps/{app}/products"

# Card-text -> state, same classification as li_product_state.py.
RANK = {"DENIED": 0, "REQ": 1, "FORM": 1, "REVIEW": 2, "added": 3}

EVAL_CARDS = r"""
JSON.stringify([...document.querySelectorAll('[class*=product],[class*=card]')]
  .map(c=>(c.innerText||'').replace(/\s+/g,' ').trim())
  .filter(t=>t.length>20&&t.length<500))
"""


def _classify(txt: str) -> str:
    t = txt.lower()
    if "access denied" in t:
        return "DENIED"
    if "review in progress" in t or "review" in t:
        return "REVIEW"
    if "access request form" in t:
        return "FORM"
    if "request access" in t:
        return "REQ"
    return "added"


_NON_PRODUCT = ("access was denied", "error", "something went wrong",
                "page not found", "404")


def _product_map(cards: list[str]) -> dict[str, str]:
    """Dedupe nested card texts by head, keep the strongest state seen."""
    out: dict[str, str] = {}
    for card in cards:
        head = card.split("  ")[0][:40].strip()
        if not head or head.lower().startswith(_NON_PRODUCT):
            continue
        state = _classify(card)
        if head not in out or RANK[state] > RANK[out[head]]:
            out[head] = state
    return out


def _wake_sidecar():
    if not mdp.STACKCTL or not Path(mdp.STACKCTL).exists():
        return
    try:
        subprocess.run(
            [sys.executable, mdp.STACKCTL, "wake", "linkedin-browser-sidecar"],
            timeout=150, capture_output=True)
    except Exception:
        pass


def _probe_app_sidecar(app: str) -> dict:
    base = mdp.SIDECAR_BASE
    h = mdp._sc_req("GET", "/health", timeout=15)
    if h.get("error"):
        _wake_sidecar()
        h = mdp._sc_req("GET", "/health", timeout=15)
    if h.get("error"):
        return {"status": "no-sidecar", "detail": h["error"]}
    if h.get("rate_limited") or h.get("circuit_open"):
        return {"status": "sidecar-rate-limited"}
    nav = mdp._sc_req("POST", "/debug/navigate",
                      {"url": PRODUCTS_URL.format(app=app)}, timeout=60)
    if nav.get("error"):
        return {"status": "navigate-error", "detail": str(nav["error"])[:160]}
    time.sleep(9)
    val, r = mdp._sc_eval(EVAL_CARDS)
    if val is None:
        return {"status": "eval-error", "detail": str(r)[:160]}
    if isinstance(val, str):
        try:
            val = json.loads(val)
        except Exception:
            pass
    cards = val if isinstance(val, list) else []
    return {"via": "sidecar", "products": _product_map(cards),
            "raw_count": len(cards)}


def _probe_apps_cdp(apps: list[str]) -> dict[str, dict]:
    """One tab, sequential Page.navigate — gentler than open/close per app."""
    base = mdp._cdp_base()
    if not base:
        return {a: {"status": "no-cdp"} for a in apps}
    tab = mdp._open_tab(base, PRODUCTS_URL.format(app=apps[0]))
    tab_id = tab.get("id", "")
    out: dict[str, dict] = {}
    c = mdp.CDP(tab["webSocketDebuggerUrl"])
    try:
        time.sleep(9)  # let the first page load
        for i, app in enumerate(apps):
            if i:
                c.cmd("Page.navigate",
                      {"url": PRODUCTS_URL.format(app=app)})
                time.sleep(10)
            try:
                raw = c.eval(EVAL_CARDS)
                cards = json.loads(raw) if raw else []
                out[app] = {"via": "cdp",
                            "products": _product_map(cards or []),
                            "raw_count": len(cards or [])}
            except Exception as e:
                out[app] = {"status": "eval-error",
                            "detail": str(e)[:160]}
    finally:
        c.close()
        mdp._close_tab(base, tab_id)
    return out


def collect(via: str = "auto") -> dict:
    state = {"ts": int(time.time()), "apps": {}}
    if via == "cdp":
        state["apps"] = _probe_apps_cdp(APPS)
        return state
    failed: list[str] = []
    for app in APPS:
        res = _probe_app_sidecar(app)
        if res.get("status") in ("no-sidecar", "navigate-error",
                                 "eval-error", "sidecar-rate-limited"):
            failed.append(app)
            res = {"status": res["status"]}
        state["apps"][app] = res
        time.sleep(2)  # be polite to the sidecar rate limiter
    if via == "auto" and failed:
        for app, res in _probe_apps_cdp(failed).items():
            if res.get("products") is not None or "status" not in res:
                res["sidecar_fallback"] = state["apps"][app].get("status")
                state["apps"][app] = res
    return state


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def _save_state(state: dict):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=1))


def diff_alerts(old: dict, new: dict) -> list[str]:
    alerts = []
    old_apps = (old or {}).get("apps") or {}
    for app, res in (new.get("apps") or {}).items():
        prods = res.get("products") or {}
        if not prods:
            continue
        old_prods = ((old_apps.get(app) or {}).get("products")) or {}
        for name, st in prods.items():
            prev = old_prods.get(name)
            if prev == st:
                continue
            arrow = f"{prev or '?'} -> {st}"
            if prev is None:
                alerts.append(f"LinkedIn app {app}: product '{name[:38]}' "
                              f"first seen as {st}")
            elif RANK.get(st, -1) > RANK.get(prev, -1):
                alerts.append(f"LinkedIn app {app}: '{name[:38]}' improved "
                              f"{arrow} — grant may be live")
            else:
                alerts.append(f"LinkedIn app {app}: '{name[:38]}' regressed "
                              f"{arrow} — check portal")
    return alerts


def tg_alert(text: str):
    try:
        subprocess.run([sys.executable, str(TG), "ops", text],
                       timeout=30, capture_output=True)
    except Exception:
        pass


def main():
    args = set(sys.argv[1:])
    if "--status" in args:
        print(json.dumps(_load_state(), indent=1)[:4000])
        return 0
    via = "auto"
    if "--via" in sys.argv:
        i = sys.argv.index("--via")
        via = sys.argv[i + 1] if i + 1 < len(sys.argv) else "auto"
    state = collect(via=via)
    if "--json" in args:
        print(json.dumps(state, indent=1))
        return 0
    old = _load_state()
    _save_state(state)
    alerts = diff_alerts(old, state)
    for a in alerts:
        print("ALERT:", a)
        tg_alert(a)
    ok = sum(1 for r in state["apps"].values() if r.get("products"))
    print(f"apps probed: {ok}/{len(APPS)}  transitions: {len(alerts)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
