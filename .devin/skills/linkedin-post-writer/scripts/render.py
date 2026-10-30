#!/usr/bin/env python3
"""
Render a LinkedIn post as a LinkedIn-style mockup HTML artifact.

JSON schema:
{
  "author": "Daniel Paul",
  "headline": "AI Trainer & Speaker · Founder of Purely Personal",
  "post": "...the post text...",
  "intent": "nurturing",
  "variation": "story-flow",
  "char_count": 1421,
  "scores": {"voice":1,"pov":1,"specificity":1,"asking":1}
}
"""
import argparse, json, sys
from pathlib import Path

TEMPLATE = Path(__file__).parent / "template.html"

def render(data: dict) -> str:
    template = TEMPLATE.read_text()
    total = sum(data.get("scores", {}).values())
    payload = {
        "author": data.get("author", "Anonymous"),
        "headline": data.get("headline", ""),
        "post": data.get("post", ""),
        "intent": data.get("intent", "—").upper(),
        "variation": data.get("variation", "—"),
        "charCount": data.get("char_count") or len(data.get("post", "")),
        "scores": data.get("scores", {"voice":0,"pov":0,"specificity":0,"asking":0}),
        "total": total,
        "shipReady": total == 4,
    }
    return template.replace("/*__DATA__*/", json.dumps(payload))

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--json"); p.add_argument("--in", dest="infile"); p.add_argument("--out", required=True)
    args = p.parse_args()
    if args.json: data = json.loads(args.json)
    elif args.infile: data = json.loads(Path(args.infile).read_text())
    else: data = json.loads(sys.stdin.read())
    Path(args.out).write_text(render(data))
    print(f"✓ Rendered {Path(args.out).resolve()}")

if __name__ == "__main__": main()
