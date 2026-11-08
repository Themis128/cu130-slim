"""Health check for gov-portal-watch deployments.

`python doctor.py [config.json ...]` — default: every watches/*.json beside
this script's deployment plus portal.json in cwd if present.

Checks per config: file readable, creds file exists (login=gsis), CDP :9229
reachable, portal tab present + auth_regex match on live DOM, state file
writable, Telegram token present. `--tg` also sends a live test message.
Exit 0 = all pass."""
import glob, json, os, re, sys, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
KEYS = "C:/Users/tbaltzakis/AppData/Roaming/devin/secrets"
CDP = "http://localhost:9229"

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""))


def configs_from_args():
    if len(sys.argv) > 1 and sys.argv[1] != "--tg":
        return sys.argv[1:]
    paths = sorted(glob.glob(os.path.join(HERE, "watches", "*.json")))
    local = os.path.join(os.getcwd(), "portal.json")
    if os.path.exists(local):
        paths.append(local)
    if not paths:
        paths = [os.path.join(HERE, "watches", "idika.json")]
    return paths


def main():
    tg_test = "--tg" in sys.argv
    import engine

    check("telegram token", os.path.exists(KEYS + "/tg-bot-token"))
    if tg_test:
        engine.tg("gov-portal-watch doctor: live test message.")
        check("telegram send", True, "test message dispatched")

    try:
        engine.ensure_browser()
        check("cdp :9229", True)
    except Exception as e:
        check("cdp :9229", False, str(e)[:120])
        _report()
        return 1

    for path in configs_from_args():
        name = os.path.basename(path)
        try:
            cfg = engine.load_config(path)
        except Exception as e:
            check(f"config {name}", False, str(e)[:120])
            continue
        check(f"config {name}", True, cfg.get("name", "?"))

        if cfg.get("login") == "gsis":
            creds = cfg.get("creds")
            ok = bool(creds) and os.path.exists(creds)
            check(f"  creds {name}", ok, creds or "not set")

        sf = cfg["state_file"]
        try:
            open(sf, "a", encoding="utf-8").close()
            check(f"  state writable {name}", True, sf)
        except Exception as e:
            check(f"  state writable {name}", False, str(e)[:80])

        host = re.sub(r"^https?://", "", cfg["url"]).split("/")[0]
        t = engine.find_tab(host)
        check(f"  portal tab {name}", t is not None, host)
        if t is not None:
            tab = engine.Tab(t["webSocketDebuggerUrl"])
            try:
                raw = tab.eval(cfg.get("auth_expr") or engine.AUTH_EXPR)
                authed = raw == "auth"
            finally:
                tab.close()
            check(f"  session auth {name}", authed, str(raw))

    _report()
    return 0 if all(ok for _, ok, _ in results) else 1


def _report():
    bad = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(bad)}/{len(results)} checks passed" +
          (f" — FAILED: {', '.join(bad)}" if bad else ""))


if __name__ == "__main__":
    sys.exit(main())
