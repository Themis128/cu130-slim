#!/usr/bin/env python3
"""content_eval.py — LLM-judge eval on generated posts (the MLflow-eval adopt).
Scores a draft on voice/dup/clarity/CTA 0-10; blocks below a threshold —
a quantitative quality gate vs heuristic-only slop-scan."""
import json
import sys
import urllib.request

DMR = "http://localhost:12435/engines/v1/chat/completions"
RUBRIC = ("Score this social post 0-10 on: 1) brand-voice fit (plain, direct, "
          "no AI-cliches), 2) specificity (one concrete detail vs generic claims), "
          "3) readability (sentence length, structure), 4) CTA clarity. "
          "Reply with ONLY a JSON: {\"total\":N,\"voice\":N,\"specificity\":N,"
          "\"readability\":N,\"cta\":N,\"why\":\"one phrase\"}.")


def eval_post(text: str) -> dict:
    body = json.dumps({"model": "ai/llama3.2", "messages": [
        {"role": "system", "content": RUBRIC},
        {"role": "user", "content": text[:2000]}],
        "temperature": 0.0, "max_tokens": 120}).encode()
    try:
        r = urllib.request.urlopen(urllib.request.Request(
            DMR, data=body, headers={"Content-Type": "application/json"}), timeout=60)
        raw = json.loads(r.read())["choices"][0]["message"]["content"].strip()
        i = raw.find("{")
        j = raw.rfind("}") + 1
        if i < 0 or j <= i:
            return {"total": 0, "why": "no-json"}
        return json.loads(raw[i:j])
    except Exception:
        return {"total": 0, "why": "eval-error"}


if __name__ == "__main__":
    out = eval_post(" ".join(sys.argv[1:]) or sys.stdin.read())
    print(json.dumps(out, indent=2))
    sys.exit(0 if out.get("total", 0) >= 6 else 1)
