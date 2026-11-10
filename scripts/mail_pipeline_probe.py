#!/usr/bin/env python3
"""mail_pipeline_probe.py — synthetic end-to-end mail pipeline check.

Sends a uniquely-tokened probe through the real outbound path and verifies
it lands in the dovecot mailbox, covering every hop in one shot:

    mail-outbound /send  ->  CF Email Sending (send_email binding)
        ->  Email Routing  ->  mail-ingest worker
        ->  webmail.cloudless.gr/ingest  ->  dovecot IMAP INBOX

Stages:
  1. send    POST {MAIL_OUTBOUND_URL} with Bearer {MAIL_OUTBOUND_TOKEN}
  2. cf-graphql  (best-effort) emailSendingAdaptive/emailRoutingAdaptive
             event status via CF GraphQL — logged, never fails the probe
  3. imap    poll OMV_IMAP_* dovecot for the probe subject token

On failure alerts Telegram #ops via tg_alert.py (severity "ops").

Env (env var first, then repo-root .env — same convention as
cf_mail_confirm_watch.py):
  MAIL_OUTBOUND_URL, MAIL_OUTBOUND_TOKEN   — worker lane
  OMV_IMAP_HOST / OMV_IMAP_USER / OMV_IMAP_PASS — dovecot mailbox
  MAIL_PROBE_TO                            — recipient (default tbaltzakis@cloudless.gr)
  CLOUDFLARE_API_TOKEN, CLOUDFLARE_ZONE_ID  — optional GraphQL leg
  TELEGRAM_BOT_TOKEN, TG_CH_*              — via tg_alert.py

Usage:
  mail_pipeline_probe.py                 # probe + alert on failure
  mail_pipeline_probe.py --no-alert      # probe only (tests/cron dry runs)
  mail_pipeline_probe.py --timeout 240   # IMAP wait budget (default 180s)
  mail_pipeline_probe.py --json          # machine-readable result

Exit 0 = probe email delivered to dovecot INBOX; 1 = a stage failed.
"""

from __future__ import annotations

import argparse
import imaplib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

REPO_ROOT = Path(os.environ.get("MAIL_REPO",
                                Path(__file__).resolve().parents[1]))
TG_ALERT = REPO_ROOT / ".devin" / "skills" / "telegram-alert-ops" / "scripts" / "tg_alert.py"

CF_GRAPHQL = "https://api.cloudflare.com/client/v4/graphql"

# Cloudflare Bot Fight Mode blocks python-urllib's default UA (error 1010)
# on workers.dev routes — send a browser signature like the stack's httpx
# clients effectively get away with.
_UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}


def _env(key: str, default: str = "") -> str:
    if val := os.environ.get(key):
        return val.strip()
    try:
        for line in (REPO_ROOT / ".env").read_text().splitlines():
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return default


def _http_json(method: str, url: str, body: dict | None, headers: dict,
               timeout: int = 30) -> tuple[int, dict | str]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json",
                                          **_UA, **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            try:
                return r.status, json.loads(raw)
            except json.JSONDecodeError:
                return r.status, raw[:300].decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:300].decode("utf-8", "replace")


def stage_send(url: str, token: str, to: str, subject: str,
               text: str) -> tuple[bool, str]:
    status, resp = _http_json(
        "POST", url,
        {"to": to, "subject": subject, "text": text,
         "from": "Cloudless <no-reply@cloudless.gr>", "category": "ops-alert"},
        {"Authorization": f"Bearer {token}"})
    if status == 200 and isinstance(resp, dict) and resp.get("ok"):
        return True, "accepted"
    return False, f"HTTP {status}: {str(resp)[:160]}"


def stage_graphql(account_tag: str, token: str) -> str:
    """Best-effort CF analytics check. Returns a short status string; never raises.

    emailSendingAdaptive is an ACCOUNT-scoped dataset — the deploy token
    has account analytics read even though it lacks zone analytics.
    """
    query = """
    query($accountTag: string, $filter: EmailSendingAdaptiveFilter_InputObject) {
      viewer { accounts(filter: {accountTag: $accountTag}) {
        emailSendingAdaptive(filter: $filter, limit: 5, orderBy: [datetime_DESC]) {
          datetime status subject messageId errorDetail
        }
      } }
    }"""
    body = {"query": query, "variables": {
        "accountTag": account_tag,
        "filter": {"datetime_geq": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 900))}}}
    status, resp = _http_json("POST", CF_GRAPHQL, body,
                              {"Authorization": f"Bearer {token}"})
    if status != 200 or not isinstance(resp, dict):
        return f"graphql HTTP {status}"
    if resp.get("errors"):
        return f"graphql error: {str(resp['errors'][0].get('message'))[:100]}"
    events = (((resp.get("data") or {}).get("viewer") or {})
              .get("accounts") or [{}])[0].get("emailSendingAdaptive") or []
    if not events:
        return "no sending events in last 15m"
    latest = events[0]
    return f"latest={latest.get('status')} @ {latest.get('datetime')}"


def stage_imap(host: str, user: str, password: str, subject_token: str,
               timeout_s: int, interval_s: int = 10) -> tuple[bool, str]:
    deadline = time.time() + timeout_s
    last_err = ""
    while time.time() < deadline:
        try:
            m = imaplib.IMAP4_SSL(host, 993)
            m.login(user, password)
            m.select("INBOX")
            _, data = m.search(None, "SUBJECT", f'"{subject_token}"')
            ids = data[0].split() if data and data[0] else []
            if ids:
                m.logout()
                return True, f"delivered after ~{int(timeout_s - (deadline - time.time()))}s"
            m.logout()
        except Exception as e:  # noqa: BLE001 — transient IMAP/network errors retry
            last_err = str(e)[:120]
        time.sleep(interval_s)
    return False, f"not found in {timeout_s}s ({last_err or 'no match'})"


def alert(text: str) -> str:
    if not TG_ALERT.exists():
        return "tg_alert.py missing"
    import subprocess
    r = subprocess.run([sys.executable, str(TG_ALERT), "ops", text],
                       capture_output=True, text=True, timeout=30)
    return (r.stdout or r.stderr).strip()[:120] or "alert sent"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-alert", action="store_true", help="don't Telegram on failure")
    ap.add_argument("--timeout", type=int, default=180, help="IMAP wait seconds")
    ap.add_argument("--json", action="store_true", help="JSON result on stdout")
    args = ap.parse_args()

    out_url = _env("MAIL_OUTBOUND_URL")
    out_tok = _env("MAIL_OUTBOUND_TOKEN")
    imap_host = _env("OMV_IMAP_HOST")
    imap_user = _env("OMV_IMAP_USER")
    imap_pass = _env("OMV_IMAP_PASS") or _env("MAIL_TBALTZAKIS_PASSWORD")
    to_addr = _env("MAIL_PROBE_TO", "tbaltzakis@cloudless.gr")

    missing = [k for k, v in {
        "MAIL_OUTBOUND_URL": out_url, "MAIL_OUTBOUND_TOKEN": out_tok,
        "OMV_IMAP_HOST": imap_host, "OMV_IMAP_USER": imap_user,
        "OMV_IMAP_PASS|MAIL_TBALTZAKIS_PASSWORD": imap_pass}.items() if not v]
    if missing:
        msg = f"MAIL PIPELINE FAIL — probe misconfigured, missing: {', '.join(missing)}"
        print(msg)
        if not args.no_alert:
            print("alert:", alert(msg))
        return 1

    token = uuid.uuid4().hex[:8]
    subject = f"mailprobe-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{token}"
    stages: list[dict] = []

    ok, detail = stage_send(out_url, out_tok, to_addr, subject,
                            f"Synthetic mail pipeline probe {subject}. "
                            f"If you see this in dovecot INBOX the full chain is live.")
    stages.append({"stage": "send", "ok": ok, "detail": detail})
    if not ok:
        return _finish(stages, args, f"mail-outbound /send threw: {detail}")

    # Best-effort CF analytics leg — never fails the probe. Prefers the
    # Workers-scoped deploy token (has account analytics read); falls back
    # to the plain API token.
    cf_tok = _env("CLOUDFLARE_WORKERS_DEPLOY_TOKEN") or _env("CLOUDFLARE_API_TOKEN")
    acct = _env("CLOUDFLARE_ACCOUNT_ID")
    if cf_tok and acct:
        stages.append({"stage": "cf-graphql", "ok": True,
                       "detail": stage_graphql(acct, cf_tok)})

    ok, detail = stage_imap(imap_host, imap_user, imap_pass,
                            subject, args.timeout)
    stages.append({"stage": "imap", "ok": ok, "detail": detail})
    if not ok:
        return _finish(stages, args,
                       f"probe sent OK but never reached dovecot INBOX: {detail}")

    return _finish(stages, args, None)


def _finish(stages: list[dict], args: argparse.Namespace,
            failure: str | None) -> int:
    result = {"ok": failure is None, "stages": stages}
    if args.json:
        print(json.dumps(result))
    else:
        for s in stages:
            print(f"  [{'ok' if s['ok'] else 'FAIL'}] {s['stage']}: {s['detail']}")
        print("MAIL PIPELINE", "OK" if failure is None else f"FAIL — {failure}")
    if failure:
        if not args.no_alert:
            print("alert:", alert(f"MAIL PIPELINE FAIL\n\n{failure}\n\n"
                                  f"Probe: mail-outbound -> CF Email Sending -> "
                                  f"Email Routing -> mail-ingest -> "
                                  f"webmail.cloudless.gr/ingest -> dovecot"))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
