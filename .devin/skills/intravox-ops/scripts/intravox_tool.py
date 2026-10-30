#!/usr/bin/env python3
"""IntraVox ops CLI — verified API surface for cloud.cloudless.gr.

Auth:  NEXTCLOUD_USERNAME + NEXTCLOUD_APP_PASSWORD env (app password).
Base:  NC_BASE_URL env or https://cloud.cloudless.gr
API:   {base}/apps/intravox/api  ·  WebDAV {base}/remote.php/dav/files/<user>/

Quirks encoded here so callers don't rediscover them:
  * Widget `type` strings are hyphenated and VALIDATED (unknown types are
    silently stripped server-side on PUT — the row comes back empty).
  * News widget `sourcePath` is relative to the language root
    ('news', NOT 'en/news'). This tool normalizes a leading 'en/' away.
  * Page create = POST /api/pages {id,title,language,parentPath}
  content
    then goes via PUT /api/pages/{uniqueId} with the FULL page object.
  * Navigation is a single document: GET /api/navigation, mutate,
    POST back the same shape.
"""

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = os.environ.get("NC_BASE_URL", "https://cloud.cloudless.gr").rstrip("/")
USER = os.environ.get("NEXTCLOUD_USERNAME", "")
APP_PW = os.environ.get("NEXTCLOUD_APP_PASSWORD", "")
API = f"{BASE}/apps/intravox/api"
DAV = f"{BASE}/remote.php/dav/files/{urllib.parse.quote(USER)}/"

VALID_WIDGET_TYPES = {
    "text", "heading", "links", "divider", "image", "video",
    "news", "feed", "file-story", "photo-story", "calendar", "people",
}


def auth() -> str:
    if not USER or not APP_PW:
        sys.exit("set NEXTCLOUD_USERNAME + NEXTCLOUD_APP_PASSWORD")
    return "Basic " + base64.b64encode(f"{USER}:{APP_PW}".encode()).decode()


def api(method: str, path: str, body=None, dav=False):
    url = (DAV + path.lstrip("/")) if dav else (API + path)
    req = urllib.request.Request(
        url, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": auth(),
            "OCS-APIRequest": "true",
            "Content-Type": "application/json",
        },
    )
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        raw = resp.read()
        return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        detail = e.read()[:400]
        try:
            detail = json.loads(detail)
        except Exception:
            detail = str(detail[:200])
        return e.code, detail


def dav_put(dest: str, data: bytes) -> int:
    req = urllib.request.Request(
        DAV + "/".join(urllib.parse.quote(p) for p in dest.split("/")),
        method="PUT", data=data, headers={"Authorization": auth()})
    return urllib.request.urlopen(req, timeout=120).status


def dav_mkcol(dest: str) -> int:
    req = urllib.request.Request(
        DAV + "/".join(urllib.parse.quote(p) for p in dest.split("/")),
        method="MKCOL", headers={"Authorization": auth()})
    try:
        return urllib.request.urlopen(req, timeout=30).status
    except urllib.error.HTTPError as e:
        return e.code  # 405 = exists


def norm_source_path(p: str) -> str:
    """Strip language root ('en/…' → '…') — API expects paths relative to it."""
    return p[3:] if p.startswith("en/") else p


def validate_widget(w: dict) -> dict:
    t = w.get("type")
    if t not in VALID_WIDGET_TYPES:
        sys.exit(f"invalid widget type {t!r} — valid: {sorted(VALID_WIDGET_TYPES)}")
    if t == "news" and w.get("sourcePath"):
        w["sourcePath"] = norm_source_path(w["sourcePath"])
    if t in ("file-story", "photo-story"):
        cfg = w.setdefault("config", {})
        if cfg.get("folderPath"):
            cfg["folderPath"] = cfg["folderPath"].lstrip("/")
    return w


def cmd_health(_):
    s, d = api("GET", "/health")
    print(s, json.dumps(d))


def cmd_pages_list(a):
    s, d = api("GET", f"/pages?path={a.lang}")
    items = d if isinstance(d, list) else d.get("pages", d.get("items", []))
    for it in items:
        print(f"{it.get('uniqueId')}  {it.get('title')}")


def cmd_page_get(a):
    s, d = api("GET", f"/pages/{a.uid}")
    print(json.dumps(d, indent=1) if s == 200 else f"{s} {d}")


def cmd_page_create(a):
    body = {"id": a.id, "title": a.title, "language": a.lang}
    if a.parent:
        body["parentPath"] = a.parent
    s, d = api("POST", "/pages", body)
    print(s, json.dumps(d)[:400])


def cmd_widget_add(a):
    s, pg = api("GET", f"/pages/{a.uid}")
    if s != 200:
        sys.exit(f"GET page: {s} {pg}")
    widget = validate_widget(json.loads(a.widget))
    if widget.get("type") != a.type:
        sys.exit("--type and --widget type mismatch")
    row = {"columns": a.column_count, "widgets": [widget]}
    pg.setdefault("layout", {}).setdefault("rows", []).append(row)
    s, d = api("PUT", f"/pages/{a.uid}", pg)
    print("PUT:", s)
    # verify the widget survived (server strips unknown types silently)
    s, pg2 = api("GET", f"/pages/{a.uid}")
    types = [w.get("type") for r in pg2.get("layout", {}).get("rows", []) for w in r.get("widgets", [])]
    print("widgets now:", types)
    if a.type not in types:
        sys.exit("widget was stripped — check type/config fields")


def cmd_news(a):
    q = urllib.parse.urlencode({
        "sourcePath": norm_source_path(a.source),
        "limit": a.limit, "sortBy": "modified", "sortOrder": "desc",
        "filterPublished": "true", "showDate": "true", "showExcerpt": "true",
    })
    s, d = api("GET", f"/news?{q}")
    items = d if isinstance(d, list) else d.get("items", [])
    print(f"{len(items)} item(s)")
    for it in items:
        print(" -", it.get("title"))


def cmd_nav_get(_):
    s, d = api("GET", "/navigation")
    def walk(items, depth=0):
        for it in items or []:
            print("  " * depth + f"{it.get('id')}  {it.get('title')}  -> {it.get('uniqueId') or it.get('url')}")
            walk(it.get("children"), depth + 1)
    walk((d.get("navigation") or {}).get("items", []))


def cmd_nav_add_child(a):
    s, nav = api("GET", "/navigation")
    if s != 200:
        sys.exit(f"GET navigation: {s}")
    items = nav["navigation"]["items"]
    def find(its):
        for it in its:
            if it.get("id") == a.parent:
                return it
            r = find(it.get("children") or [])
            if r:
                return r
        return None
    parent = find(items) if a.parent else {"children": items}
    if parent is None:
        sys.exit(f"nav id {a.parent!r} not found")
    parent.setdefault("children", []).append({
        "id": a.id, "title": a.title,
        "uniqueId": a.uid, "url": a.url, "target": None, "children": [],
    })
    s, d = api("POST", "/navigation", nav)
    print("POST /navigation:", s)


def cmd_upload(a):
    src = Path(a.local)
    dest = a.dest.rstrip("/")
    if src.is_dir():
        files = sorted(src.rglob("*"))
        files = [f for f in files if f.is_file()]
    else:
        files = [src]
    done = fail = 0
    for f in files:
        rel = f.relative_to(src) if src.is_dir() else Path(f.name)
        target = dest + "/" + str(rel).replace(os.sep, "/")
        parent = str(Path(target).parent).replace(os.sep, "/")
        parts = parent.split("/")
        for i in range(1, len(parts) + 1):
            dav_mkcol("/".join(parts[:i]))
        code = dav_put(target, f.read_bytes())
        if code in (200, 201, 204):
            done += 1
        else:
            fail += 1
            print(" FAIL", code, target)
    print(f"uploaded {done}, failed {fail} -> /{dest}")


def main() -> int:
    ap = argparse.ArgumentParser(prog="intravox_tool")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("health").set_defaults(f=cmd_health)

    pl = sub.add_parser("pages-list")
    pl.add_argument("--lang", default="en")
    pl.set_defaults(f=cmd_pages_list)

    pg = sub.add_parser("page-get")
    pg.add_argument("uid")
    pg.set_defaults(f=cmd_page_get)

    pc = sub.add_parser("page-create")
    pc.add_argument("id")
    pc.add_argument("title")
    pc.add_argument("--lang", default="en")
    pc.add_argument("--parent", default="")
    pc.set_defaults(f=cmd_page_create)

    wa = sub.add_parser("widget-add")
    wa.add_argument("uid")
    wa.add_argument("--type", required=True, choices=sorted(VALID_WIDGET_TYPES))
    wa.add_argument("--widget", required=True, help="full widget JSON incl. type")
    wa.add_argument("--column-count", type=int, default=1)
    wa.set_defaults(f=cmd_widget_add)

    nw = sub.add_parser("news")
    nw.add_argument("--source", default="news")
    nw.add_argument("--limit", type=int, default=5)
    nw.set_defaults(f=cmd_news)

    ng = sub.add_parser("nav-get")
    ng.set_defaults(f=cmd_nav_get)

    na = sub.add_parser("nav-add-child")
    na.add_argument("parent", help="nav id e.g. nav_ops, or '' for root")
    na.add_argument("id")
    na.add_argument("title")
    na.add_argument("--uid", default=None)
    na.add_argument("--url", default=None)
    na.set_defaults(f=cmd_nav_add_child)

    up = sub.add_parser("upload")
    up.add_argument("local")
    up.add_argument("dest", help="user-visible folder e.g. IntraVox/media/x")
    up.set_defaults(f=cmd_upload)

    a = ap.parse_args()
    a.f(a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
