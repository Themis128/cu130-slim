#!/usr/bin/env python3
"""ai_classify.py — SQL-callable AI data functions via the DMR model.
ai_classify("is this a lead?", text) → one-word label; ai_extract(field, text)
→ a value. The Databricks AI-Functions pattern on the datalake."""
import json
import sys
import urllib.request

DMR = "http://localhost:12435/engines/v1/chat/completions"
COMMANDS = ("ai_classify", "ai_extract")


def _dmr(prompt: str) -> str:
    body = json.dumps({"model": "ai/llama3.2", "messages": [{"role": "user", "content": prompt}],
                       "temperature": 0.0, "max_tokens": 40}).encode()
    r = urllib.request.urlopen(urllib.request.Request(
        DMR, data=body, headers={"Content-Type": "application/json"}), timeout=45)
    return json.loads(r.read())["choices"][0]["message"]["content"].strip()


def ai_classify(instruction: str, text: str) -> str:
    return _dmr(f"Classify strictly. {instruction}. Reply with ONE word only.\n\n{text[:1500]}")


def ai_extract(field: str, text: str) -> str:
    return _dmr(f"Extract the {field} from this text. Reply with ONLY the value.\n\n{text[:1500]}")


def _usage() -> None:
    print("usage: ai_classify.py {ai_classify|ai_extract} <instruction|field> <text>",
          file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    if len(sys.argv) < 4 or sys.argv[1] not in COMMANDS:
        _usage()
    cmd, arg, text = sys.argv[1], sys.argv[2], " ".join(sys.argv[3:])
    if cmd == "ai_classify":
        print(ai_classify(arg, text))
    else:
        print(ai_extract(arg, text))
