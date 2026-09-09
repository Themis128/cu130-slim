"""Shared helpers for safe logging of untrusted / user-derived text."""

from __future__ import annotations

import re


def sanitize_log_text(text: str, max_len: int = 400) -> str:
    """Strip newlines and control chars so log lines cannot be forged.

    Replaces CR/LF with escaped forms and removes other C0 control characters
    (except tab). Truncates to ``max_len``.
    """
    cleaned = str(text).replace("\n", "\\n").replace("\r", "\\r")
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", cleaned)
    return cleaned[:max_len]
