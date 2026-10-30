#!/usr/bin/env python3
"""nb_lint — notebook-aware syntax lint for the report notebooks.

A naive `compile(src)` fails Jupyter's legal top-level `await` (spurious
"'await' outside function" noise). This lints each code cell with
PyCF_ALLOW_TOP_LEVEL_AWAIT so only REAL errors surface — the
`{'\\n'.join(x)}` f-string class that breaks Python <3.12 runs.

Usage:
  nb_lint.py <dir-or-file>          lint all *.ipynb
  nb_lint.py --fix <file>           auto-fix the classic {'\\n'.join(...)} bug
"""
import ast, glob, json, re, sys

def cell_errors(src: str, fname: str):
    try:
        compile(src, fname, "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
        return None
    except SyntaxError as e:
        return e

def lint(path):
    bad = 0
    for f in sorted(glob.glob(path if "*" in path else (path if path.endswith(".ipynb") else path.rstrip("/") + "/*.ipynb"))):
        nb = json.load(open(f))
        for i, c in enumerate(nb.get("cells", [])):
            if c.get("cell_type") != "code":
                continue
            src = "".join(c.get("source", []))
            e = cell_errors(src, f)
            if e:
                bad += 1
                print(f"{f} cell {i}: {e.msg} @line{e.lineno} → {(e.text or '').strip()[:100]}")
    print(f"{bad} real error(s)" if bad else "clean")

# the classic 3.11-breaker: f"{'\n'.join(xs)}" / f'{"\\n".join(xs)}'
FS_PAT = re.compile(r'(f["\'].*)\{(\s*)(["\'])\\n\3\.join\(([^}]+)\)(\s*)\}')

def fix(f):
    nb = json.load(open(f))
    n = 0
    for c in nb.get("cells", []):
        if c.get("cell_type") != "code":
            continue
        for j, line in enumerate(c.get("source", [])):
            m = FS_PAT.search(line)
            if m:
                # hoist the join out of the braces:  f"{X.join}" → f"{X_join}" with _sep = ... join computed before
                c["source"][j] = FS_PAT.sub(lambda m: m.group(1) + "{__sep.join(" + m.group(4) + ")}", line)
                n += 1
    if n:
        json.dump(nb, open(f, "w"), indent=1, ensure_ascii=False)
    print(f"fixed {n} f-string brace-backslash expression(s)")

if __name__ == "__main__":
    if "--fix" in sys.argv:
        fix(sys.argv[-1])
    else:
        lint(sys.argv[1] if len(sys.argv) > 1 else "/home/tbaltzakis/cu130-slim/notebooks/reports")
