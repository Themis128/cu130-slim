#!/usr/bin/env python3
"""Trim consent-submit-rejected scopes from a staged LinkedIn consent URL.

Some scopes pass the authorize-URL probe but LinkedIn rejects them at consent
submit with ?error=unauthorized_scope_error&error_description=Scope+"X"+...
Run this after each failed click to strip the named scope and retry — until
the URL lands on the callback with a code (or a login wall).

Usage:
  li_consent_trim.py <url-file> <scope> [<scope> ...]   # strip scopes in place
  li_consent_trim.py <url-file> --show                  # print current scope list
  li_consent_trim.py <url-file> --from-error            # strip scope named in the
                                                      # file's error_description
"""
from __future__ import annotations

import sys
import urllib.parse


def _read(path: str) -> str:
    return open(path, encoding="utf-8").read().strip()


def _split(url: str) -> tuple[urllib.parse.SplitResult, list]:
    parts = urllib.parse.urlsplit(url)
    q = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    return parts, q


def _scopes(q: list) -> list[str]:
    for k, v in q:
        if k == "scope":
            return v.split()
    return []


def _write(path: str, parts, q: list, scopes: list[str]) -> None:
    q = [(k, " ".join(scopes) if k == "scope" else v) for k, v in q]
    url = urllib.parse.urlunsplit(parts._replace(
        query=urllib.parse.urlencode(q)))
    with open(path, "w", encoding="utf-8") as f:
        f.write(url + "\n")


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    path = sys.argv[1]
    url = _read(path)
    parts, q = _split(url)
    scopes = _scopes(q)
    if not scopes:
        print("no scope= param in URL")
        return 1

    args = sys.argv[2:]
    if args == ["--show"]:
        print(" ".join(scopes))
        return 0

    if args == ["--from-error"]:
        # staged file holds the *callback* URL carrying the rejection
        err_desc = ""
        for k, v in q:
            if k == "error_description":
                err_desc = v
        # LinkedIn writes: ... Scope "X" ... / Scope+X+...
        import re
        m = re.search(r'[Ss]cope\s+"?([A-Za-z0-9_]+)"?', err_desc)
        if not m:
            print(f"no scope named in error_description: {err_desc[:200]}")
            return 1
        # after trimming, restore a fresh authorize URL — the error URL's
        # own scope param is what the retry needs cleaned
        args = [m.group(1)]

    before = list(scopes)
    for s in args:
        if s in scopes:
            scopes.remove(s)
        else:
            print(f"warn: {s} not in scope list (have: {' '.join(before)})")
    if scopes == before:
        return 1
    _write(path, parts, q, scopes)
    print(f"trimmed {' '.join(before)} -> {' '.join(scopes)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
