#!/usr/bin/env python3
"""li_products_matrix.py — LinkedIn product -> scope -> app grantability matrix.

Combines the doc-sourced product map (Microsoft Learn "Getting Access" /
"Increasing Access" tables, verified 2026-11) with live `li_scope_probe`
results for each app's client_id, so "which scopes can this app actually
consent today" is answered with data, not docs.

Usage:
  li_products_matrix.py                          # static doc matrix only
  li_products_matrix.py --app 227354605=77tf4oysp8u3fz --app 265077368=77fd3pui3m2jwm
  li_products_matrix.py --json

Client IDs are NOT secrets — they appear in authorize URLs. Probing is a
single unauthenticated GET per scope against linkedin.com.
"""
from __future__ import annotations
import argparse
import json
import urllib.parse
import urllib.request

AUTHZ = "https://www.linkedin.com/oauth/v2/authorization"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# --- doc-sourced matrix (learn.microsoft.com/linkedin, verified 2026-11) -----
# availability: "self-serve" | "application" (review) | "restricted" | "closed"
PRODUCTS = {
    "Sign In with LinkedIn (OIDC)": {
        "availability": "self-serve",
        "scopes": ["openid", "profile", "email"],
    },
    "Share on LinkedIn": {
        "availability": "self-serve",
        "scopes": ["w_member_social"],
    },
    "Community Management API": {
        "availability": "application (Dev -> Standard tier, 12-month dev window)",
        "scopes": [
            "w_member_social", "w_member_social_feed",
            "w_organization_social", "w_organization_social_feed",
            "r_organization_social", "r_organization_social_feed",
            "rw_organization_admin", "r_organization_admin",
            "r_organization_followers", "r_basicprofile",
            "r_member_profileAnalytics", "r_member_postAnalytics",
            "r_1st_connections_size",
        ],
        "notes": "member-analytics + feed scopes listed under CM on current "
                 "API versions; older grants may lack them -> support ticket.",
    },
    "Advertising API": {
        "availability": "application (Dev -> Standard tier)",
        "scopes": [
            "r_ads", "rw_ads", "r_ads_reporting",
            "r_organization_admin", "r_organization_social",
            "rw_organization_admin", "w_organization_social",
            "w_member_social", "r_basicprofile", "r_1st_connections_size",
        ],
    },
    "Lead Sync API": {
        "availability": "application",
        "scopes": ["r_marketing_leadgen_automation", "r_ads", "r_events",
                   "r_organization_admin"],
    },
    "Conversions API": {
        "availability": "application",
        "scopes": ["rw_conversions", "r_ads"],
    },
    "Events Management API": {
        "availability": "self-serve on request",
        "scopes": ["r_events", "rw_events"],
    },
    "Matched Audiences API": {
        "availability": "application",
        "scopes": ["rw_dmp_segments"],
    },
    "Member Data Portability (Member)": {
        "availability": "in-portal request + business-email verify",
        "scopes": ["r_dma_portability_self_serve"],
        "notes": "member's own-data snapshot; the 'dedicated Company page' "
                 "gate applies to portability-bound apps at creation.",
    },
    "Pages Data Portability": {
        "availability": "access form",
        "scopes": ["r_dma_admin_pages_content", "r_dma_admin_pages_list"],
    },
}

ALL_SCOPES = sorted({s for p in PRODUCTS.values() for s in p["scopes"]})


def probe(client_id: str, redirect_uri: str, scopes: list[str]) -> str:
    params = {"response_type": "code", "client_id": client_id,
              "redirect_uri": redirect_uri, "state": "probe",
              "scope": " ".join(scopes)}
    url = AUTHZ + "?" + urllib.parse.urlencode(params)

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    try:
        resp = urllib.request.build_opener(NoRedirect).open(
            urllib.request.Request(url, headers=UA), timeout=30)
        loc = resp.headers.get("Location", "")
        body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location", "") if e.headers else ""
        body = e.read().decode("utf-8", "replace") if e.fp else ""
    except Exception as e:
        return f"error:{e}"
    if "unauthorized_scope_error" in (loc + " " + body).lower():
        return "rejected"
    return "grantable"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", action="append", default=[],
                    help="appid=client_id, repeatable")
    ap.add_argument("--redirect-uri",
                    default="https://social.cloudless.gr/api/v1/auth/oauth/linkedin/callback")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    apps = {}
    for spec in args.app:
        appid, cid = spec.split("=", 1)
        apps[appid] = cid

    grant = {}  # scope -> {appid: result}
    if apps:
        for appid, cid in apps.items():
            for s in ALL_SCOPES:
                grant.setdefault(s, {})[appid] = probe(cid, args.redirect_uri, [s])

    if args.json:
        print(json.dumps({"apps": apps, "grant": grant,
                          "products": PRODUCTS}, indent=2))
        return

    header = f"{'scope':32s} {'product':42s}" + "".join(f" {a:>12s}" for a in apps)
    print(header)
    print("-" * len(header))
    prod_of = {}
    for pname, p in PRODUCTS.items():
        for s in p["scopes"]:
            prod_of.setdefault(s, []).append(pname)
    for s in ALL_SCOPES:
        prods = ",".join(sorted(set(prod_of.get(s, ["-"]))))[:41]
        row = f"{s:32s} {prods:42s}"
        for a in apps:
            row += f" {grant[s][a]:>12s}"
        print(row)
    print()
    for pname, p in PRODUCTS.items():
        print(f"* {pname}: {p['availability']}")
        if p.get("notes"):
            print(f"    note: {p['notes']}")


if __name__ == "__main__":
    main()
