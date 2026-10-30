#!/usr/bin/env python3
"""ax_pass_ingest.py — ingest the AX Pass (Shift.Forward) post-event page.

Drop the saved HTML (Ctrl+S) or pasted text into Downloads
the script
extracts resource links/tools/frameworks and maps them to stack surfaces.

    ax_pass_ingest.py [file]            # auto-picks newest axpass/after file
    ax_pass_ingest.py --watch           # poll Downloads like audience_watch
"""
import html.parser
import json
import re
import sys
import time
from pathlib import Path

DIRS = [Path("/mnt/c/Users/tbaltzakis/Downloads"), Path.home() / "Downloads"]
PAT = re.compile(r"(axpass|shift.?forward|after.?event)", re.I)
LINK = re.compile(r'href="([^"]+)"')
STOP = {"legal", "privacy", "terms", "cookies", "favicon", "css", "js"}

# stack surfaces to match adopt candidates against
STACK = ["n8n", "mcp", "skill", "worker", "celery", "agent", "memory",
         "vectorize", "duckdb", "datalake", "r2", "d1", "langgraph",
         "crewai", "autogen", "magentic", "tool", "workflow"]


class P(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self.text: list[str] = []
        self._in = False

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._in = True
            self._href = dict(attrs).get("href", "")

    def handle_data(self, d):
        self.text.append(d)
        if self._in and self._href:
            self.links.append((d.strip(), self._href))

    def handle_endtag(self, tag):
        if tag == "a":
            self._in = False


def latest() -> Path | None:
    for d in DIRS:
        if not d.is_dir():
            continue
        hits = [p for p in d.iterdir()
                if p.is_file() and PAT.search(p.name)
                and p.suffix in {".html", ".htm", ".txt", ".mhtml"}]
        if hits:
            return max(hits, key=lambda p: p.stat().st_mtime)
    return None


def extract(path: Path) -> dict:
    raw = path.read_text(errors="replace")
    if path.suffix == ".txt":
        text, links = raw, [(t, u) for u in LINK.findall(raw) for t in [u]]
    else:
        p = P()
        p.feed(raw)
        text, links = " ".join(p.text), p.links
    urls = []
    for label, u in links:
        u = u.strip()
        if not u or u.startswith(("javascript", "#", "/_next", "/brand", "/static")):
            continue
        if any(s in u.lower() for s in STOP):
            continue
        urls.append({"label": label[:60], "url": u})
    # keyword hits → adopt candidates
    low = text.lower()
    hits = {k for k in STACK if k in low}
    return {"file": path.name, "chars": len(text),
            "links": urls[:60], "stack_hits": sorted(hits),
            "text_head": text[:800]}


def main() -> int:
    if "--watch" in sys.argv:
        seen = set()
        print("watching Downloads for axpass/shiftforward exports…")
        while True:
            p = latest()
            if p and str(p) not in seen:
                seen.add(str(p))
                out = extract(p)
                Path("/tmp/axpass-adopt.json").write_text(json.dumps(out, indent=2))
                print(f"ingested {p.name}: {len(out['links'])} links, "
                      f"hits={out['stack_hits']} → /tmp/axpass-adopt.json")
            time.sleep(15)
    path = Path(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1] != "--watch" else latest()
    if not path or not path.is_file():
        print("no axpass/after-event file in Downloads — save the page first")
        return 1
    print(json.dumps(extract(path), indent=2)[:4000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
