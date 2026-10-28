"""Platform driver registry.

Each driver wraps the existing per-platform publishing functions and
exposes a uniform interface for publish, delete, analytics, and
follower count.
"""
from __future__ import annotations

from app.services.platforms.base import PlatformDriver, get_driver

__all__ = ["PlatformDriver", "get_driver"]
