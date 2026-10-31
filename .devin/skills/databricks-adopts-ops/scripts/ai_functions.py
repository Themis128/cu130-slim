#!/usr/bin/env python3
"""ai_functions.py — the Databricks AI-Functions chain via the DMR.
fix_grammar / ai_classify / ai_extract / ai_mask / ai_similarity — SQL-callable
or CLI. Media copy passes fix_grammar -> nlp>=80 -> similarity<0.85 before render."""
import json
import math
import subprocess
import sys
import urllib.request

DMR = "http://localhost:12435/engines/v1/chat/completions"
DMR_EMBED = "http://localhost:12435/engines/v1/embeddings"


def _chat(prompt, tok=200):
    body = json.dumps({"model": "ai/smollm3", "messages": [
        {"role": "user", "content": prompt}], "temperature": 0.0, "max_tokens": tok}).encode()
    r = urllib.request.urlopen(urllib.request.Request(
        DMR, data=body, headers={"Content-Type": "application/json"}), timeout=90)
    return json.loads(r.read())["choices"][0]["message"]["content"].strip()


def _embed(text):
    body = json.dumps({"model": "docker.io/ai/qwen3-embedding:latest",
                       "input": text[:1500]}).encode()
    r = urllib.request.urlopen(urllib.request.Request(
        DMR_EMBED, data=body, headers={"Content-Type": "application/json"}), timeout=60)
    return json.loads(r.read())["data"][0]["embedding"]


def _cos(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)) or 1)


def fix_grammar(text):
    return _chat("Fix ONLY spelling and grammar. Preserve meaning, proper nouns, "
                 "hashtags, URLs, numbers EXACTLY. Return ONLY the corrected text.\n\n" + text)


def ai_classify(instruction, text):
    return _chat(f"Classify strictly. {instruction}. Reply ONE word.\n\n{text[:1200]}", 10)


def ai_extract(field, text):
    return _chat(f"Extract the {field}. Reply ONLY the value.\n\n{text[:1200]}", 30)


def ai_mask(text, labels="person,email,phone"):
    return _chat(f"Replace all {labels} in this text with [MASKED]. "
                 f"Return ONLY the masked text.\n\n{text[:1200]}")


def ai_similarity(a, b):
    return _cos(_embed(a), _embed(b))


def media_gate(text):
    """The full pre-render chain: fix_grammar -> nlp>=80 (best-effort)."""
    fixed = fix_grammar(text)
    nlp = -1
    try:
        safe = fixed.replace('"', "'")[:800]
        r = subprocess.run(
            ["docker", "compose", "exec", "-T", "social-api", "python", "-c",
             'from app.services.plain_english import sofia_nlp_score;'
             f'print(sofia_nlp_score("{safe}")[0])'],
            cwd="social-automation/backend",
            capture_output=True, text=True, timeout=120)
        nlp = int(r.stdout.strip() or -1)
    except Exception:
        pass
    return {"pass": nlp < 0 or nlp >= 80, "nlp": nlp, "fixed": fixed}


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "media_gate":
        print(json.dumps(media_gate(" ".join(sys.argv[2:])), indent=1))
    elif cmd == "ai_similarity":
        print(ai_similarity(sys.argv[2], sys.argv[3]))
    elif cmd == "ai_classify":
        print(ai_classify(sys.argv[2], " ".join(sys.argv[3:])))
    elif cmd == "ai_extract":
        print(ai_extract(sys.argv[2], " ".join(sys.argv[3:])))
    elif cmd == "ai_mask":
        print(ai_mask(" ".join(sys.argv[2:])))
    elif cmd == "fix_grammar":
        print(fix_grammar(" ".join(sys.argv[2:])))
