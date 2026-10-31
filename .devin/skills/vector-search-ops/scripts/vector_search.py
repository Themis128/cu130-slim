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
    body = json.dumps({"model": "ai/mxbai-embed-large",
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


def similar(text: str, k: int = 5):
    vec = _embed(text)
    if not vec:
        print("no embedding — DMR embed endpoint down"); return
    lit = "[" + ",".join(str(x) for x in vec) + "]"
    sql = (f"SELECT id, left(content_text,80), 1-(embedding<=>'{lit}') AS sim "
           "FROM posts WHERE embedding IS NOT NULL "
           f"ORDER BY embedding<=>'{lit}' LIMIT {k}")
    print(_psql(sql))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "similar"
    if cmd == "embed":
        print(_psql("ALTER TABLE posts ADD COLUMN IF NOT EXISTS embedding vector(1024)"))
    else:
        similar(" ".join(sys.argv[2:]))
