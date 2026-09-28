"""Minimal AWS Signature Version 4 signer for S3-compatible stores.

Replaces boto3 for the two things the backend actually uses: presigned
PUT/GET URLs (Cloudflare R2) and basic object ops against MinIO
(head/create bucket, put/get/delete/head object). Pure stdlib — no AWS
SDK dependency, nothing contacts amazonaws.com.

Spec: https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_sigv-create-signed-request.html
R2 path-style endpoint: https://<account>.r2.cloudflarestorage.com/<bucket>/<key>
"""
from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import Request, urlopen

_SAFE = "-_.~"
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def _uri_encode(value: str, encode_slash: bool = True) -> str:
    return quote(value, safe="" if encode_slash else "/")


def _signing_key(secret: str, datestamp: str, region: str, service: str = "s3") -> bytes:
    key = _hmac_sha256(f"AWS4{secret}".encode(), datestamp)
    key = _hmac_sha256(key, region)
    key = _hmac_sha256(key, service)
    return _hmac_sha256(key, "aws4_request")


def _canonical_query(params: dict[str, str]) -> str:
    pairs = sorted(
        (_uri_encode(k), _uri_encode(v)) for k, v in params.items()
    )
    return "&".join(f"{k}={v}" for k, v in pairs)


def _canonical_headers(headers: dict[str, str]) -> tuple[str, str]:
    # Lowercase names, trimmed values, sorted by name — then emit the
    # header block and the SignedHeaders list.
    items = sorted((k.lower(), " ".join(v.split())) for k, v in headers.items())
    block = "".join(f"{k}:{v}\n" for k, v in items)
    signed = ";".join(k for k, _ in items)
    return block, signed


def presign_url(
    method: str,
    url: str,
    access_key: str,
    secret_key: str,
    region: str = "auto",
    expires: int = 3600,
    headers: dict[str, str] | None = None,
) -> str:
    """Return a SigV4 presigned URL (query-string auth) for ``url``.

    ``headers`` become signed headers — the client must send them exactly
    (mirrors boto3's Params→signed-headers behaviour, e.g. ContentType).
    """
    parts = urlsplit(url)
    host = parts.netloc
    canonical_uri = _uri_encode(parts.path or "/", encode_slash=False)

    now = datetime.now(UTC)
    amzdate = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    scope = f"{datestamp}/{region}/s3/aws4_request"

    signed_headers_map = {"host": host}
    if headers:
        signed_headers_map.update(headers)
    header_block, signed_headers = _canonical_headers(signed_headers_map)

    query = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": f"{access_key}/{scope}",
        "X-Amz-Date": amzdate,
        "X-Amz-Expires": str(expires),
        "X-Amz-SignedHeaders": signed_headers,
    }
    canonical_query = _canonical_query(query)
    canonical_request = (
        f"{method.upper()}\n{canonical_uri}\n{canonical_query}\n"
        f"{header_block}\n{signed_headers}\nUNSIGNED-PAYLOAD"
    )
    string_to_sign = (
        f"AWS4-HMAC-SHA256\n{amzdate}\n{scope}\n{_hash(canonical_request.encode())}"
    )
    signature = hmac.new(
        _signing_key(secret_key, datestamp, region),
        string_to_sign.encode(),
        hashlib.sha256,
    ).hexdigest()

    return urlunsplit(parts) + f"?{canonical_query}&X-Amz-Signature={signature}"


class S3Error(Exception):
    """HTTP error from an S3-compatible endpoint (carries status code)."""

    def __init__(self, status: int, detail: str = ""):
        super().__init__(detail or f"S3 error {status}")
        self.status = status


class S3LiteClient:
    """Signed-header S3 client over stdlib HTTP (path-style URLs)."""

    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        region: str = "us-east-1",
    ):
        parts = urlsplit(endpoint)
        self.scheme = parts.scheme or "http"
        self.host = parts.netloc
        self.access_key = access_key
        self.secret_key = secret_key
        self.region = region

    def _url(self, bucket: str, key: str | None = None) -> str:
        path = f"/{bucket}" + (f"/{key}" if key else "")
        return f"{self.scheme}://{self.host}{path}"

    def request(
        self,
        method: str,
        bucket: str,
        key: str | None = None,
        body: bytes = b"",
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        """Signed request; returns (status, headers, body). 404 → S3Error."""
        url = self._url(bucket, key)
        parts = urlsplit(url)
        canonical_uri = _uri_encode(parts.path, encode_slash=False)

        now = datetime.now(UTC)
        amzdate = now.strftime("%Y%m%dT%H%M%SZ")
        datestamp = now.strftime("%Y%m%d")
        scope = f"{datestamp}/{self.region}/s3/aws4_request"
        payload_hash = _hash(body)

        headers: dict[str, str] = {
            "host": parts.netloc,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amzdate,
        }
        if content_type:
            headers["content-type"] = content_type
        if metadata:
            for k, v in metadata.items():
                headers[f"x-amz-meta-{k.lower()}"] = v

        header_block, signed_headers = _canonical_headers(headers)
        canonical_request = (
            f"{method.upper()}\n{canonical_uri}\n\n{header_block}\n"
            f"{signed_headers}\n{payload_hash}"
        )
        string_to_sign = (
            f"AWS4-HMAC-SHA256\n{amzdate}\n{scope}\n"
            f"{_hash(canonical_request.encode())}"
        )
        signature = hmac.new(
            _signing_key(self.secret_key, datestamp, self.region),
            string_to_sign.encode(),
            hashlib.sha256,
        ).hexdigest()

        send_headers = {k: v for k, v in headers.items() if k != "host"}
        send_headers["Authorization"] = (
            f"AWS4-HMAC-SHA256 Credential={self.access_key}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        req = Request(url, data=body or None, headers=send_headers, method=method.upper())
        try:
            with urlopen(req, timeout=30) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except HTTPError as exc:
            raise S3Error(exc.code, exc.read()[:400].decode("utf-8", "replace")) from exc

    # -- object ops used by the backend ------------------------------------
    def head_bucket(self, bucket: str) -> None:
        self.request("HEAD", bucket)

    def create_bucket(self, bucket: str) -> None:
        self.request("PUT", bucket)

    def put_object(
        self,
        bucket: str,
        key: str,
        data: bytes,
        content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> str:
        _, resp_headers, _ = self.request(
            "PUT", bucket, key, body=data, content_type=content_type, metadata=metadata
        )
        return (resp_headers.get("ETag") or resp_headers.get("etag") or "").strip('"')

    def get_object(self, bucket: str, key: str) -> bytes:
        _, _, body = self.request("GET", bucket, key)
        return body

    def head_object(self, bucket: str, key: str) -> None:
        self.request("HEAD", bucket, key)

    def delete_object(self, bucket: str, key: str) -> None:
        self.request("DELETE", bucket, key)
