"""Patch comfy-kitchen PEP-585 builtin generics for torch infer_schema.

comfy-kitchen 0.2.40 annotates custom-op signatures with builtin generics
(list[int], dict[...], ...) which torch's infer_schema cannot parse —
only typing.List/Dict/... are accepted. ComfyUI HEAD imports
comfy_kitchen.int8_decode, whose registration chain hits this on every
backend file, so the whole package's annotation generics are rewritten.

Safe to blanket-replace: the package has no isinstance(x, list[...]) or
cast(list[...], ...) runtime uses — every occurrence is an annotation.
"""

import pathlib
import re
import sys

SITE_PACKAGES = sys.argv[1] if len(sys.argv) > 1 else "/opt/venv/lib/python3.10/site-packages"
SITE = pathlib.Path(SITE_PACKAGES) / "comfy_kitchen"
MAP = {"list": "List", "dict": "Dict", "tuple": "Tuple", "set": "Set", "type": "Type"}

patched = 0
for f in SITE.rglob("*.py"):
    s = f.read_text()
    orig = s
    for b, t in MAP.items():
        s = re.sub(rf"(?<![\w.]){b}\[", f"{t}[", s)
    if s == orig:
        continue

    used = sorted({t for b, t in MAP.items() if re.search(rf"\b{t}\[", s)})
    m = re.search(r"^from typing import (.*)$", s, re.M)
    if m:
        have = {x.strip() for x in m.group(1).split(",")}
        missing = [t for t in used if t not in have]
        if missing:
            s = s[: m.end(1)] + ", " + ", ".join(missing) + s[m.end(1):]
    else:
        # Insert after any from __future__ lines (must stay first), else
        # before the first import, else at the top.
        lines = s.split("\n")
        idx = 0
        for i, line in enumerate(lines):
            if line.startswith("from __future__"):
                idx = i + 1
            elif line.startswith(("import ", "from ")) and idx == 0:
                idx = i
                break
        lines.insert(idx, f"from typing import {', '.join(used)}")
        s = "\n".join(lines)
    f.write_text(s)
    patched += 1

# __future__ imports must stay at file top — a typing import inserted
# above them is a SyntaxError; reorder defensively.
reordered = 0
for f in SITE.rglob("*.py"):
    s = f.read_text()
    m = re.search(r"^from __future__ import annotations\n?", s, re.M)
    if m and m.start() > 0:
        s = s[: m.start()] + s[m.end():]
        s = m.group(0) + s
        f.write_text(s)
        reordered += 1

print(f"comfy-kitchen typing patch: {patched} files patched, {reordered} reordered")
