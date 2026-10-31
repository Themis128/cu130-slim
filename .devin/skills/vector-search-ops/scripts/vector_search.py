#!/usr/bin/env python3
"""vector_search.py — semantic dedup + similar-content retrieval.
Embeds posts via the DMR embedding endpoint; cosine-similarity nearest-
neighbour search — the Vector Search adopt on Postgres."""
import json
import subprocess
import sys
import urllib.request

DMR_EMBED = "http://localhost:12435/engines/v1/embeddings"


def _embed(text: str) -> list:
    body = json.dumps({"model": "docker.io/ai/qwen3-embedding:latest",
                       "input": text[:2000]}).encode()
    try:
        r = urllib.request.urlopen(urllib.request.Request(
            DMR_EMBED, data=body, headers={"Content-Type": "application/json"}), timeout=30)
        return json.loads(r.read())["data"][0]["embedding"]
    except Exception:
        return []


def _psql(sql: str) -> str:
    r = subprocess.run(
        ["docker", "compose", "exec", "-T", "social-postgres", "psql",
         "-U", "social_user", "-d", "social_automation", "-tA", "-c", sql],
        capture_output=True, text=True, timeout=30)
    return r.stdout


import math
def _cos(a, b):
    dot = sum(x*y for x, y in zip(a, b))
    na = math.sqrt(sum(x*x for x in a)); nb = math.sqrt(sum(y*y for y in b))
    return dot/(na*nb) if na and nb else 0.0


def similar(text: str, k: int = 5):
    vec = _embed(text)
    if not vec:
        print("no embedding — DMR embed endpoint down"); return
    _psql("CREATE TABLE IF NOT EXISTS post_embeddings(post_id uuid PRIMARY KEY, content text, emb float8[])")
    rows = _psql("SELECT post_id, left(content,60), emb FROM post_embeddings").strip().splitlines()
    scored = [(float(i), cid, c[:60])
              for line in rows for cid, c, e in [line.split("|")]
              for i in [_cos(vec, [float(x) for x in e.strip('{}[]').split(',')])]]
    scored.sort(reverse=True)
    for s, cid, c in scored[:k]:
        print(f"{s:.3f} {c}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "similar"
    if cmd == "embed":
        _psql("CREATE TABLE IF NOT EXISTS post_embeddings(post_id uuid PRIMARY KEY, content text, emb float8[])")
        for line in _psql("SELECT id, left(content_text,500) FROM posts WHERE content_text IS NOT NULL LIMIT 50").strip().splitlines():
            cid, _, c = line.partition("|")
            e = _embed(c)
            if e:
                _psql("INSERT INTO post_embeddings VALUES('%s','%s','%s') ON CONFLICT DO NOTHING" % (cid, c.replace("'","''"), "{"+",".join(str(x) for x in e)+"}"))
        print("embedded")
    else:
        similar(" ".join(sys.argv[2:]))
