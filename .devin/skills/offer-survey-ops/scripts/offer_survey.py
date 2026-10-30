#!/usr/bin/env python3
"""Offer-validation survey for the Cloudless SMB signature offer.

Creates and operates a `kind:"survey"` email campaign via the SocialAuto API.
Each recipient gets a hosted per-recipient form at /s/{token}
submissions
land in `survey_responses` and write back to EspoCRM Notes when the recipient
matches a CRM entity.

Usage:
    python3 offer_survey.py questions                 # dump questions JSON
    python3 offer_survey.py draft                     # create draft campaign
    python3 offer_survey.py audience <id> [--espocrm] [--email a@b ...]
    python3 offer_survey.py responses <id>            # aggregate results
    python3 offer_survey.py send <id> --yes           # real send (explicit)

Safety:
- `send` requires the literal `--yes` flag (Code of Honor #2 — human approves).
- `draft` is idempotent per day: an existing draft with today's name is
  reused, not duplicated.
- Refuses cleartext http:// for non-localhost APIs and any redirect that
  changes origin — the Authorization header never leaves the original origin.

Reads SOCIAL_ADMIN_EMAIL / SOCIAL_ADMIN_PASSWORD from repo .env.
API base: SOCIAL_API_URL or http://127.0.0.1:8083
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
API = os.environ.get("SOCIAL_API_URL", "http://127.0.0.1:8083")
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}

CAMPAIGN_NAME = f"SMB offer validation ({date.today().isoformat()})"
SUBJECT = "Who do you call when it breaks?"
TEXT_BODY = """{{first_name}}

Quick one — you've got a website someone built, an IT person you call
sometimes, and a brand that grew however it grew. I'm asking every business
owner I know a few honest questions about how that's actually working — and
I'd rather hear it from you than guess.

Two minutes, seven questions, no pitch on the other side. What I send back
is a written summary of what owners told me — nothing else.

CTA: Answer the 7 questions | {{survey_url}}

Themistoklis
Cloudless"""

QUESTIONS: list[dict] = [
    {
        "id": "it_support",
        "label": "When something IT-related breaks in your business, who do you call today — and how fast does it actually get fixed?",
        "type": "text",
        "required": True,
    },
    {
        "id": "sells_online",
        "label": "Do you currently sell online? If not — what has stopped you so far?",
        "type": "choice",
        "options": [
            "Yes, we sell online",
            "No — cost",
            "No — too complex",
            "No — don't know who to trust",
            "Tried it before and it failed",
        ],
        "required": True,
    },
    {
        "id": "vendor_count",
        "label": "How many different vendors touch your digital presence today (site, e-shop, branding, IT, email)?",
        "type": "choice",
        "options": ["1 — one partner", "2", "3 or more", "I handle it myself"],
        "required": True,
    },
    {
        "id": "embarrassment",
        "label": "What's the one thing about your online presence that embarrasses you in front of customers?",
        "type": "text",
    },
    {
        "id": "monthly_value",
        "label": "If one reliable partner handled all of it — site, e-shop, brand, IT support — what would that be worth per month?",
        "type": "choice",
        "options": ["Under €100", "€100–300", "€300–600", "€600+", "Nothing — not interested"],
        "required": True,
    },
    {
        "id": "hesitation",
        "label": "What would make you hesitate before paying a partner like that upfront?",
        "type": "text",
    },
    {
        "id": "audit_interest",
        "label": "Would you take a free 30-minute audit of your current setup, with a written list of what's actually broken?",
        "type": "choice",
        "options": ["Yes", "No"],
        "required": True,
    },
]


def _origin(url: str) -> tuple:
    p = urllib.parse.urlparse(url)
    return (p.scheme, p.hostname, p.port or (443 if p.scheme == "https" else 80))


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if _origin(newurl) != _origin(req.full_url):
            raise urllib.error.HTTPError(
                newurl, code, f"Refused cross-origin redirect to {newurl}",
                headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


OPENER = urllib.request.build_opener(SafeRedirect())


def check_api_url() -> None:
    p = urllib.parse.urlparse(API)
    if p.scheme not in {"http", "https"}:
        raise SystemExit(f"SOCIAL_API_URL must be http(s): {API}")
    if p.scheme == "http" and p.hostname not in LOCAL_HOSTS:
        raise SystemExit(
            f"Refusing cleartext http:// for non-localhost API: {API}. "
            "Use https:// or run against the local stack."
        )


def load_env() -> tuple[str, str]:
    env = {}
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env["SOCIAL_ADMIN_EMAIL"], env["SOCIAL_ADMIN_PASSWORD"]


def req(path: str, token: str, body: dict | None = None, method: str = "POST"):
    headers = {"Authorization": f"Bearer {token}"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    r = urllib.request.Request(f"{API}{path}", data=data, headers=headers, method=method)
    raw = OPENER.open(r, timeout=90).read()
    return json.loads(raw) if raw else {}


def login() -> str:
    email, password = load_env()
    r = urllib.request.Request(
        f"{API}/api/v1/auth/login",
        data=urllib.parse.urlencode({"username": email, "password": password}).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    return json.loads(OPENER.open(r, timeout=30).read())["access_token"]


def cmd_draft(token: str) -> None:
    for c in req("/api/v1/email-campaigns?limit=200", token, method="GET"):
        if c.get("name") == CAMPAIGN_NAME and c.get("status") == "draft":
            print(json.dumps({"reused": True, "id": c["id"], "name": CAMPAIGN_NAME}))
            return
    c = req("/api/v1/email-campaigns", token, body={
        "name": CAMPAIGN_NAME,
        "kind": "survey",
        "subject": SUBJECT,
        "text_body": TEXT_BODY,
        "questions": QUESTIONS,
    })
    print(json.dumps({"reused": False, "id": c.get("id"), "name": CAMPAIGN_NAME}))


def cmd_audience(token: str, cid: str, espocrm: bool, emails: list[str]) -> None:
    body = {
        "emails": [{"email": e} for e in emails],
        "include_espocrm": espocrm,
    }
    print(json.dumps(req(f"/api/v1/email-campaigns/{cid}/audience", token, body=body)))


def cmd_responses(token: str, cid: str) -> None:
    rows = req(f"/api/v1/email-campaigns/{cid}/responses", token, method="GET")
    if isinstance(rows, dict):
        rows = rows.get("responses", rows.get("items", []))
    counts: dict[str, dict[str, int]] = {}
    texts: dict[str, list[str]] = {}
    audit_yes = []
    for r in rows:
        answers = r.get("answers") or {}
        if answers.get("audit_interest") == "Yes":
            audit_yes.append(r.get("email") or "(token recipient)")
        for q in QUESTIONS:
            v = answers.get(q["id"])
            if v is None:
                continue
            if q["type"] == "choice":
                counts.setdefault(q["id"], {}).setdefault(str(v), 0)
                counts[q["id"]][str(v)] += 1
            else:
                texts.setdefault(q["id"], []).append(str(v))
    print(f"responses: {len(rows)}")
    for q in QUESTIONS:
        print(f"\n[{q['id']}] {q['label'][:80]}")
        if q["id"] in counts:
            for opt, n in sorted(counts[q["id"]].items(), key=lambda kv: -kv[1]):
                print(f"  {n:>3}  {opt}")
        for t in texts.get(q["id"], []):
            print(f"  • {t[:200]}")
    print(f"\nQ7 audit=Yes follow-ups ({len(audit_yes)}):")
    for e in audit_yes:
        print(f"  → {e}")


_JUNK_DOMAINS = {"messenger.local", "test.dev", "example.com", "localhost"}
_OWNER_ADDRS = {"tbaltzakis@cloudless.gr", "themis.baltzakis@gmail.com",
                "baltzakis.themis@gmail.com", "admin@example.com"}


def _classify(lead: dict, seen: set[str]) -> tuple[str | None, str | None]:
    e = (lead.get("email") or "").strip().lower()
    if not e or "@" not in e:
        return None, "no email"
    dom = e.rsplit("@", 1)[1]
    if dom in _JUNK_DOMAINS or dom.endswith(".local") or dom.endswith(".dev"):
        return None, f"synthetic domain @{dom}"
    if e in _OWNER_ADDRS:
        return None, "owner/test mailbox"
    if e.startswith("probe") or e.startswith("test"):
        return None, "probe/test address"
    if e in seen:
        return None, "duplicate"
    seen.add(e)
    return e, None


def cmd_suggest(token: str) -> None:
    """List CRM leads, classify each mailable/junk, emit paste-ready flags."""
    leads: list[dict] = []
    off = 0
    while True:
        r = req(f"/api/v1/email-campaigns/crm/clients?limit=100&offset={off}",
                token, method="GET")
        batch = (r.get("leads") or []) + (r.get("contacts") or [])
        leads += batch
        if len(batch) < 100:
            break
        off += 100
    good, junk = [], []
    seen: set[str] = set()
    for l in leads:
        e, why = _classify(l, seen)
        (good if e else junk).append((e or l.get("email") or "-", why or l.get("name", "")))
    print(f"leads: {len(leads)}  mailable: {len(good)}  filtered: {len(junk)}")
    print("\nMAILABLE:")
    for e, name in good:
        print(f"  {e:<45} {name}")
    print("\nFILTERED:")
    for e, why in junk:
        print(f"  {e:<45} {why}")
    print("\npaste-ready:")
    print("  " + " ".join(f"--email={e}" for e, _ in good))


def cmd_nudge(token: str, cid: str, yes: bool) -> None:
    """Re-queue non-responders and (with --yes) resend the survey to them."""
    out = req(f"/api/v1/email-campaigns/{cid}/nudge", token, body={})
    if yes:
        out["send"] = req(f"/api/v1/email-campaigns/{cid}/send", token, body={})
    else:
        out["note"] = "pass --yes to actually resend"
    print(json.dumps(out))


def cmd_readout(token: str, cid: str, yes: bool) -> None:
    """Draft a read-out campaign to responders: aggregate summary as body,
    responder emails as audience. --yes sends it (human gate)."""
    rows = req(f"/api/v1/email-campaigns/{cid}/responses", token, method="GET")
    if isinstance(rows, dict):
        rows = rows.get("responses", rows.get("items", []))
    emails = sorted({r.get("email") for r in rows if r.get("email")})
    if not emails:
        raise SystemExit("no responders yet — nothing to send a read-out to")
    counts: dict[str, dict[str, int]] = {}
    for r in rows:
        for k, v in (r.get("answers") or {}).items():
            counts.setdefault(k, {}).setdefault(str(v), 0)
            counts[k][str(v)] += 1
    audit = sum(1 for r in rows if (r.get("answers") or {}).get("audit_interest") == "Yes")

    # Human-friendly narrative — no raw question text, no counts-in-parens.
    def _top(qid, default="—"):
        qc = counts.get(qid) or {}
        return max(qc.items(), key=lambda kv: kv[1])[0] if qc else default

    price = _top("monthly_value")
    pain = _top("embarrassment", "their online presence")
    vendors = _top("vendor_count", "more than one")
    pain_text = pain.lower() if pain != "—" else "an online presence that understates the real quality of the work"
    paragraphs = [
        f"A while back I asked {len(rows)} business owners a few short questions "
        f"about how they handle their digital presence. Who fixes things when they "
        f"break, how many vendors they juggle, what they would pay for one "
        f"reliable partner.",
        "Here's what came back:\n\n"
        f"• Vendor sprawl — most run on {vendors} different vendors: a freelancer "
        f"here, an agency there, a computer guy who doesn't talk to the others.\n\n"
        f"• The real pain — {pain_text}.\n\n"
        f"• What it's worth — the answers cluster around {price} a month for one "
        f"partner covering the site, the e-shop, the brand and the IT.",
        "The pattern is the same every time: it's not a technology problem, it's "
        "a coordination problem. Nobody owns the whole picture, so the owner ends "
        "up managing vendors instead of running the business.",
    ]
    if audit:
        paragraphs.append(
            f"{audit} of {len(rows)} also asked for the free 30-minute audit, so "
            f"I'm booking those now. If you'd like one too, just reply to this email."
        )
    paragraphs.append("That's it. No pitch in this one — thanks for answering.")
    body = "\n\n".join(paragraphs)
    c = req("/api/v1/email-campaigns", token, body={
        "name": f"SMB survey read-out ({date.today().isoformat()})",
        "kind": "marketing",
        "subject": "What you told me — the honest version",
        "text_body": "Hi {{first_name}},\n\n" + body + "\n\n— Themis, Cloudless",
    })
    rid = c.get("id")
    req(f"/api/v1/email-campaigns/{rid}/audience", token,
        body={"emails": [{"email": e} for e in emails], "include_espocrm": False})
    out = {"draft_id": rid, "responders": len(emails), "audit_yes": audit}
    if yes:
        out["send"] = req(f"/api/v1/email-campaigns/{rid}/send", token, body={})
    else:
        out["note"] = f"review the draft then: offer_survey.py send {rid} --yes"
    print(json.dumps(out))


def cmd_send(token: str, cid: str, yes: bool) -> None:
    if not yes:
        raise SystemExit("send requires --yes (Code of Honor #2: human approves sends)")
    print(json.dumps(req(f"/api/v1/email-campaigns/{cid}/send", token, body={})))


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] == "questions":
        print(json.dumps(QUESTIONS, indent=2, ensure_ascii=False))
        return
    check_api_url()
    token = login()
    cmd = args[0]
    if cmd == "draft":
        cmd_draft(token)
    elif cmd == "audience" and len(args) >= 2:
        emails = [a.split("=", 1)[1] for a in args[2:] if a.startswith("--email=")]
        cmd_audience(token, args[1], "--espocrm" in args, emails)
    elif cmd == "suggest":
        cmd_suggest(token)
    elif cmd == "responses" and len(args) >= 2:
        cmd_responses(token, args[1])
    elif cmd == "nudge" and len(args) >= 2:
        cmd_nudge(token, args[1], "--yes" in args)
    elif cmd == "readout" and len(args) >= 2:
        cmd_readout(token, args[1], "--yes" in args)
    elif cmd == "send" and len(args) >= 2:
        cmd_send(token, args[1], "--yes" in args)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
