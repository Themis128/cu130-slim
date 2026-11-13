#!/usr/bin/env python3
"""reply-watch.py — alert on new inbound mail from the case authorities.

Runs every ~15 min (spawned by admin-followups.js). Checks TWO inbound
lanes because the case correspondence lives in both:

  1. cloudless.gr IMAP (omv-ha dovecot) — replies to the noreply@ SMTP
     follow-ups sent by admin-followups.js
  2. Gmail API (baltzakis.themis@gmail.com, gmail.modify token) — the
     address registered on the Ombudsman complaint; authority replies
     and OTP/notification mail land here

Watched domains: synigoros.gr, dypa.gov.gr, hli.gov.gr
(overridable via reply-watch.json {"domains": [...], "since": "YYYY-MM-DD"};
a domain may be {"name": ..., "require": "<subject regex>"} to limit
alerts to case-related subjects — e.g. dypa.gov.gr also sends unrelated
program mail)

State: reply-watch-state.json {"seen": ["imap:<dom>:<uid>" | "gmail:<msgid>", ...]}
"""
import email, importlib.util, imaplib, json, os, re, sys, urllib.parse, urllib.request
from email.header import decode_header

DIR = os.path.dirname(os.path.abspath(__file__))
ENV = os.path.normpath(os.path.join(DIR, "..", "..", ".env"))
STATE = os.path.join(DIR, "reply-watch-state.json")
# Canonical watch config lives in backend/scripts/synigoros_case (mounted
# into the SocialAuto workers); fall back to a legacy local copy.
_SCHED = os.path.normpath(os.path.join(
    DIR, "..", "..", "social-automation", "backend", "scripts", "synigoros_case"))
CONF = os.path.join(_SCHED, "reply-watch.json") \
    if os.path.exists(os.path.join(_SCHED, "reply-watch.json")) \
    else os.path.join(DIR, "reply-watch.json")
SECRETS = os.path.join(os.environ.get("APPDATA", ""), "devin", "secrets")
GSR = (r"C:\Users\tbaltzakis\AppData\Roaming\devin\skills"
       r"\gmail-send-ops\scripts\gmail-schedule-reply.py")

DEFAULT_DOMAINS = ["synigoros.gr", "dypa.gov.gr", "hli.gov.gr"]
DEFAULT_SINCE = "2026-10-26"  # complaint filing date
MAX_SEEN = 5000
MAX_ALERTS_PER_RUN = 10
# automated noise we don't want to page about (OTP codes, login alerts…)
NOISE = re.compile(r"(κωδικ|οτπ|otp|επαληθευ|ταυτοποίηση|επιβεβαίωση σύνδεσης|"
                   r"password|κωδικός|2fa|dump|bot|has new sign-in|"
                   r"new sign-in|security alert|φόρμα αποστολής|υπενθύμιση|"
                   r"ενημέρωση|επιτυχής)", re.I)
# anything from a no-reply mailbox is automation, not a human response —
# the monitor's status poll already covers automated status notifications
AUTO_SENDER = re.compile(r"(noreply|no-?reply|donotreply|no_reply)", re.I)


def env_val(key):
    try:
        for line in open(ENV, encoding="utf-8", errors="replace"):
            m = re.match(r"^" + re.escape(key) + r'=(.*)$', line.strip())
            if m:
                return m.group(1).strip().strip('"').strip("'")
    except OSError:
        pass
    return None


def dec(s):
    if not s:
        return ""
    return "".join(
        p.decode(c or "utf-8", errors="replace") if isinstance(p, bytes) else p
        for p, c in decode_header(s))


def tg(text):
    try:
        bot = open(os.path.join(SECRETS, "tg-bot-token")).read().strip()
        cfg = json.load(open(os.path.join(DIR, "config.json"), encoding="utf-8"))
        data = urllib.parse.urlencode({"chat_id": cfg["chatId"], "text": text}).encode()
        urllib.request.urlopen(
            "https://api.telegram.org/bot%s/sendMessage" % bot,
            data=data, timeout=15)
    except Exception as e:
        print("tg err", e, file=sys.stderr)


def imap_since(since):
    mon = {1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
           7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec"}
    y, m, d = (int(x) for x in since.split("-"))
    return "%02d-%s-%04d" % (d, mon[m], y)


def gmail_since(since):
    return since.replace("-", "/")


def parse_domains(raw):
    """-> [(name, require_regex_or_None)]"""
    out = []
    for d in raw or []:
        if isinstance(d, str):
            out.append((d, None))
        elif isinstance(d, dict) and d.get("name"):
            out.append((d["name"], d.get("require")))
    return out


def check_imap(domains, since, seen, fresh):
    user = env_val("IMAP_USER") or env_val("SMTP_USER") or "tbaltzakis@cloudless.gr"
    pw = env_val("IMAP_PASSWORD") or env_val("SMTP_PASSWORD")
    host = env_val("IMAP_HOST") or "192.168.1.130"
    port = int(env_val("IMAP_PORT") or 993)
    if not pw:
        print("imap: no mailbox password", file=sys.stderr)
        return
    mail = imaplib.IMAP4_SSL(host, port)
    mail.login(user, pw)
    mail.select("INBOX")
    for dom, req_re in domains:
        req = re.compile(req_re, re.I) if req_re else None
        _, data = mail.uid("SEARCH", None, "FROM", '"%s"' % dom,
                           "SINCE", imap_since(since))
        uids = data[0].split() if data and data[0] else []
        for uid in uids:
            key = "imap:%s:%s" % (dom, uid.decode())
            if key in seen:
                continue
            seen.add(key)
            _, msg = mail.uid("FETCH", uid,
                              "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)]"
                              " BODY.PEEK[TEXT]<0.1200>)")
            # response is a list of (meta, literal) tuples — one per section
            header_blob, body_blob = "", ""
            for part in (msg or []):
                if not (isinstance(part, tuple) and len(part) > 1):
                    continue
                meta = part[0].decode("utf-8", "replace") if isinstance(part[0], bytes) else str(part[0])
                lit = part[1]
                if isinstance(lit, bytes):
                    text = lit.decode("utf-8", "replace")
                else:
                    continue
                if "HEADER" in meta.upper():
                    header_blob = text
                else:
                    body_blob = text
            frm = re.search(r"^From:\s*(.+)$", header_blob, re.M)
            sub = re.search(r"^Subject:\s*(.+)$", header_blob, re.M)
            date = re.search(r"^Date:\s*(.+)$", header_blob, re.M)
            snippet = re.sub(r"<[^>]+>", " ", body_blob)
            snippet = re.sub(r"\s+", " ", snippet)[:160].strip()
            subj = dec(sub.group(1).strip()) if sub else ""
            if req and not req.search(subj):
                continue
            fresh.append({
                "lane": "cloudless", "key": key,
                "from": dec(frm.group(1).strip()) if frm else "",
                "subject": subj,
                "snippet": snippet,
                "date": dec(date.group(1).strip()) if date else "",
                "auto": bool(AUTO_SENDER.search(
                    dec(frm.group(1).strip()) if frm else "")),
            })
    mail.logout()


def check_gmail(domains, since, seen, fresh):
    try:
        spec = importlib.util.spec_from_file_location("gsr", GSR)
        gsr = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gsr)
    except Exception as e:
        print("gmail: loader failed: %s" % e, file=sys.stderr)
        return
    # Gmail can't OR per-domain require filters in one query cheaply —
    # query the union, then apply each domain's require to its matches.
    q = "(" + " OR ".join("from:%s" % d for d, _ in domains) + \
        ") after:%s" % gmail_since(since)
    try:
        res = gsr.api("GET", "messages?q=" + urllib.parse.quote(q) +
                      "&maxResults=100")
    except Exception as e:
        print("gmail: list failed: %s" % str(e)[:150], file=sys.stderr)
        return
    for m in res.get("messages", []):
        key = "gmail:%s" % m["id"]
        if key in seen:
            continue
        seen.add(key)
        try:
            full = gsr.api("GET", "messages/%s?format=metadata"
                           "&metadataHeaders=From&metadataHeaders=Subject"
                           "&metadataHeaders=Date" % m["id"])
        except Exception:
            continue
        headers = {h["name"].lower(): h["value"]
                   for h in full.get("payload", {}).get("headers", [])}
        frm = headers.get("from", "")
        subj = headers.get("subject", "")
        # figure out which watched domain this message belongs to and
        # apply that domain's require-regex on the subject
        low = (frm + " " + subj).lower()
        req = None
        for d, r in domains:
            if d.lower() in low:
                req = re.compile(r, re.I) if r else None
                break
        if req and not req.search(subj):
            continue
        fresh.append({
            "lane": "gmail", "key": key,
            "from": frm[:120],
            "subject": subj[:160],
            "snippet": (m.get("snippet") or "")[:160],
            "date": headers.get("date", "")[:60],
            "link": "https://mail.google.com/mail/u/0/#all/%s" % m.get("threadId", m["id"]),
            "auto": bool(AUTO_SENDER.search(frm)),
        })


def main():
    cfg = {}
    try:
        cfg = json.load(open(CONF, encoding="utf-8"))
    except Exception:
        pass
    domains = parse_domains(cfg.get("domains")) or \
        parse_domains(DEFAULT_DOMAINS)
    since = cfg.get("since") or DEFAULT_SINCE

    try:
        state = json.load(open(STATE, encoding="utf-8"))
    except Exception:
        state = {"seen": []}
    seen = set(state.get("seen", []))

    fresh = []
    try:
        check_imap(domains, since, seen, fresh)
    except Exception as e:
        print("imap lane error: %s" % e, file=sys.stderr)
    try:
        check_gmail(domains, since, seen, fresh)
    except Exception as e:
        print("gmail lane error: %s" % e, file=sys.stderr)

    state["seen"] = sorted(seen)[-MAX_SEEN:]
    try:
        json.dump(state, open(STATE, "w", encoding="utf-8"))
    except Exception as e:
        print("state write err", e, file=sys.stderr)

    interesting = [m for m in fresh
                   if not m.get("auto") and not NOISE.search(m["subject"] or "")]
    noise = len(fresh) - len(interesting)
    if noise:
        print("filtered %d automated/noise message(s)" % noise)

    if not interesting:
        print("reply-watch: no new mail")
        return

    shown = interesting[:MAX_ALERTS_PER_RUN]
    lines = ["📨 ΝΕΑ ΑΛΛΗΛΟΓΡΑΦΙΑ — %d μήνυμα(τα) από τις αρχές:" % len(interesting)]
    for m in shown:
        lines.append("• [%s] %s\n  %s — %s%s%s" % (
            m["lane"], m["subject"][:90], m["from"][:60], m["date"][:40],
            ("\n  " + m["snippet"]) if m.get("snippet") else "",
            ("\n  " + m["link"]) if m.get("link") else ""))
    if len(interesting) > MAX_ALERTS_PER_RUN:
        lines.append("… και %d ακόμη" % (len(interesting) - MAX_ALERTS_PER_RUN))
    tg("\n".join(lines))
    print("reply-watch: alerted %d message(s)" % len(interesting))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("reply-watch error: %s" % e, file=sys.stderr)
        sys.exit(0)  # never break the monitor spawn chain
