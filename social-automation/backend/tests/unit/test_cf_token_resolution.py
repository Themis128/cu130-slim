"""Tests for the Cloudflare token resolution chain (2026-11-28 incident class).

Covers resolve_cf_token() in core/config.py and the Settings backfill
validator — the absent-token shadowing failure mode where a valid token
provisioned under an alternate name never reached consumers.
"""
import os
from unittest.mock import patch

from app.core.config import Settings, resolve_cf_token

CF_KEYS = ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_AI_API_TOKEN", "CLOUDFLARE_WORKERS_AI_TOKEN")


def _clean_env():
    return {k: v for k, v in os.environ.items() if k not in CF_KEYS}


class TestResolveCfToken:
    def test_canonical_first(self):
        env = _clean_env() | {
            "CLOUDFLARE_API_TOKEN": "canonical",
            "CLOUDFLARE_WORKERS_AI_TOKEN": "workers",
        }
        with patch.dict(os.environ, env, clear=True):
            assert resolve_cf_token() == "canonical"

    def test_falls_back_to_ai_api(self):
        env = _clean_env() | {"CLOUDFLARE_AI_API_TOKEN": "ai-tok"}
        with patch.dict(os.environ, env, clear=True):
            assert resolve_cf_token() == "ai-tok"

    def test_falls_back_to_workers_ai(self):
        env = _clean_env() | {"CLOUDFLARE_WORKERS_AI_TOKEN": "workers-tok"}
        with patch.dict(os.environ, env, clear=True):
            assert resolve_cf_token() == "workers-tok"

    def test_empty_when_nothing_set(self):
        with patch.dict(os.environ, _clean_env(), clear=True):
            assert resolve_cf_token() == ""

    def test_whitespace_canonical_skipped(self):
        env = _clean_env() | {
            "CLOUDFLARE_API_TOKEN": "   ",
            "CLOUDFLARE_WORKERS_AI_TOKEN": "workers-tok",
        }
        with patch.dict(os.environ, env, clear=True):
            assert resolve_cf_token() == "workers-tok"

    def test_token_is_stripped(self):
        env = _clean_env() | {"CLOUDFLARE_API_TOKEN": "  padded  "}
        with patch.dict(os.environ, env, clear=True):
            assert resolve_cf_token() == "padded"


class TestSettingsBackfill:
    def test_backfills_from_workers_ai(self):
        env = _clean_env() | {"CLOUDFLARE_WORKERS_AI_TOKEN": "workers-tok"}
        with patch.dict(os.environ, env, clear=True):
            s = Settings()
            assert s.CLOUDFLARE_API_TOKEN == "workers-tok"

    def test_backfills_from_ai_api(self):
        env = _clean_env() | {"CLOUDFLARE_AI_API_TOKEN": "ai-tok"}
        with patch.dict(os.environ, env, clear=True):
            s = Settings()
            assert s.CLOUDFLARE_API_TOKEN == "ai-tok"

    def test_never_overrides_explicit_value(self):
        env = _clean_env() | {
            "CLOUDFLARE_API_TOKEN": "explicit",
            "CLOUDFLARE_WORKERS_AI_TOKEN": "workers-tok",
        }
        with patch.dict(os.environ, env, clear=True):
            s = Settings()
            assert s.CLOUDFLARE_API_TOKEN == "explicit"

    def test_empty_when_no_tokens(self):
        with patch.dict(os.environ, _clean_env(), clear=True):
            s = Settings()
            assert s.CLOUDFLARE_API_TOKEN == ""
