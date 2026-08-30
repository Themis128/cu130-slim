import os
import pathlib
import re


def safe_path_component(value: str | None, max_length: int = 128) -> str:
    """Reduce an untrusted string to a single, safe path component.

    Strips path separators, control characters and ``..`` segments so the
    result can be joined into a safe path without changing directories.
    """
    if not value:
        return "unnamed"
    value = str(value)
    # Remove path separators and control characters early.
    value = re.sub(r"[\x00-\x1f\\/]+", "_", value)
    # Replace any remaining special characters that are not filename-safe.
    value = re.sub(r"[^\w.\-]", "_", value)
    value = value.strip("._")
    if value in ("", ".", ".."):
        value = "unnamed"
    return value[:max_length]


def safe_resolve(root: str | os.PathLike, *parts: str) -> pathlib.Path:
    """Resolve ``root/parts`` and raise if it escapes ``root``.

    Uses ``os.path.realpath`` for normalization and an explicit prefix check,
    which CodeQL's path-injection query recognizes as a robust guard.
    """
    base = os.path.realpath(root)
    joined = os.path.join(base, *parts)
    target = os.path.realpath(joined)
    if target != base and not target.startswith(base + os.sep):
        raise ValueError(f"Resolved path {target!r} escapes root {base!r}")
    return pathlib.Path(target)
