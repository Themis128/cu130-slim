#!/usr/bin/env python3
"""cf_access_audit.py — Cloudflare Access (Zero Trust) gate ops for cloudless.gr.

    cf_access_audit.py probe <host> [host...]   # is each endpoint Access-gated?
    cf_access_audit.py scan                     # probe all known tunnel hosts
    cf_access_audit.py orgs                     # what can CLOUDFLARE_API_TOKEN see?
    cf_access_audit.py policy <host>            # emit bypass-policy JSON to apply
"""
import argparse
import json
import os
import re
import sys
import urllib.request

HOSTS = ["ntfy", "kuma", "grafana", "meili", "docs", "espocrm", "n8n",
         "postiz", "omv", "webmail", "ftp", "manage", "pi-origin",
         "appflowy", "logs", "social", "cloud", "signal"]
APEX = "cloudless.gr"
ACCT = "fb7dc7b69b662480cd5961a4d1913c78"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def gated(host: str) -> tuple[bool, str]:
    url = host if host.startswith("http") else f"https://{host}/"
    try:
        req = urllib.request.Request(url, method="GET",
                                     headers={"User-Agent": "cf-access-audit/1.0"})
        _OPENER.open(req, timeout=10)
        return False, "open"
    except urllib.error.HTTPError as e:
        auth_hdr = e.headers.get("Www-Authenticate") or ""
        loc = e.headers.get("Location") or ""
        if auth_hdr.startswith("Cloudflare-Access") or "cloudflareaccess" in loc:
            return True, f"Access-gated (HTTP {e.code} -> SSO)"
        return False, f"open (HTTP {e.code})"
    except Exception as e:
        return False, f"unreachable ({type(e).__name__})"


def api_token() -> str:
    env_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env")
    for cand in (env_path, ".env"):
        if os.path.isfile(cand):
            for line in open(cand, encoding="utf-8", errors="replace"):
                if line.startswith("CLOUDFLARE_API_TOKEN=") and "=" in line:
                    v = line.split("=", 1)[1].strip()
                    if v:
                        return v
    # container env is the live source of truth for this stack
    try:
        out = subprocess.check_output(
            ["docker", "exec", "social-api", "printenv", "CLOUDFLARE_API_TOKEN"],
            text=True, timeout=10).strip()
        return out
    except Exception:
        return ""


def orgs() -> int:
    import subprocess  # noqa: PLC0415
    tok = api_token()
    if not tok:
        print("no CLOUDFLARE_API_TOKEN found (.env or social-api env)")
        return 1
    for path in (f"accounts/{ACCT}/access/apps?per_page=50",
                 "accounts?per_page=10", "zones?name=cloudless.gr"):
        req = urllib.request.Request(
            f"https://api.cloudflare.com/client/v4/{path}",
            headers={"Authorization": f"Bearer {tok}"})
        try:
            r = json.load(urllib.request.urlopen(req, timeout=20))
            res = r.get("result") or []
            print(f"{path.split('?')[0]:50s} {len(res)} item(s)")
            for it in res[:10]:
                print("   -", it.get("name") or it.get("domain") or it.get("id"))
        except urllib.error.HTTPError as e:
            print(f"{path.split('?')[0]:50s} HTTP {e.code} (perm-masked)")
    print("\nNote: the ZT org 'cloudless-gr.cloudflareaccess.com' lives on a")
    print("different CF account — zero apps here means perm-masked, not absent.")
    return 0


def policy(host: str) -> int:
    pol = {
        "name": f"bypass-{host} machine access",
        "decision": "bypass",
        "include": [{"everyone": {}}],
        "comment": ("Machine-client endpoints (ntfy app / watchdog / uptime) "
                    "can't complete Access SSO. Keep ACLs on the origin."),
    }
    print(json.dumps(pol, indent=2))
    print(f"\nApply in Zero Trust -> Access -> Applications -> <app covering {host}>")
    print("-> Policies -> Add policy, or POST /accounts/{acct}/access/apps/{app}/policies")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("probe"); pr.add_argument("hosts", nargs="+")
    sub.add_parser("scan")
    sub.add_parser("orgs")
    po = sub.add_parser("policy"); po.add_argument("host")
    a = ap.parse_args()

    if a.cmd == "probe":
        bad = 0
        for h in a.hosts:
            g, msg = gated(h if "." in h else f"{h}.{APEX}")
            print(f"{'GATED' if g else ' ok  '} {h:30s} {msg}")
            bad += g
        return 1 if bad else 0
    if a.cmd == "scan":
        bad = 0
        for h in HOSTS:
            g, msg = gated(f"{h}.{APEX}")
            print(f"{'GATED' if g else ' ok  '} {h:12s} {msg}")
            bad += g
        print(f"\n{bad}/{len(HOSTS)} hosts Access-gated")
        return 0
    if a.cmd == "orgs":
        return orgs()
    if a.cmd == "policy":
        return policy(a.host)
    return 0


if __name__ == "__main__":
    import subprocess  # noqa: F401  (used inside api_token)
    sys.exit(main())
