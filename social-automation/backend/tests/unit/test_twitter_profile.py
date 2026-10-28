"""Unit tests for app/services/twitter_profile.py.

tweepy is not installed in the test env; a fake module is injected into
sys.modules so the real service code paths run end-to-end.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest

from app.services.twitter_profile import TwitterProfileError, TwitterProfileService


def _fake_tweepy(user=None, raise_on=None):
    mod = types.ModuleType("tweepy")
    api = MagicMock()
    api.verify_credentials.return_value = user
    api.update_profile.return_value = user
    api.update_profile_image.return_value = user
    api.update_profile_banner.return_value = user
    if raise_on:
        getattr(api, raise_on).side_effect = RuntimeError("boom")
    mod.OAuth1UserHandler = MagicMock(return_value="auth")
    mod.API = MagicMock(return_value=api)
    return mod, api


def _user():
    u = MagicMock()
    u.id = 123
    u.screen_name = "TBaltzakis"
    u.name = "Themis"
    u.description = "bio"
    u.location = "Athens"
    u.url = "https://cloudless.gr"
    u.profile_image_url_https = "https://x/pic_normal.jpg"
    u.profile_banner_url = "https://x/banner.jpg"
    u.followers_count = 42
    u.verified = True
    u._json = {"raw": True}
    return u


def test_get_profile(monkeypatch):
    mod, api = _fake_tweepy(user=_user())
    monkeypatch.setitem(sys.modules, "tweepy", mod)
    svc = TwitterProfileService("k", "s", "t", "ts")
    p = svc.get_profile()
    assert p["username"] == "TBaltzakis"
    assert p["profile_pic_url"] == "https://x/pic.jpg"  # _normal stripped
    assert p["cover_url"] == "https://x/banner.jpg"
    assert p["followers"] == 42 and p["is_verified"] is True
    mod.API.assert_called_once_with("auth", wait_on_rate_limit=True)


def test_get_profile_no_banner(monkeypatch):
    u = _user()
    del u.profile_banner_url  # attribute absent
    u.profile_image_url_https = None
    mod, _ = _fake_tweepy(user=u)
    monkeypatch.setitem(sys.modules, "tweepy", mod)
    p = TwitterProfileService("k", "s", "t", "ts").get_profile()
    assert p["cover_url"] is None and p["profile_pic_url"] is None


def test_get_profile_auth_failure(monkeypatch):
    mod, _ = _fake_tweepy(user=_user(), raise_on="verify_credentials")
    monkeypatch.setitem(sys.modules, "tweepy", mod)
    with pytest.raises(TwitterProfileError) as e:
        TwitterProfileService("k", "s", "t", "ts").get_profile()
    assert e.value.status_code == 401


def test_update_profile(monkeypatch):
    mod, api = _fake_tweepy(user=_user())
    monkeypatch.setitem(sys.modules, "tweepy", mod)
    svc = TwitterProfileService("k", "s", "t", "ts")
    out = svc.update_profile(name="N", description="D", location="L", url="U")
    assert out["success"] is True
    assert sorted(out["updated_fields"]) == ["description", "location", "name", "url"]
    api.update_profile.assert_called_once_with(name="N", description="D", location="L", url="U")


def test_update_profile_partial_and_empty(monkeypatch):
    mod, api = _fake_tweepy(user=_user())
    monkeypatch.setitem(sys.modules, "tweepy", mod)
    svc = TwitterProfileService("k", "s", "t", "ts")
    out = svc.update_profile(name="Only")
    api.update_profile.assert_called_with(name="Only")
    assert out["updated_fields"] == ["name"]
    with pytest.raises(TwitterProfileError) as e:
        svc.update_profile()
    assert e.value.status_code == 400


def test_update_profile_api_error(monkeypatch):
    mod, _ = _fake_tweepy(user=_user(), raise_on="update_profile")
    monkeypatch.setitem(sys.modules, "tweepy", mod)
    with pytest.raises(TwitterProfileError) as e:
        TwitterProfileService("k", "s", "t", "ts").update_profile(name="x")
    assert e.value.status_code == 500


def test_update_profile_image(monkeypatch):
    mod, api = _fake_tweepy(user=_user())
    monkeypatch.setitem(sys.modules, "tweepy", mod)
    out = TwitterProfileService("k", "s", "t", "ts").update_profile_image(b"\xff\xd8img")
    assert out["success"] is True and out["updated_fields"] == ["profile_picture"]
    kw = api.update_profile_image.call_args.kwargs
    assert kw["filename"] == "profile.jpg"


def test_update_profile_image_error(monkeypatch):
    mod, _ = _fake_tweepy(user=_user(), raise_on="update_profile_image")
    monkeypatch.setitem(sys.modules, "tweepy", mod)
    with pytest.raises(TwitterProfileError) as e:
        TwitterProfileService("k", "s", "t", "ts").update_profile_image(b"x")
    assert e.value.status_code == 500


def test_update_profile_banner(monkeypatch):
    mod, api = _fake_tweepy(user=_user())
    monkeypatch.setitem(sys.modules, "tweepy", mod)
    out = TwitterProfileService("k", "s", "t", "ts").update_profile_banner(b"img")
    assert out["success"] is True and out["updated_fields"] == ["banner"]
    kw = api.update_profile_banner.call_args.kwargs
    assert kw["filename"] == "banner.jpg"


def test_update_profile_banner_error(monkeypatch):
    mod, _ = _fake_tweepy(user=_user(), raise_on="update_profile_banner")
    monkeypatch.setitem(sys.modules, "tweepy", mod)
    with pytest.raises(TwitterProfileError) as e:
        TwitterProfileService("k", "s", "t", "ts").update_profile_banner(b"x")
    assert e.value.status_code == 500


def test_missing_tweepy(monkeypatch):
    monkeypatch.setitem(sys.modules, "tweepy", None)
    with pytest.raises(TwitterProfileError) as e:
        TwitterProfileService("k", "s", "t", "ts")
    assert e.value.status_code == 500
    assert "tweepy" in e.value.detail
