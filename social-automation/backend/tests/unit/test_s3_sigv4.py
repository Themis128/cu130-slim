"""Unit tests for app/services/s3_sigv4.py — pure SigV4 signer + S3LiteClient.

urlopen is stubbed at the module boundary; signing logic itself is real.
"""

from __future__ import annotations

import io
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

import app.services.s3_sigv4 as s3

# ── pure helpers ────────────────────────────────────────────────────


def test_hash_and_hmac():
    assert s3._hash(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert len(s3._hmac_sha256(b"k", "msg")) == 32


def test_uri_encode():
    assert s3._uri_encode("a/b") == "a%2Fb"
    assert s3._uri_encode("a/b", encode_slash=False) == "a/b"
    assert s3._uri_encode("a b+c") == "a%20b%2Bc"


def test_signing_key_deterministic():
    k1 = s3._signing_key("secret", "20260101", "auto")
    k2 = s3._signing_key("secret", "20260101", "auto")
    assert k1 == k2 and len(k1) == 32
    assert s3._signing_key("secret", "20260102", "auto") != k1


def test_canonical_query_sorted():
    q = s3._canonical_query({"b": "2", "a": "1"})
    assert q == "a=1&b=2"
    q2 = s3._canonical_query({"k": "a b"})
    assert q2 == "k=a%20b"


def test_canonical_headers():
    block, signed = s3._canonical_headers({"Host": "h", "X-Amz-Date": "  d  "})
    assert "host:h\n" in block and "x-amz-date:d\n" in block
    assert signed == "host;x-amz-date"


# ── presign_url ─────────────────────────────────────────────────────


def test_presign_url_structure():
    url = s3.presign_url(
        "PUT", "https://acct.r2.cloudflarestorage.com/bucket/key.bin",
        "AKID", "SECRET", expires=600,
    )
    parts = urlsplit(url)
    assert parts.path == "/bucket/key.bin"
    q = parse_qs(parts.query)
    assert q["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
    assert q["X-Amz-Expires"] == ["600"]
    assert q["X-Amz-Credential"][0].startswith("AKID/")
    assert "/auto/s3/aws4_request" in q["X-Amz-Credential"][0]
    assert "X-Amz-Signature" in q
    assert len(q["X-Amz-Signature"][0]) == 64


def test_presign_url_signed_headers_with_extra():
    url = s3.presign_url(
        "GET", "https://h/b/k", "A", "S",
        headers={"Content-Type": "text/plain"},
    )
    q = parse_qs(urlsplit(url).query)
    assert "content-type" in q["X-Amz-SignedHeaders"][0]
    assert "host" in q["X-Amz-SignedHeaders"][0]


# ── S3LiteClient ────────────────────────────────────────────────────


def _client():
    return s3.S3LiteClient("http://minio:9000", "AKID", "SECRET")


class _FakeResp:
    def __init__(self, status=200, headers=None, body=b""):
        self.status = status
        self.headers = headers or {}
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body


def test_request_rejects_unsafe_names():
    c = _client()
    with pytest.raises(s3.S3Error) as e:
        c.request("GET", "bad bucket?")
    assert e.value.status == 400
    with pytest.raises(s3.S3Error):
        c.request("GET", "ok-bucket", "key/with space?")


def test_request_success_signs_and_sends():
    c = _client()
    with patch.object(s3, "urlopen", return_value=_FakeResp(200, {"ETag": '"abc"'}, b"data")) as uo:
        status, headers, body = c.request("PUT", "bkt", "k", body=b"payload",
                                          content_type="text/plain",
                                          metadata={"Author": "x"})
    assert status == 200 and body == b"data"
    req = uo.call_args.args[0]
    assert "Authorization" in req.headers
    assert "AWS4-HMAC-SHA256 Credential=AKID/" in req.headers["Authorization"]
    assert "x-amz-meta-author" in {h.lower() for h in req.headers.keys()}


def test_request_http_error_raises_s3error():
    c = _client()
    err = HTTPError("http://x", 404, "nf", {}, io.BytesIO(b"not found"))
    with patch.object(s3, "urlopen", side_effect=err):
        with pytest.raises(s3.S3Error) as e:
            c.request("GET", "bkt", "missing")
    assert e.value.status == 404
    assert "not found" in str(e.value)


def test_object_ops():
    c = _client()
    with patch.object(s3, "urlopen", return_value=_FakeResp(200, {"etag": '"e1"'}, b"content")) as uo:
        c.head_bucket("b")
        c.create_bucket("b")
        etag = c.put_object("b", "k", b"d", "text/plain")
        assert etag == "e1"
        assert c.get_object("b", "k") == b"content"
        c.head_object("b", "k")
        c.delete_object("b", "k")
    methods = [c2.args[0].get_method() for c2 in uo.call_args_list]
    assert methods == ["HEAD", "PUT", "PUT", "GET", "HEAD", "DELETE"]


def test_url_and_endpoint_parsing():
    c = s3.S3LiteClient("https://s3.example.com", "A", "S", region="eu")
    assert c.scheme == "https" and c.host == "s3.example.com" and c.region == "eu"
    c2 = s3.S3LiteClient("minio:9000", "A", "S")  # no scheme → http
    assert c2.scheme == "http"
    assert c._url("b", "k") == "https://s3.example.com/b/k"
    assert c._url("b") == "https://s3.example.com/b"
