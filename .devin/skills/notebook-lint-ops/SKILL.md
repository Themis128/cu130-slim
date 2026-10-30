# notebook-lint-ops

Notebook-aware syntax lint for `/notebooks/reports/*.ipynb` — finds the
f-string brace-backslash class (`{'\\n'.join(x)}`) that kills Python <3.12
executions, without the "'await' outside function" false positives a plain
`compile()` produces (Jupyter top-level await is legal).

## Tool

```bash
python3 .devin/skills/notebook-lint-ops/scripts/nb_lint.py <dir|file>
python3 .devin/skills/notebook-lint-ops/scripts/nb_lint.py --fix <file.ipynb>
```

- Each code cell compiles with `ast.PyCF_ALLOW_TOP_LEVEL_AWAIT` — only real
  SyntaxErrors surface, with cell index + offending line.
- `--fix` hoists the classic `{'\\n'.join(...)}` expression out of the braces.

## The 2026-10-30 lesson

The Slack "notebook_reports failed" errors were NOT notebook syntax — the
trace named `content_sanitizer.py:124`: an `r'\w+'` regex inside f-string
braces. Any service module imported inside a notebook cell fails the whole
run the same way — **lint service imports, not just notebook cells**. When a
PapermillExecutionError surfaces, read the wrapped traceback: it names the
true file:line.
