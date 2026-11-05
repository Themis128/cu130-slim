#!/usr/bin/env python3
"""li_scope_probe.py — probe which OAuth scopes a LinkedIn app can request.

Builds https://www.linkedin.com/oauth/v2/authorization URLs and classifies the
response WITHOUT needing a login or the client secret:

  - LinkedIn rejects unapproved scopes immediately at the authorize step:
    either a redirect to <redirect_uri>?error=unauthorized_scope_error or an
    error page (error=unauthorized_scope_error in body/location).
  - Approved scopes render the consent screen (or the login wall) — i.e. the
    request is grantable. This needs NO member session to determine.

Usage:
  li_scope_probe.py --client-id XXXX --scopes w_member_social r_ads ...
  li_scope_probe.py --client-id XXXX --probe-each --scopes a b c   (one URL per scope)
  li_scope_probe.py --client-id XXXX --file scopes.txt             (one per line)

Redirect URI defaults to the app's own callback (any registered URI works).
Exit code 0 always; JSON result on stdout.
"""
import argparse, json, sys, urllib.parse, urllib.request

AUTHZ = "https://www.linkedin.com/oauth/v2/authorization"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def probe_one(client_id: str, redirect_uri: str, scopes: list[str]) -> dict:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": "probe",
        "scope": " ".join(scopes),
    }
    url = AUTHZ + "?" + urllib.parse.urlencode(params)

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = urllib.request.build_opener(NoRedirect)
    req = urllib.request.Request(url, headers=UA)
    try:
        resp = opener.open(req, timeout=30)
        status, loc, body = resp.status, resp.headers.get("Location", ""), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        status, loc, body = e.code, e.headers.get("Location", "") if e.headers else "", e.read().decode("utf-8", "replace") if e.fp else ""
    except Exception as e:
        return {"scopes": scopes, "result": "error", "detail": str(e)}

    hay = (loc + " " + body).lower()
    if "unauthorized_scope_error" in hay:
        return {"scopes": scopes, "result": "rejected", "detail": "unauthorized_scope_error", "status": status}
    if "error=" in urllib.parse.unquote(loc).lower():
        return {"scopes": scopes, "result": "rejected", "detail": loc[:200], "status": status}
    # consent page or login wall -> grantable
    kind = "consent" if "oauth/v2/authorization" in str(resp.geturl() if 'resp' in dir() else url) else "ok"
    return {"scopes": scopes, "result": "grantable", "status": status,
            "location": loc[:200], "final": (resp.geturl() if 'resp' in dir() else "")[:200]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client-id", required=True)
    ap.add_argument("--redirect-uri", default="https://social.cloudless.gr/api/v1/auth/oauth/linkedin/callback")
    ap.add_argument("--scopes", nargs="*", default=[])
    ap.add_argument("--file")
    ap.add_argument("--probe-each", action="store_true")
    a = ap.parse_args()
    scopes = list(a.scopes)
    if a.file:
        scopes += [l.strip() for l in open(a.file) if l.strip() and not l.startswith("#")]
    if not scopes:
        ap.error("no scopes given")

    out = []
    if a.probe_each:
        for s in scopes:
            out.append(probe_one(a.client_id, a.redirect_uri, [s]))
    else:
        out.append(probe_one(a.client_id, a.redirect_uri, scopes))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
