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

    Uses ``os.path.realpath`` for normalization and ``Path.relative_to`` as the
    guard. CodeQL's path-injection taint tracker recognises the
    ``ValueError``-on-traversal path as a sanitizer.
    """
    base = pathlib.Path(os.path.realpath(root))
    joined = pathlib.Path(base, *parts)
    target = pathlib.Path(os.path.realpath(joined))
    try:
        target.relative_to(base)
    except ValueError as exc:
        if target != base:
            raise ValueError(f"Resolved path {target!r} escapes root {base!r}") from exc
    return target
