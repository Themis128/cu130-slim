#!/usr/bin/env python3
"""li_recap_watch.py — ingest LinkedIn weekly analytics-recap emails into
analytics_events so the strategy/analytics surfaces see real reach numbers
without anyone reading the mail.

LinkedIn sends "Themistoklis, your posts got N impressions last week"
weekly. This watcher:

  1. searches Gmail for the recap (from:notifications-noreply@linkedin.com,
     subject:impressions, newer_than:8d),
  2. dedups by Gmail message id (state file),
  3. fetches the full body, parses impressions + reactions + the
     "week of M/D/YYYY – M/D/YYYY" range + top audience lines,
  4. INSERTs one analytics_events row (event_type=weekly_analytics_recap,
     platform=linkedin, personal account) via docker exec psql — same
     row shape as a manual ingest.

Cron (host):  20 8 * * 1  python3 <repo>/.devin/skills/gmail-ops/scripts/li_recap_watch.py
(weekly, Monday morning — the recap lands Sunday/Monday)

State: .devin/skills/gmail-ops/state/li_recap_seen.json
Log:   stdout (cron captures)
"""
import base64
import json
import os
import re
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gmail  # noqa: E402  (gmail.py — OAuth helpers)

STATE = os.path.join(HERE, "..", "state", "li_recap_seen.json")
QUERY = ('from:notifications-noreply@linkedin.com subject:impressions '
         'newer_than:8d')
DOCKER = ("/mnt/c/Users/tbaltzakis/AppData/Local/Programs/DockerDesktop/"
          "resources/bin/docker.exe")
if not os.path.exists(DOCKER):
    DOCKER = "docker"

IMP_RE = re.compile(r"(\d[\d,]*)\s*impressions", re.I)
REACT_RE = re.compile(r"(\d[\d,]*)\s*reactions?", re.I)
WEEK_RE = re.compile(r"week of\s+(\d{1,2}/\d{1,2}/\d{4})\s*[–-]\s*"
                     r"(\d{1,2}/\d{1,2}/\d{4})", re.I)
AUDIENCE_RE = re.compile(r"(?:Industry|Location|Seniority)\s*([^\n|<]{3,60})")


def _messages():
    tok = gmail._access()
    if not tok:
        print("no gmail access token", file=sys.stderr)
        return []
    q = urllib.request.quote(QUERY)
    url = ("https://gmail.googleapis.com/gmail/v1/users/me/messages"
           f"?q={q}&maxResults=10")
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {tok}"})
    data = json.loads(urllib.request.urlopen(req, timeout=30).read())
    return data.get("messages", [])


def _body(mid):
    tok = gmail._access()
    url = ("https://gmail.googleapis.com/gmail/v1/users/me/messages/"
           f"{mid}?format=full")
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {tok}"})
    msg = json.loads(urllib.request.urlopen(req, timeout=30).read())
    parts = []

    def walk(p):
        data = (p.get("body") or {}).get("data")
        if data:
            parts.append(base64.urlsafe_b64decode(
                data + "=" * (-len(data) % 4)).decode(
                    "utf-8", "replace"))
        for s in p.get("parts") or []:
            walk(s)
    for p in msg.get("payload", {}).get("parts") or [msg["payload"]]:
        walk(p)
    raw = "\n".join(parts)
    # strip tags for regexing
    return re.sub(r"<[^>]+>", " ", raw)


def _insert(meta: dict) -> bool:
    meta_json = json.dumps(meta).replace("'", "''")
    sql = (
        "INSERT INTO analytics_events "
        "(id, team_id, social_account_id, event_type, platform, "
        " occurred_at, meta_data, created_at) "
        "SELECT gen_random_uuid(), a.team_id, a.id, "
        " 'weekly_analytics_recap', 'linkedin', "
        f" '{meta['week_end']} 23:59:59+00'::timestamptz, "
        f" '{meta_json}'::jsonb, now() "
        "FROM social_accounts a WHERE a.platform='linkedin' "
        "AND a.account_type='person' LIMIT 1")
    r = subprocess.run(
        [DOCKER, "exec", "social-postgres", "psql", "-U", "social_user",
         "-d", "social_automation", "-c", sql],
        capture_output=True, text=True, timeout=40)
    return r.returncode == 0


def main():
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    seen = set()
    if os.path.exists(STATE):
        seen = set(json.load(open(STATE)))
    inserted = 0
    for m in _messages():
        mid = m["id"]
        if mid in seen:
            continue
        try:
            text = _body(mid)
        except Exception as e:
            print(f"{mid}: body fetch failed: {e}")
            continue
        imp = IMP_RE.search(text)
        if not imp:
            seen.add(mid)
            continue
        react = REACT_RE.search(text)
        week = WEEK_RE.search(text)
        week_end = ""
        if week:
            try:
                import datetime as _dt
                we = _dt.datetime.strptime(week.group(2), "%m/%d/%Y")
                week_end = we.date().isoformat()
            except Exception:
                pass
        audience = AUDIENCE_RE.findall(text)[:3]
        meta = {
            "source": "linkedin_email_recap",
            "gmail_message_id": mid,
            "impressions": int(imp.group(1).replace(",", "")),
            "reactions": int(react.group(1).replace(",", ""))
            if react else None,
            "week": f"{week.group(1)}..{week.group(2)}" if week else None,
            "top_audience": [a.strip() for a in audience],
        }
        # occurred_at = parsed week end, else today
        meta_for_sql = dict(meta)
        meta_for_sql["week_end"] = week_end or __import__(
            "datetime").date.today().isoformat()
        if _insert(meta_for_sql):
            inserted += 1
            print(f"{mid}: inserted impressions={meta['impressions']} "
                  f"reactions={meta['reactions']} week={meta['week']}")
        else:
            print(f"{mid}: DB insert failed", file=sys.stderr)
            continue
        seen.add(mid)
    with open(STATE, "w") as f:
        json.dump(sorted(seen)[-200:], f)
    print(f"done — {inserted} recap(s) ingested")
    return 0


if __name__ == "__main__":
    sys.exit(main())
