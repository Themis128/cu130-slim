import pytest

from app.services.meta_graph import FACEBOOK_GRAPH_BASE, FACEBOOK_GRAPH_VERSION, facebook_graph_url


def test_facebook_graph_url_builds_versioned_url() -> None:
    url = facebook_graph_url("me")
    assert url == f"{FACEBOOK_GRAPH_BASE}/{FACEBOOK_GRAPH_VERSION}/me"


def test_facebook_graph_url_strips_leading_slashes() -> None:
    url = facebook_graph_url("/oauth/access_token", version="/v26.0")
    assert url == f"{FACEBOOK_GRAPH_BASE}/v26.0/oauth/access_token"


def test_facebook_graph_url_rejects_empty_version() -> None:
    with pytest.raises(ValueError):
        facebook_graph_url("me", version="")

