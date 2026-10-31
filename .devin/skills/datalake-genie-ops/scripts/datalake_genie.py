#!/usr/bin/env python3
"""datalake_genie.py — natural-language → SQL → answer over SocialAuto Postgres.

The Genie-One pattern: a question → the DMR model generates a read-only SELECT
→ it's validated + executed → the answer's returned. No Databricks needed."""
import json
import os
import subprocess
import sys
import textwrap
import urllib.request

DMR = "http://localhost:12435/engines/v1/chat/completions"
SCHEMA_TABLES = ("posts", "social_accounts", "engagement_candidates",
                 "engagement_actions", "publish_queue", "survey_responses",
                 "media_assets", "audit_requests")


def _schema() -> str:
    r = subprocess.run(
        ["docker", "compose", "exec", "-T", "social-postgres",
         "psql", "-U", "social_user", "-d", "social_automation", "-tA", "-c",
         "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"],
        capture_output=True, text=True, timeout=30)
    return r.stdout.strip() or ",".join(SCHEMA_TABLES)


def _gen_sql(question: str, schema: str) -> str:
    prompt = ("You generate READ-ONLY Postgres SELECT queries. Output ONLY the "
              "SQL — no prose, no markdown. Tables: " + schema + ". "
              "Question: " + question)
    body = json.dumps({"model": "ai/llama3.2",
                       "messages": [{"role": "user", "content": prompt}],
                       "temperature": 0.0, "max_tokens": 400}).encode()
    try:
        r = urllib.request.urlopen(urllib.request.Request(
            DMR, data=body, headers={"Content-Type": "application/json"}), timeout=45)
        raw = json.loads(r.read())["choices"][0]["message"]["content"]
        return raw.strip().strip("`").split(";")[0] + ";"
    except Exception:
        return ""


def _safe(sql: str) -> bool:
    s = sql.lower()
    return s.startswith("select") and not any(k in s for k in
                                              ("insert", "update", "delete", "drop",
                                               "truncate", "alter", "grant"))


def ask(question: str) -> int:
    schema = _schema()
    sql = _gen_sql(question, schema)
    if not _safe(sql):
        print("no safe query generated"); return 1
    print("SQL:", sql)
    r = subprocess.run(
        ["docker", "compose", "exec", "-T", "social-postgres",
         "psql", "-U", "social_user", "-d", "social_automation", "-c", sql],
        capture_output=True, text=True, timeout=30)
    print(r.stdout or r.stderr)
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "ask"
    if cmd == "schema":
        print(_schema())
    elif cmd == "sql":
        print(_gen_sql(" ".join(sys.argv[2:]), _schema()))
    else:
        sys.exit(ask(" ".join(sys.argv[1:])))
