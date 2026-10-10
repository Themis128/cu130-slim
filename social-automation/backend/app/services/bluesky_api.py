"""Bluesky (AT Protocol) REST client.

Auth model: ``com.atproto.server.createSession`` with handle + app password
(app passwords are long-lived; a fresh session per publish avoids token
lifecycle bookkeeping). The app password is stored encrypted in
``SocialAccount.access_token_enc``; the account DID lives in
``account.account_id`` and the handle in ``username``.

Media: blobs are uploaded via ``com.atproto.repo.uploadBlob`` (raw bytes,
Content-Type = mime) then embedded — images up to 4×2MB
(``app.bsky.embed.images``), one mp4 ≤300MB (``app.bsky.embed.video``).

Facets: Bluesky does NOT auto-link URLs/mentions/hashtags — they must be
annotated as ``app.bsky.richtext.facet`` with UTF-8 *byte* offsets. Without
facets, links render as plain text and are unclickable.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx

BSKY_PDS_DEFAULT = "https://bsky.social"
MAX_GRAPHEMES = 300
MAX_IMAGES = 4
MAX_IMAGE_BYTES = 2_000_000          # app.bsky.embed.images lexicon
MAX_VIDEO_BYTES = 300_000_000        # app.bsky.embed.video lexicon
IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
VIDEO_MIMES = {"video/mp4"}


class BlueskyAPIError(Exception):
    def __init__(self, status_code: int, response_text: str, url: str):
        self.status_code = status_code
        self.response_text = response_text
        self.url = url
        super().__init__(f"Bluesky API error {status_code} for {url}: {response_text[:400]}")


_URL_RE = re.compile(r"https?://[^\s\u2039\u203a\u00ab\u00bb\"'<>{}()]+")
_MENTION_RE = re.compile(
    r"(?<![\w@])@([a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?)+)"
)
_HASHTAG_RE = re.compile(r"(?<!\w)#(\w+)")


def _normalize_pds_url(pds_url: str) -> str:
    """Validate a user-supplied PDS base URL and return a clean
    ``scheme://host[:port]`` origin. Self-hosted PDS instances are a
    supported feature, so the host itself is user-configured — but the
    scheme must be http(s) and credentials/path/query/fragment are
    rejected so the value cannot smuggle anything beyond the origin."""
    parsed = urlparse(pds_url.strip())
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ValueError(f"invalid pds_url {pds_url!r}: expected http(s)://host[:port]")
    if parsed.username or parsed.password:
        raise ValueError("pds_url must not embed credentials")
    host = parsed.hostname
    if ":" in host:
        host = f"[{host}]"  # IPv6 literal
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{host}{port}"


def build_facets(text: str) -> list[dict[str, Any]]:
    """Detect links, @mentions and #hashtags in ``text`` and return the
    ``app.bsky.richtext.facet`` records the post needs. Byte offsets are
    UTF-8 — NOT character indices."""
    encoded = text.encode("utf-8")
    facets: list[dict[str, Any]] = []

    def _facet(start_char: int, end_char: int, features: list[dict[str, str]]) -> None:
        byte_start = len(encoded[: len(text[:start_char].encode("utf-8"))])
        byte_end = len(encoded[: len(text[:end_char].encode("utf-8"))])
        facets.append(
            {
                "index": {"byteStart": byte_start, "byteEnd": byte_end},
                "features": features,
            }
        )

    for m in _URL_RE.finditer(text):
        url = m.group(0).rstrip(".,;:!?)")
        _facet(m.start(), m.start() + len(url), [
            {"$type": "app.bsky.richtext.facet#link", "uri": url},
        ])
    for m in _MENTION_RE.finditer(text):
        # Placeholder did — resolved to a real DID by create_post via
        # getProfile; facets whose handle fails to resolve are dropped.
        _facet(m.start(), m.end(), [
            {"$type": "app.bsky.richtext.facet#mention", "did": "", "handle": m.group(1)},
        ])
    for m in _HASHTAG_RE.finditer(text):
        _facet(m.start(), m.end(), [
            {"$type": "app.bsky.richtext.facet#tag", "tag": m.group(1)},
        ])

    facets.sort(key=lambda f: f["index"]["byteStart"])
    return facets


class BlueskyClient:
    """Async AT Protocol client for a single Bluesky account."""

    def __init__(
        self,
        identifier: str,
        app_password: str,
        pds_url: str = BSKY_PDS_DEFAULT,
    ):
        self.identifier = identifier
        self.app_password = app_password
        self.pds_url = _normalize_pds_url(pds_url)
        self._access_jwt: str | None = None
        self._refresh_jwt: str | None = None
        self.did: str | None = None

    def _xrpc(self, method: str) -> str:
        return f"{self.pds_url}/xrpc/{method}"

    def _auth_headers(self) -> dict[str, str]:
        if not self._access_jwt:
            raise BlueskyAPIError(0, "no session — call create_session() first", self.pds_url)
        return {"Authorization": f"Bearer {self._access_jwt}"}

    async def create_session(self) -> dict[str, Any]:
        """``POST com.atproto.server.createSession`` — authenticate with
        handle + app password; stores accessJwt/refreshJwt/did."""
        url = self._xrpc("com.atproto.server.createSession")
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                json={"identifier": self.identifier, "password": self.app_password},
            )
            if resp.status_code != 200:
                raise BlueskyAPIError(resp.status_code, resp.text, url)
            data = resp.json()
        self._access_jwt = data["accessJwt"]
        self._refresh_jwt = data.get("refreshJwt")
        self.did = data["did"]
        return data

    async def get_profile(self, actor: str | None = None) -> dict[str, Any]:
        """``GET app.bsky.actor.getProfile`` — public profile + counts."""
        url = self._xrpc("app.bsky.actor.getProfile")
        params = {"actor": actor or self.did or self.identifier}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=self._auth_headers(), params=params)
            if resp.status_code != 200:
                raise BlueskyAPIError(resp.status_code, resp.text, url)
            return resp.json()

    async def upload_blob(self, data: bytes, mime: str) -> dict[str, Any]:
        """``POST com.atproto.repo.uploadBlob`` — returns the blob ref dict."""
        url = self._xrpc("com.atproto.repo.uploadBlob")
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                url,
                headers={**self._auth_headers(), "Content-Type": mime},
                content=data,
            )
            if resp.status_code != 200:
                raise BlueskyAPIError(resp.status_code, resp.text, url)
            return resp.json()["blob"]

    async def create_post(
        self,
        text: str,
        images: list[dict[str, Any]] | None = None,
        video: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create an ``app.bsky.feed.post`` record.

        ``images``: up to 4 dicts ``{blob, alt, width, height}``.
        ``video``: dict ``{blob, alt, width, height, duration_ms?}``.
        Facets for links/mentions/hashtags are detected automatically.
        """
        if not self.did:
            raise BlueskyAPIError(0, "no session — call create_session() first", self.pds_url)

        record: dict[str, Any] = {
            "$type": "app.bsky.feed.post",
            "text": text,
            "createdAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }

        facets = build_facets(text)
        # Resolve mention handles to real DIDs; drop facets that fail.
        resolved: list[dict[str, Any]] = []
        for facet in facets:
            feat = facet["features"][0]
            if feat["$type"] == "app.bsky.richtext.facet#mention":
                try:
                    profile = await self.get_profile(feat["handle"])
                    feat["did"] = profile["did"]
                    feat.pop("handle", None)
                    resolved.append(facet)
                except Exception:
                    continue
            else:
                resolved.append(facet)
        if resolved:
            record["facets"] = resolved

        if video:
            embed: dict[str, Any] = {
                "$type": "app.bsky.embed.video",
                "video": video["blob"],
                "alt": video.get("alt") or "",
            }
            if video.get("aspectRatio"):
                embed["aspectRatio"] = video["aspectRatio"]
            record["embed"] = embed
        elif images:
            record["embed"] = {
                "$type": "app.bsky.embed.images",
                "images": [
                    {
                        "alt": img.get("alt") or "",
                        "image": img["blob"],
                        **({"aspectRatio": img["aspectRatio"]} if img.get("aspectRatio") else {}),
                    }
                    for img in images[:MAX_IMAGES]
                ],
            }

        url = self._xrpc("com.atproto.repo.createRecord")
        payload = {
            "repo": self.did,
            "collection": "app.bsky.feed.post",
            "record": record,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=self._auth_headers(), json=payload)
            if resp.status_code != 200:
                raise BlueskyAPIError(resp.status_code, resp.text, url)
            return resp.json()  # {uri, cid}
