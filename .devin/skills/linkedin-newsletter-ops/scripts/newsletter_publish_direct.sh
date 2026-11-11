#!/usr/bin/env bash
# newsletter_publish_direct.sh — publish a LinkedIn newsletter issue by
# POSTing DIRECTLY to the linkedin-browser-sidecar (:9225/newsletter/publish),
# bypassing social-api's 420s->900s client timeout and its restart-prone
# request path. Use when /api/v1/newsletter/linkedin/issue keeps dying to
# Docker/WSL engine bounces or pr-land-watch service restarts.
#
# What it does:
#   1. preflight: engine up, social-api healthy, sidecar has_session
#   2. assembles the payload INSIDE social-api with app modules
#      (blog_articles.get_published_article + newsletter_cover.render_newsletter_cover)
#      and writes it to /app/uploads/newsletter_payload.json (bind-mounted,
#      so it lands on the host at social-automation/uploads/)
#   3. resolves the sidecar container IP and fires a DETACHED host curl with
#      --max-time 1500 straight at http://<ip>:9225/newsletter/publish —
#      survives social-api restarts; only an engine bounce kills it
#   4. polls the result file; prints the sidecar JSON on completion
#
# Usage:
#   newsletter_publish_direct.sh [--slug SLUG] [--issue N] [--name "Newsletter Name"] \
#       [--title "..."] [--html-file /path/body.html] [--dry-run] [--wait]
#
# Defaults: latest R2 article slug, issue 3, "The Cloudless Build Log".
# --html-file + --title skip article resolution entirely (explicit body).
set -euo pipefail

REPO="${REPO:-$HOME/cu130-slim}"
UPLOADS_HOST="$REPO/social-automation/uploads"
RESULT_LOG="$HOME/logs/newsletter_sidecar_result.json"
CURL_LOG="$HOME/logs/newsletter_sidecar_curl.log"
SIDECAR="linkedin-browser-sidecar"
API="social-api"

SLUG="2026-11-02-weekly-cloud-computing-trends"
ISSUE=3
NAME="The Cloudless Build Log"
TITLE=""
HTML_FILE=""
DRY_RUN=false
WAIT=true

while [[ $# -gt 0 ]]; do
  case "$1" in
    --slug) SLUG="$2"; shift 2;;
    --issue) ISSUE="$2"; shift 2;;
    --name) NAME="$2"; shift 2;;
    --title) TITLE="$2"; shift 2;;
    --html-file) HTML_FILE="$2"; shift 2;;
    --dry-run) DRY_RUN=true; shift;;
    --no-wait) WAIT=false; shift;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done

echo "== preflight =="
docker info >/dev/null 2>&1 || { echo "FATAL: docker engine down"; exit 1; }
docker ps --format '{{.Names}}' | grep -qx "$API" || { echo "FATAL: $API not running"; exit 1; }
docker ps --format '{{.Names}}' | grep -qx "$SIDECAR" || { echo "FATAL: $SIDECAR not running"; exit 1; }

HEALTH=$(docker exec "$API" python -c "
import urllib.request,json,sys
try:
    h=json.loads(urllib.request.urlopen('http://$SIDECAR:9225/health',timeout=15).read())
    print(json.dumps({k:h.get(k) for k in ('has_session','browser_connected','page_ok','rate_limited')}))
except Exception as e:
    print(json.dumps({'error':str(e)}))
")
echo "sidecar: $HEALTH"
echo "$HEALTH" | grep -q '"has_session": true' || { echo "FATAL: no LinkedIn session on sidecar"; exit 1; }

# warn if pr-land-watch restarted services recently (publish needs ~15min quiet)
if [[ -f "$HOME/logs/pr-land-watch.log" ]]; then
  LAST_RESTART=$(grep "restarted.*services" "$HOME/logs/pr-land-watch.log" | tail -1 | awk '{print $1" "$2}')
  [[ -n "$LAST_RESTART" ]] && echo "note: last service restart by pr-land-watch: $LAST_RESTART"
fi

echo "== assembling payload inside $API =="
# --html-file is a HOST path; the container only sees bind mounts, so stage
# it under uploads/ and pass the container-side path instead.
if [[ -n "$HTML_FILE" ]]; then
  [[ -f "$HTML_FILE" ]] || { echo "FATAL: --html-file $HTML_FILE not found"; exit 1; }
  cp "$HTML_FILE" "$UPLOADS_HOST/newsletter_body.html"
  HTML_FILE="/app/uploads/newsletter_body.html"
fi
# shellcheck disable=SC2087
SLUG="$SLUG" ISSUE="$ISSUE" NAME="$NAME" TITLE="$TITLE" HTML_FILE="$HTML_FILE" DRY_RUN="$DRY_RUN" \
docker exec -i -w /app -e SLUG -e ISSUE -e NAME -e TITLE -e HTML_FILE -e DRY_RUN "$API" python - <<'PYEOF'
import asyncio, json, base64, os, sys

slug = os.environ.get("SLUG") or ""
issue = int(os.environ.get("ISSUE") or 0)
name = os.environ.get("NAME") or ""
title_arg = os.environ.get("TITLE") or ""
html_file = os.environ.get("HTML_FILE") or ""
dry = os.environ.get("DRY_RUN") == "true"

async def resolve():
    """Return (title, html, seo_desc, post_desc, category, cover_b64)."""
    from app.services import blog_articles
    from app.services.newsletter_cover import render_newsletter_cover
    if html_file:
        html = open(html_file).read()
        title = title_arg
        png = render_newsletter_cover(title or "Newsletter",
                                      issue_number=issue or None, category="")
        return title, html, "", "", "", base64.b64encode(png).decode()
    a = await blog_articles.get_published_article(slug)
    if not a:
        sys.exit(f"FATAL: no published article for slug {slug!r}")
    html = (a.get("html") or "").strip() or blog_articles.article_markdown_to_html(
        a.get("contentMarkdown") or a.get("content") or "")
    if not html:
        sys.exit("FATAL: article resolved but html body empty")
    title = title_arg or (a.get("title") or "").strip()
    seo_desc = (a.get("excerpt") or "")[:160]
    post_desc = blog_articles.clean_social_post(a.get("socialPost") or "") or ""
    category = a.get("category") or ""
    png = render_newsletter_cover(title, issue_number=issue or None, category=category)
    return title, html, seo_desc, post_desc, category, base64.b64encode(png).decode()

title, html, seo_desc, post_desc, category, cover_b64 = asyncio.run(resolve())
payload = {
    "title": title, "html_body": html,
    "cover_base64": cover_b64, "cover_filename": "newsletter-cover.png",
    "newsletter_name": name, "seo_title": (title or "")[:60],
    "seo_description": seo_desc, "post_description": post_desc,
    "dry_run": dry,
}
open("/app/uploads/newsletter_payload.json", "w").write(json.dumps(payload))
print(f"payload: title={title!r} html_len={len(html)} cover={len(cover_b64)}b dry={dry}")
PYEOF

PAYLOAD="$UPLOADS_HOST/newsletter_payload.json"
[[ -f "$PAYLOAD" ]] || { echo "FATAL: payload not written to $PAYLOAD"; exit 1; }

IP=$(docker inspect "$SIDECAR" --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')
[[ -n "$IP" ]] || { echo "FATAL: no IP for $SIDECAR"; exit 1; }
echo "sidecar ip: $IP"

rm -f "$RESULT_LOG"
nohup curl -sS --max-time 1500 -X POST "http://$IP:9225/newsletter/publish" \
  -H 'Content-Type: application/json' --data-binary "@$PAYLOAD" \
  -o "$RESULT_LOG" -w "HTTP %{http_code} time %{time_total}s\n" > "$CURL_LOG" 2>&1 &
CURL_PID=$!
echo "curl pid $CURL_PID -> $RESULT_LOG"

if [[ "$WAIT" == "true" ]]; then
  echo "== waiting (poll every 30s, sidecar untouched) =="
  for i in $(seq 1 50); do
    sleep 30
    if ! kill -0 "$CURL_PID" 2>/dev/null; then
      echo "curl finished after ~$((i*30))s"
      cat "$CURL_LOG"
      echo "--- result ---"
      cat "$RESULT_LOG" 2>/dev/null | head -c 3000; echo
      exit 0
    fi
    docker ps --format '{{.Names}}' | grep -qx "$SIDECAR" || { echo "FATAL: $SIDECAR vanished mid-publish"; exit 1; }
  done
  echo "still running after 25min — check $RESULT_LOG later"
else
  echo "detached. poll: cat $RESULT_LOG"
fi
