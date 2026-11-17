#!/usr/bin/env python3
"""win_spawn_audit.py — find Windows-interop spawns that flash a console
window on the Windows desktop.

Every call to a Windows binary from WSL (powershell.exe, *.exe under
/mnt/c, wsl.exe, adb.exe, docker.exe, cmd.exe) makes Windows allocate a
console — visible as a CMD/PowerShell popup. Cron scripts that do this
unconditionally every pass are the #1 popup source (~500/day observed
2026-11-16).

Usage:
    win_spawn_audit.py                 # audit all WSL crontab scripts
    win_spawn_audit.py --paths a.py b.sh DIR/...
    win_spawn_audit.py --json          # machine-readable

Classification per hit:
  HOT   — invoked unconditionally on the script's main path (fix: batch,
          stamp-throttle, or share state from a batched probe)
  GUARD — behind a conditional / rare remediation branch (acceptable)
  DEAD  — inside a function never called (acceptable, note for cleanup)

Heuristic: a spawn is GUARD if every reference sits inside a function
whose name suggests a rare path (heal/escalate/restart/recover/alert/
janitor/dismiss/reap/fallback) or after an early-return guard on the
same line-block. Everything else on the cron path is HOT.
"""
import argparse
import json
import os
import re
import subprocess
import sys

INTEROP_RE = re.compile(
    r"(powershell\.exe|cmd\.exe|wsl\.exe|adb\.exe|docker\.exe|"
    r"/mnt/[cd]/[^\s\"']*\.exe|[A-Za-z]:\\\\[^\s\"']*\.exe)"
)
RARE_FN_RE = re.compile(
    r"def (_?\w*(heal|escalat|recover|restart|respawn|reap|dismiss|"
    r"janitor|fallback|repair|remediat|alert|shutdown|relaunch|drive)\w*)\s*\(",
    re.I,
)
CRON_RE = re.compile(r"^\s*([^#]+?)\s+(/[^\s]+\.(?:py|sh|bash))\s*(.*)$")


def cron_scripts():
    try:
        out = subprocess.run(["crontab", "-l"], capture_output=True,
                             text=True, timeout=10).stdout
    except Exception:
        return []
    rows = []
    for line in out.splitlines():
        m = CRON_RE.match(line)
        if m and os.path.exists(m.group(2)):
            rows.append((m.group(1).strip(), m.group(2), m.group(3)))
    return rows


def cadence(schedule):
    field = schedule.split()[0] if schedule else ""
    if field.startswith("*/"):
        return f"every {field[2:]}min"
    if field == "*":
        return "every minute"
    if re.fullmatch(r"\d+,\d+", field):
        return f"{field}min-mark"
    if re.fullmatch(r"\d+-\d+/\d+", field):
        return f"range {field}"
    return field or "?"


ASSIGN_RE = re.compile(r"^\s*(?:export\s+)?[A-Za-z_]\w*\s*=")
PURE_STR_RE = re.compile(r'^\s*["\'][^"\']*\.exe["\']\s*,?\s*$')


def is_spawn_line(stripped):
    """True when the line plausibly EXECUTES the interop binary rather than
    naming it (path constants, candidate lists, docstrings)."""
    if ASSIGN_RE.match(stripped):
        return False
    if PURE_STR_RE.match(stripped):
        return False
    # invocation markers: exe followed by args, $VAR command position,
    # subprocess/os.system wrappers
    return bool(re.search(
        r"\.exe[\"']?\s+[-\w]|\"\$\w*(?:EXE|ADB|DOCKER|PS|WSL)\w*\"|"
        r"subprocess\.(?:run|Popen|call|check_output)\s*\(|"
        r"os\.system\s*\(|`[^`]*\.exe|powershell\.exe\s+-|"
        r"wsl\.exe\s+-|adb\.exe\s|docker\.exe\s", stripped))


def python_string_lines(lines):
    """Line numbers (1-based) inside triple-quoted strings — crude but
    effective for module docstrings."""
    inside = set()
    in_str = False
    quote = None
    for i, ln in enumerate(lines, 1):
        # count unescaped triple-quote occurrences
        for q in ('"""', "'''"):
            start = 0
            while True:
                j = ln.find(q, start)
                if j < 0:
                    break
                if not in_str:
                    in_str, quote = True, q
                    # content after opener on same line is string too
                    inside.add(i)
                elif quote == q:
                    in_str, quote = False, None
                start = j + 3
        if in_str:
            inside.add(i)
    return inside


def classify(path):
    """Return list of {line, snippet, klass}."""
    try:
        lines = open(path, errors="replace").read().splitlines()
    except OSError as e:
        return [{"error": str(e)}]
    str_lines = (python_string_lines(lines)
                 if path.endswith(".py") else set())
    # map line -> enclosing def name
    fn_starts = []  # (lineno, name)
    for i, ln in enumerate(lines, 1):
        m = re.match(r"\s*def (\w+)\s*\(", ln)
        if m:
            fn_starts.append((i, m.group(1)))

    def enclosing(i):
        name = "<module>"
        for ln_no, fn in fn_starts:
            if ln_no < i:
                name = fn
            else:
                break
        return name

    hits = []
    for i, ln in enumerate(lines, 1):
        if not INTEROP_RE.search(ln):
            continue
        if i in str_lines:
            continue
        stripped = ln.strip()
        # skip comments and non-invocation references
        if stripped.startswith("#") or not is_spawn_line(stripped):
            continue
        fn = enclosing(i)
        if fn != "<module>" and RARE_FN_RE.match(f"def {fn}("):
            klass = "GUARD"
        elif fn == "<module>":
            # module level = import-time or top-level script body
            klass = "HOT"
        else:
            klass = "REVIEW"
        hits.append({"line": i, "fn": fn, "klass": klass,
                     "snippet": stripped[:110]})
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--paths", nargs="*", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.paths:
        targets = []
        for p in args.paths:
            if os.path.isdir(p):
                for root, _dirs, files in os.walk(p):
                    for fn in files:
                        if fn.endswith((".py", ".sh", ".bash")):
                            targets.append(os.path.join(root, fn))
            elif os.path.isfile(p):
                targets.append(p)
        jobs = [("?", t, "") for t in sorted(set(targets))]
    else:
        jobs = cron_scripts()

    report = []
    for sched, path, tail in jobs:
        hits = classify(path)
        hot = [h for h in hits if h.get("klass") == "HOT"]
        if not hits:
            continue
        report.append({
            "script": path, "schedule": sched,
            "cadence": cadence(sched), "tail": tail.strip()[:60],
            "hits": hits,
            "hot": len(hot),
        })

    if args.json:
        print(json.dumps(report, indent=1))
        return 0

    report.sort(key=lambda r: (-r["hot"], r["script"]))
    total_hot = sum(r["hot"] for r in report)
    print(f"{'script':<72} {'cadence':<14} HOT  hits")
    print("-" * 100)
    for r in report:
        flag = " <<<" if r["hot"] else ""
        print(f"{r['script']:<72} {r['cadence']:<14} "
              f"{r['hot']:>3}  {len(r['hits']):>4}{flag}")
    print("-" * 100)
    print(f"{len(report)} scripts with interop refs, {total_hot} HOT hits")
    print("\nHOT hits (unconditional spawns — fix these):")
    for r in report:
        for h in r["hits"]:
            if h.get("klass") == "HOT":
                print(f"  {r['script']}:{h['line']}  {h['snippet']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
