"""Unit tests for app/services/twitter_profile.py (OAuth1Session backend)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services.twitter_profile import TwitterProfileError, TwitterProfileService


def _resp(status=200, body=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body if body is not None else {}
    return r


def _svc(monkeypatch, get=None, post=None):
    sess = MagicMock()
    sess.get.return_value = get or _resp(200, _user_dict())
    sess.post.return_value = post or _resp(200, _user_dict())
    mod = MagicMock()
    mod.OAuth1Session = MagicMock(return_value=sess)
    monkeypatch.setattr("requests_oauthlib.OAuth1Session", mod.OAuth1Session)
    return sess


def _user_dict():
    return {
        "id": 123,
        "screen_name": "TBaltzakis",
        "name": "Themis",
        "description": "bio",
        "location": "Athens",
        "url": "https://cloudless.gr",
        "profile_image_url_https": "https://x/pic_normal.jpg",
        "profile_banner_url": "https://x/banner.jpg",
        "followers_count": 42,
        "verified": True,
    }


def test_get_profile(monkeypatch):
    _svc(monkeypatch)
    p = TwitterProfileService("k", "s", "t", "ts").get_profile()
    assert p["username"] == "TBaltzakis"
    assert p["profile_pic_url"] == "https://x/pic.jpg"
    assert p["cover_url"] == "https://x/banner.jpg"
    assert p["followers"] == 42 and p["is_verified"] is True


def test_get_profile_no_banner(monkeypatch):
    u = _user_dict()
    del u["profile_banner_url"]
    u["profile_image_url_https"] = None
    _svc(monkeypatch, get=_resp(200, u))
    p = TwitterProfileService("k", "s", "t", "ts").get_profile()
    assert p["cover_url"] is None and p["profile_pic_url"] is None


def test_get_profile_auth_failure(monkeypatch):
    sess = MagicMock()
    sess.get.side_effect = RuntimeError("boom")
    monkeypatch.setattr("requests_oauthlib.OAuth1Session", MagicMock(return_value=sess))
    with pytest.raises(TwitterProfileError) as e:
        TwitterProfileService("k", "s", "t", "ts").get_profile()
    assert e.value.status_code == 401


def test_get_profile_non_200(monkeypatch):
    _svc(monkeypatch, get=_resp(401))
    with pytest.raises(TwitterProfileError) as e:
        TwitterProfileService("k", "s", "t", "ts").get_profile()
    assert e.value.status_code == 401


def test_update_profile(monkeypatch):
    sess = _svc(monkeypatch)
    svc = TwitterProfileService("k", "s", "t", "ts")
    out = svc.update_profile(name="N", description="D", location="L", url="U")
    assert out["success"] is True
    assert sorted(out["updated_fields"]) == ["description", "location", "name", "url"]
    assert sess.post.call_args.kwargs["data"] == {"name": "N", "description": "D", "location": "L", "url": "U"}


def test_update_profile_partial_and_empty(monkeypatch):
    sess = _svc(monkeypatch)
    svc = TwitterProfileService("k", "s", "t", "ts")
    out = svc.update_profile(name="Only")
    assert sess.post.call_args.kwargs["data"] == {"name": "Only"}
    assert out["updated_fields"] == ["name"]
    with pytest.raises(TwitterProfileError) as e:
        svc.update_profile()
    assert e.value.status_code == 400


def test_update_profile_api_error(monkeypatch):
    _svc(monkeypatch, post=_resp(500))
    with pytest.raises(TwitterProfileError) as e:
        TwitterProfileService("k", "s", "t", "ts").update_profile(name="x")
    assert e.value.status_code == 500


def test_update_profile_exception(monkeypatch):
    sess = MagicMock()
    sess.post.side_effect = RuntimeError("net")
    monkeypatch.setattr("requests_oauthlib.OAuth1Session", MagicMock(return_value=sess))
    with pytest.raises(TwitterProfileError) as e:
        TwitterProfileService("k", "s", "t", "ts").update_profile(name="x")
    assert e.value.status_code == 500


def test_update_profile_image(monkeypatch):
    sess = _svc(monkeypatch)
    out = TwitterProfileService("k", "s", "t", "ts").update_profile_image(b"\xff\xd8img")
    assert out["success"] is True and out["updated_fields"] == ["profile_picture"]
    assert sess.post.call_args.kwargs["files"]["image"][0] == "profile.jpg"


def test_update_profile_image_error(monkeypatch):
    _svc(monkeypatch, post=_resp(500))
    with pytest.raises(TwitterProfileError) as e:
        TwitterProfileService("k", "s", "t", "ts").update_profile_image(b"x")
    assert e.value.status_code == 500


def test_update_profile_banner(monkeypatch):
    sess = _svc(monkeypatch)
    out = TwitterProfileService("k", "s", "t", "ts").update_profile_banner(b"img")
    assert out["success"] is True and out["updated_fields"] == ["banner"]
    assert sess.post.call_args.kwargs["files"]["banner"][0] == "banner.jpg"


def test_update_profile_banner_error(monkeypatch):
    _svc(monkeypatch, post=_resp(502))
    with pytest.raises(TwitterProfileError) as e:
        TwitterProfileService("k", "s", "t", "ts").update_profile_banner(b"x")
    assert e.value.status_code == 500


def test_missing_oauthlib(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "requests_oauthlib", None)
    with pytest.raises(TwitterProfileError) as e:
        TwitterProfileService("k", "s", "t", "ts")
    assert e.value.status_code == 500
    assert "requests-oauthlib" in e.value.detail
