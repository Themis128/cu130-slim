#!/usr/bin/env python3
"""Beat-schedule ↔ module-existence gate.

Parses celery_app.py's beat_schedule + include list WITHOUT importing it
(no side effects, no deps). Asserts every scheduled task's dotted path maps
to a real module file that defines the named function — catches the
incomplete-merge class where a beat ref lands before its task module.

Exit 0 = every referenced module+function exists. Exit 1 = missing.
"""

import ast
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
CELERY_APP = BACKEND / "app" / "worker" / "celery_app.py"
APP_ROOT = BACKEND / "app"


def _str(node) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _collect() -> tuple[set[str], set[str]]:
    """Return (beat_task_paths, include_modules) as dotted strings."""
    tree = ast.parse(CELERY_APP.read_text(), filename=str(CELERY_APP))
    tasks: set[str] = set()
    includes: set[str] = set()

    def task_paths(node) -> None:
        """Walk an AST subtree collecting every ``"task": "dotted.path"`` pair."""
        for n in ast.walk(node):
            if isinstance(n, ast.Dict):
                for k, v in zip(n.keys, n.values):
                    if _str(k) == "task":
                        s = _str(v)
                        if s:
                            tasks.add(s)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = getattr(fn, "attr", getattr(fn, "id", ""))
            if name == "Celery":
                for kw in node.keywords:
                    if kw.arg == "include" and isinstance(kw.value, (ast.List, ast.Tuple)):
                        for elt in kw.value.elts:
                            s = _str(elt)
                            if s:
                                includes.add(s)
            for kw in node.keywords:
                if kw.arg == "beat_schedule":
                    task_paths(kw.value)
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "beat_schedule":
                    task_paths(node.value)
                if isinstance(t, ast.Attribute) and t.attr == "beat_schedule":
                    task_paths(node.value)
    return tasks, includes


def _module_file(dotted: str) -> Path | None:
    parts = dotted.split(".")
    for n in range(len(parts), 1, -1):
        mod = APP_ROOT.joinpath(*parts[1:n])
        f = mod.with_suffix(".py")
        if f.exists():
            return f
    return None


def main() -> int:
    tasks, includes = _collect()
    errors: list[str] = []
    for path in sorted(tasks):
        *mod_parts, func = path.split(".")
        mod_file = _module_file(".".join(mod_parts))
        if mod_file is None:
            errors.append(f"beat task {path}: module {'.'.join(mod_parts)} has no file")
            continue
        tree = ast.parse(mod_file.read_text(), filename=str(mod_file))
        defs = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        if func not in defs:
            errors.append(f"beat task {path}: {mod_file.name} has no def {func}")
    for mod in sorted(includes):
        if _module_file(mod) is None:
            errors.append(f"include {mod}: no module file")
    if errors:
        for e in errors:
            print(f"::error::{e}")
        return 1
    print(f"beat gate: {len(tasks)} scheduled tasks, {len(includes)} includes — all modules exist")
    return 0


if __name__ == "__main__":
    sys.exit(main())
