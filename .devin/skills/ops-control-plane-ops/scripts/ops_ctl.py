#!/usr/bin/env python3
"""ops_ctl — SocialAuto ops control-plane CLI (ops_watchers + ops_services).

Two auth lanes:
  management  → admin JWT (SOCIAL_ADMIN_EMAIL/PASSWORD via skill_http)
  agent       → X-Service-Token (OPS_WATCHER_AGENT_TOKEN)

Usage:
  ops_ctl.py status [--json]
  ops_ctl.py watchers list [--tier T] [--executor E] [--enabled on|off] [--kind K] [--json]
  ops_ctl.py watchers show NAME
  ops_ctl.py watchers toggle NAME on|off
  ops_ctl.py watchers run NAME
  ops_ctl.py watchers due [--json]                       # agent
  ops_ctl.py watchers result NAME [--exit-code N] [--output-tail S] [--observed] [--duration S]  # agent
  ops_ctl.py watchers sync --file payload.json           # agent
  ops_ctl.py services list [--tier T] [--status S] [--enabled on|off] [--json]
  ops_ctl.py services show NAME
  ops_ctl.py services action NAME ACTION [--reason R]    # ACTION: restart|start|stop|logs|status
  ops_ctl.py services pending [--json]                   # agent
  ops_ctl.py services report --file reports.json         # agent
  ops_ctl.py services action-result NAME --ok|--fail [--output S]  # agent
  ops_ctl.py services sync --file payload.json           # agent
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import env, request, social_api  # noqa: E402

API = env("SOCIAL_API_URL", "http://127.0.0.1:8083").rstrip("/")
AGENT_TOKEN = env("OPS_WATCHER_AGENT_TOKEN")


def _admin() -> tuple[str, str]:
    return social_api()


def _agent_headers() -> dict:
    if not AGENT_TOKEN:
        print("OPS_WATCHER_AGENT_TOKEN not set (env or .env)", file=sys.stderr)
        sys.exit(2)
    return {"X-Service-Token": AGENT_TOKEN}


def _mgmt(method: str, path: str, body: dict | None = None):
    api, token = _admin()
    return request(method, f"{api}{path}", token=token, data=body)


def _agent(method: str, path: str, body: dict | None = None):
    return request(method, f"{API}/api/v1{path}", headers=_agent_headers(), data=body)


def _emit(obj, as_json: bool) -> None:
    if as_json:
        print(json.dumps(obj, indent=2, default=str))
    elif isinstance(obj, dict) and "watchers" in obj:
        for w in obj["watchers"]:
            print(
                f"{w['name']:<48} {w['tier']:<9} {w['executor']:<11} "
                f"{w['status']:<9} en={str(w['enabled']):<5} "
                f"fails={w.get('consecutive_failures', 0)}"
            )
        print(f"-- {obj['count']} watcher(s)")
    elif isinstance(obj, dict) and "services" in obj:
        for s in obj["services"]:
            pend = f" PENDING:{s['pending_action']}" if s.get("pending_action") else ""
            print(
                f"{s['name']:<48} {s['tier']:<9} {s.get('health', '?'):<10} "
                f"{s['status']:<10} en={str(s['enabled']):<5}{pend}"
            )
        print(f"-- {obj['count']} service(s)")
    else:
        print(json.dumps(obj, indent=2, default=str))


def _load(path: str) -> dict:
    return json.loads(Path(path).read_text())


# ── status dashboard ─────────────────────────────────────────────────────


def cmd_status(args) -> None:
    api, token = _admin()
    w = request("GET", f"{api}/api/v1/ops/watchers", token=token)
    s = request("GET", f"{api}/api/v1/ops/services", token=token)
    ws, ss = w["watchers"], s["services"]

    def tally(key, rows):
        out = {}
        for r in rows:
            out[r[key]] = out.get(r[key], 0) + 1
        return out

    dashboard = {
        "watchers": {
            "total": len(ws),
            "by_executor": tally("executor", ws),
            "by_status": tally("status", ws),
            "disabled": sum(1 for x in ws if not x["enabled"]),
        },
        "services": {
            "total": len(ss),
            "by_tier": tally("tier", ss),
            "by_status": tally("status", ss),
            "pending_actions": [x["name"] for x in ss if x.get("pending_action")],
            "disabled": sum(1 for x in ss if not x["enabled"]),
        },
    }
    if args.json:
        print(json.dumps(dashboard, indent=2))
        return
    print("== ops_watchers ==")
    for k, v in dashboard["watchers"]["by_executor"].items():
        print(f"  executor {k:<12} {v}")
    for k, v in dashboard["watchers"]["by_status"].items():
        print(f"  status   {k:<12} {v}")
    print(f"  disabled {dashboard['watchers']['disabled']}")
    print("== ops_services ==")
    for k, v in dashboard["services"]["by_tier"].items():
        print(f"  tier     {k:<12} {v}")
    for k, v in dashboard["services"]["by_status"].items():
        print(f"  status   {k:<12} {v}")
    pa = dashboard["services"]["pending_actions"]
    print(f"  pending  {len(pa)}" + (f" → {', '.join(pa[:5])}" if pa else ""))
    print(f"  disabled {dashboard['services']['disabled']}")


# ── watchers ─────────────────────────────────────────────────────────────


def cmd_watchers_list(args) -> None:
    q = []
    if args.tier:
        q.append(f"tier={args.tier}")
    if args.executor:
        q.append(f"executor={args.executor}")
    if args.kind:
        q.append(f"kind={args.kind}")
    if args.enabled is not None:
        q.append(f"enabled={str(args.enabled).lower()}")
    suffix = ("?" + "&".join(q)) if q else ""
    api, token = _admin()
    out = request("GET", f"{api}/api/v1/ops/watchers{suffix}", token=token)
    if args.json:
        print(json.dumps(out, indent=2, default=str))
    else:
        _emit(out, False)


def cmd_watchers_show(args) -> None:
    api, token = _admin()
    out = request("GET", f"{api}/api/v1/ops/watchers", token=token)
    for w in out["watchers"]:
        if w["name"] == args.name:
            print(json.dumps(w, indent=2, default=str))
            return
    print(f"watcher not found: {args.name}", file=sys.stderr)
    sys.exit(1)


def cmd_watchers_toggle(args) -> None:
    out = _mgmt("POST", f"/api/v1/ops/watchers/{args.name}/toggle", {"enabled": args.state == "on"})
    print(f"{out['name']}: enabled={out['enabled']} status={out['status']}")


def cmd_watchers_run(args) -> None:
    out = _mgmt("POST", f"/api/v1/ops/watchers/{args.name}/run")
    print(f"{out['name']}: dispatched={out.get('dispatched', '?')} status={out['status']}")


def cmd_watchers_due(args) -> None:
    _emit(_agent("GET", "/ops/watchers/due"), args.json)


def cmd_watchers_result(args) -> None:
    body = {
        "exit_code": args.exit_code,
        "output_tail": args.output_tail,
        "duration_seconds": args.duration,
        "observed": args.observed,
    }
    out = _agent("POST", f"/ops/watchers/{args.name}/result", body)
    print(f"{args.name}: {out}")


def cmd_watchers_sync(args) -> None:
    out = _agent("POST", "/ops/watchers/sync", _load(args.file))
    print(out)


# ── services ─────────────────────────────────────────────────────────────


def cmd_services_list(args) -> None:
    q = []
    if args.tier:
        q.append(f"tier={args.tier}")
    if args.status:
        q.append(f"status={args.status}")
    suffix = ("?" + "&".join(q)) if q else ""
    api, token = _admin()
    out = request("GET", f"{api}/api/v1/ops/services{suffix}", token=token)
    if args.json:
        print(json.dumps(out, indent=2, default=str))
    else:
        _emit(out, False)


def cmd_services_show(args) -> None:
    api, token = _admin()
    out = request("GET", f"{api}/api/v1/ops/services", token=token)
    for s in out["services"]:
        if s["name"] == args.name:
            print(json.dumps(s, indent=2, default=str))
            return
    print(f"service not found: {args.name}", file=sys.stderr)
    sys.exit(1)


def cmd_services_action(args) -> None:
    out = _mgmt(
        "POST",
        f"/api/v1/ops/services/{args.name}/action",
        {"action": args.action, "reason": args.reason},
    )
    print(f"{out['name']}: queued={out.get('queued')} allowed-check passed")


def cmd_services_pending(args) -> None:
    _emit(_agent("GET", "/ops/services/actions"), args.json)


def cmd_services_report(args) -> None:
    out = _agent("POST", "/ops/services/report", _load(args.file))
    print(out)


def cmd_services_action_result(args) -> None:
    out = _agent(
        "POST",
        f"/ops/services/{args.name}/action-result",
        {"ok": args.result == "ok", "output_tail": args.output},
    )
    print(f"{args.name}: {out}")


def cmd_services_sync(args) -> None:
    out = _agent("POST", "/ops/services/sync", _load(args.file))
    print(out)


# ── argparse ─────────────────────────────────────────────────────────────


def main() -> int:
    p = argparse.ArgumentParser(prog="ops_ctl", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("status")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_status)

    w = sub.add_parser("watchers")
    wsub = w.add_subparsers(dest="sub", required=True)
    wl = wsub.add_parser("list")
    wl.add_argument("--tier")
    wl.add_argument("--executor")
    wl.add_argument("--kind")
    wl.add_argument("--enabled", choices=["on", "off"])
    wl.add_argument("--json", action="store_true")
    wl.set_defaults(fn=lambda a: cmd_watchers_list(_BoolArgs(a)))
    ws = wsub.add_parser("show")
    ws.add_argument("name")
    ws.set_defaults(fn=cmd_watchers_show)
    wt = wsub.add_parser("toggle")
    wt.add_argument("name")
    wt.add_argument("state", choices=["on", "off"])
    wt.set_defaults(fn=cmd_watchers_toggle)
    wr = wsub.add_parser("run")
    wr.add_argument("name")
    wr.set_defaults(fn=cmd_watchers_run)
    wd = wsub.add_parser("due")
    wd.add_argument("--json", action="store_true")
    wd.set_defaults(fn=cmd_watchers_due)
    wo = wsub.add_parser("result")
    wo.add_argument("name")
    wo.add_argument("--exit-code", type=int, default=0)
    wo.add_argument("--output-tail", default="")
    wo.add_argument("--observed", action="store_true")
    wo.add_argument("--duration", type=float, default=0.0)
    wo.set_defaults(fn=cmd_watchers_result)
    wy = wsub.add_parser("sync")
    wy.add_argument("--file", required=True)
    wy.set_defaults(fn=cmd_watchers_sync)

    sv = sub.add_parser("services")
    ssub = sv.add_subparsers(dest="sub", required=True)
    sl = ssub.add_parser("list")
    sl.add_argument("--tier")
    sl.add_argument("--status")
    sl.add_argument("--json", action="store_true")
    sl.set_defaults(fn=cmd_services_list)
    sh = ssub.add_parser("show")
    sh.add_argument("name")
    sh.set_defaults(fn=cmd_services_show)
    sa = ssub.add_parser("action")
    sa.add_argument("name")
    sa.add_argument("action", choices=["restart", "start", "stop", "logs", "status"])
    sa.add_argument("--reason", default="")
    sa.set_defaults(fn=cmd_services_action)
    sp = ssub.add_parser("pending")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_services_pending)
    sr = ssub.add_parser("report")
    sr.add_argument("--file", required=True)
    sr.set_defaults(fn=cmd_services_report)
    sx = ssub.add_parser("action-result")
    sx.add_argument("name")
    sx.add_argument("result", choices=["ok", "fail"])
    sx.add_argument("--output", default="")
    sx.set_defaults(fn=cmd_services_action_result)
    sy = ssub.add_parser("sync")
    sy.add_argument("--file", required=True)
    sy.set_defaults(fn=cmd_services_sync)

    args = p.parse_args()
    args.fn(args)
    return 0


class _BoolArgs:
    """Adapt argparse namespace: 'on'/'off' → True/False/None for filters."""

    def __init__(self, ns):
        self.__dict__.update(vars(ns))
        self.enabled = {"on": True, "off": False}.get(getattr(ns, "enabled", None))


if __name__ == "__main__":
    sys.exit(main())
