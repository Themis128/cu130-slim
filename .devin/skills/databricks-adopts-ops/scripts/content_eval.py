#!/usr/bin/env python3
"""content_eval.py — LLM-judge eval on generated posts (the MLflow-eval adopt).
Scores a draft on voice/specificity/readability/CTA 0-10 and derives an overall
0-10 "total" as the mean of the four sub-scores (the model's own total is
ignored); blocks below a threshold — a quantitative quality gate vs
heuristic-only slop-scan."""
import json
import sys
import urllib.request

DMR = "http://localhost:12435/engines/v1/chat/completions"
SUB_SCORES = ("voice", "specificity", "readability", "cta")
RUBRIC = ("Score this social post 0-10 on: 1) brand-voice fit (plain, direct, "
          "no AI-cliches), 2) specificity (one concrete detail vs generic claims), "
          "3) readability (sentence length, structure), 4) CTA clarity. "
          "Also give \"total\", your overall 0-10 judgement. "
          "Reply with ONLY a JSON: {\"total\":N,\"voice\":N,\"specificity\":N,"
          "\"readability\":N,\"cta\":N,\"why\":\"one phrase\"}.")


def _score(v) -> float:
    """Coerce a sub-score to float; anything non-numeric counts as 0."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


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
            return {"total": 0.0, "why": "no-json"}
        out = json.loads(raw[i:j])
        if not isinstance(out, dict):
            return {"total": 0.0, "why": "no-json"}
        # "total" is the overall 0-10 mean of the four sub-scores, computed
        # here — a model-supplied total can never sneak a weak post past the
        # gate and a non-numeric value can never raise downstream.
        subs = [_score(out.get(k)) for k in SUB_SCORES]
        out["total"] = round(sum(subs) / len(subs), 1)
        return out
    except Exception:
        return {"total": 0.0, "why": "eval-error"}


if __name__ == "__main__":
    out = eval_post(" ".join(sys.argv[1:]) or sys.stdin.read())
    print(json.dumps(out, indent=2))
    sys.exit(0 if out.get("total", 0.0) >= 6 else 1)
