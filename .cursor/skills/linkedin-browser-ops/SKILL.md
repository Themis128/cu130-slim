# LinkedIn Browser Ops — the /debug/* endpoints + in-page fetch

The linkedin-browser-sidecar's real power surface: a logged-in Chromium
page you can drive with arbitrary JS. This is the **only** reliable way to
hit LinkedIn's internal endpoints — raw cookie replay (httpx + exported
`li_at`/`JSESSIONID`) gets 401/302 on everything except `/voyager/api/me`
because LinkedIn injects extra headers the replay misses.

## Endpoints (all `POST`, body `{"expr": "<js>"}` for eval)

| Endpoint | Payload | What it does |
|---|---|---|
| `/debug/eval` | `{"expr": "..."}` | `(async () => { expr })()` in the live page |
| `/debug/navigate` | `{"url": "..."}` | page.goto in the session |
| `/debug/click` | `{"selector": "..."}` | click element |
| `/debug/type` | `{"selector","text","delay"}` | real keystrokes (not paste) |
| `/debug/press` | `{"key","selector?"}` | key press |
| `/debug/wait` | `{"ms"}` | sleep |
| `/debug/hover` | `{"selector"}` | hover |
| `/session` GET | — | session state (logged_in?) |
| `/health` | — | sidecar health |

## The in-page fetch pattern — the LinkedIn-native API

Inside `expr`, `fetch()` carries the page's own session headers. Any
voyager/dash endpoint works — this is how `fetch_subscribers` should call
the series-subscribers feed instead of httpx:

```js
const csrf = (document.cookie.match(/JSESSIONID="?([^";]+)/)||[])[1]||'';
const r = await fetch('/voyager/api/voyagerPublishingDashSeriesSubscribers' +
  '?q=contentSeries&seriesUrn=urn:li:fsd_contentSeries:<ID>&count=50&start=0',
  {headers: {'csrf-token': csrf, 'x-restli-protocol-version': '2.0.0'}});
return {status: r.status, data: await r.json()};
```

Status codes: **200** real data · **400** wrong params · **401** session
dead (re-auth) · **404** wrong endpoint path.

## Scripts

- `scripts/eval.sh "<js>"` — run an expression in the live page
- `scripts/nav.sh "<url>"` — navigate the session page
- `scripts/fetch-subscribers.sh "<seriesUrn>"` — the correct in-page
  subscriber fetch (the pattern `fetch_subscribers` should adopt)

## Known-working fetches

- `/voyager/api/me` → profile data (also works via raw cookies)
- `/in/<vanity>/` → full profile HTML (200 via in-page fetch)
- `/in/<vanity>/recent-activity/newsletter/` → pulse issue URLs (no
  series URNs embedded — LinkedIn lazy-loads them)
- `/voyager/api/voyagerPublishingDashSeriesSubscribers` → 401 without a
  valid `seriesUrn` param

## Sniffer scripts (upgrade)

- `scripts/sniffer-install.sh` — arm fetch+XHR hooks, sessionStorage-
  persistent across SPA navigation. Arm AFTER a full-page navigate (hooks
  die on reload; only the log survives), BEFORE the SPA click you want.
- `scripts/sniffer-read.sh [regex]` — read captures (status + first 400
  bytes of body per request).
- `scripts/sniffer-clear.sh` — reset the buffer.

Known limit: LinkedIn's subscribers dialog renders server-side/embedded —
no capturable XHR fires, so this path can't capture that endpoint's shape.
DOM-scraping the rendered list is the working fallback.
