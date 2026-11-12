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

import base64
import json
import re
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx

BSKY_PDS_DEFAULT = "https://bsky.social"
MAX_GRAPHEMES = 300
MAX_IMAGES = 4
MAX_IMAGE_BYTES = 2_000_000  # app.bsky.embed.images lexicon
MAX_VIDEO_BYTES = 300_000_000  # app.bsky.embed.video lexicon
IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
VIDEO_MIMES = {"video/mp4"}


class BlueskyAPIError(Exception):
    def __init__(self, status_code: int, response_text: str, url: str):
        self.status_code = status_code
        self.response_text = response_text
        self.url = url
        super().__init__(f"Bluesky API error {status_code} for {url}: {response_text[:400]}")


_URL_RE = re.compile(r"https?://[^\s\u2039\u203a\u00ab\u00bb\"'<>{}()]+")
_MENTION_RE = re.compile(r"(?<![\w@])@([a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?)+)")
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
        _facet(
            m.start(),
            m.start() + len(url),
            [
                {"$type": "app.bsky.richtext.facet#link", "uri": url},
            ],
        )
    for m in _MENTION_RE.finditer(text):
        # Placeholder did — resolved to a real DID by create_post via
        # getProfile; facets whose handle fails to resolve are dropped.
        _facet(
            m.start(),
            m.end(),
            [
                {"$type": "app.bsky.richtext.facet#mention", "did": "", "handle": m.group(1)},
            ],
        )
    for m in _HASHTAG_RE.finditer(text):
        _facet(
            m.start(),
            m.end(),
            [
                {"$type": "app.bsky.richtext.facet#tag", "tag": m.group(1)},
            ],
        )

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

    def set_session(self, access_jwt: str, refresh_jwt: str | None, did: str | None) -> None:
        """Restore a previously persisted session without an API call."""
        self._access_jwt = access_jwt
        self._refresh_jwt = refresh_jwt
        self.did = did

    def session_state(self) -> dict[str, Any]:
        """Serializable session snapshot for ``SocialAccount.meta_data``.

        AT Protocol refresh tokens are single-use and rotate on every
        ``refreshSession`` — the NEW pair returned must replace the stored
        one atomically or the next refresh fails with ExpiredToken."""
        return {
            "access_jwt": self._access_jwt,
            "refresh_jwt": self._refresh_jwt,
            "did": self.did,
            "saved_at": datetime.now(UTC).isoformat(),
        }

    @staticmethod
    def _jwt_exp(jwt: str) -> float | None:
        """Decode the ``exp`` claim without verification (lifetime check only)."""
        try:
            payload = jwt.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
        except Exception:  # noqa: BLE001
            return None

    async def refresh_session(self) -> dict[str, Any]:
        """``POST com.atproto.server.refreshSession`` — exchange the stored
        refreshJwt for a new access+refresh pair. The old refreshJwt is
        revoked the moment this succeeds; callers MUST persist
        ``session_state()`` afterwards."""
        if not self._refresh_jwt:
            raise BlueskyAPIError(0, "no refresh token — call create_session() first", self.pds_url)
        url = self._xrpc("com.atproto.server.refreshSession")
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers={"Authorization": f"Bearer {self._refresh_jwt}"})
            if resp.status_code != 200:
                raise BlueskyAPIError(resp.status_code, resp.text, url)
            data = resp.json()
        self._access_jwt = data["accessJwt"]
        self._refresh_jwt = data.get("refreshJwt", self._refresh_jwt)
        self.did = data.get("did", self.did)
        return data

    async def create_app_password(self, name: str = "socialauto") -> dict[str, Any]:
        """``POST com.atproto.server.createAppPassword`` — mint a new app
        password. Requires a FULL-account session (main password) — app
        passwords cannot create app passwords (atproto PR #826 scope rule).
        Returns ``{name, password, createdAt, privileged}``."""
        url = self._xrpc("com.atproto.server.createAppPassword")
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                headers=self._auth_headers(),
                json={"name": name},
            )
            if resp.status_code != 200:
                raise BlueskyAPIError(resp.status_code, resp.text, url)
            return resp.json()

    async def revoke_app_password(self, name: str) -> None:
        """``POST com.atproto.server.revokeAppPassword`` — drop an app
        password by name. Idempotent on the PDS (missing name still 200)."""
        url = self._xrpc("com.atproto.server.revokeAppPassword")
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                headers=self._auth_headers(),
                json={"name": name},
            )
            if resp.status_code != 200:
                raise BlueskyAPIError(resp.status_code, resp.text, url)

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


async def ensure_session(client: BlueskyClient, stored: dict[str, Any] | None) -> dict[str, Any]:
    """Authenticate ``client`` reusing a persisted session when possible.

    Order: unexpired stored accessJwt → stored refreshJwt (single-use,
    rotates — the caller MUST persist ``client.session_state()`` into
    ``account.meta_data["bsky_session"]`` afterwards) → fresh
    ``createSession``. ``createSession`` is the strictest rate-limited
    endpoint on bsky.social (observed 429s when every sync/publish
    logged in fresh), so persisting the session keeps logins rare."""
    stored = stored or {}
    access_jwt = stored.get("access_jwt")
    if access_jwt and hasattr(client, "set_session") and hasattr(client, "refresh_session"):
        exp = BlueskyClient._jwt_exp(access_jwt)
        if exp is not None and exp > time.time() + 60:
            client.set_session(access_jwt, stored.get("refresh_jwt"), stored.get("did"))
            return {"reused": True}
    if stored.get("refresh_jwt") and hasattr(client, "refresh_session") and hasattr(client, "set_session"):
        client.set_session("", stored["refresh_jwt"], stored.get("did"))
        try:
            return await client.refresh_session()
        except BlueskyAPIError:
            pass  # revoked/expired refresh token → fresh login below
    return await client.create_session()


def persist_session(account: Any, client: BlueskyClient) -> None:
    """Write ``client.session_state()`` into ``account.meta_data``.

    Refresh tokens rotate single-use — persisting the new pair is what
    keeps ``ensure_session`` off the rate-limited ``createSession``
    endpoint. Caller is responsible for committing the session."""
    session_state = getattr(client, "session_state", None)
    if session_state is None:
        return
    from sqlalchemy.orm.attributes import flag_modified

    meta = dict(account.meta_data or {})
    meta["bsky_session"] = session_state()
    account.meta_data = meta
    flag_modified(account, "meta_data")


async def rotate_app_password(account: Any, main_password: str, db: Any) -> dict[str, Any]:
    """Rotate the stored Bluesky app password using the account's MAIN
    password — the only credential scope allowed to call
    ``createAppPassword`` (app passwords cannot mint app passwords).

    Flow: createSession(main password) → createAppPassword → verify the
    new app password with its own createSession → encrypt + persist on
    ``account.access_token_enc`` + seed ``bsky_session`` → commit.
    Raises BlueskyAPIError on any PDS failure; caller decides retry/alert.
    """
    from sqlalchemy.orm.attributes import flag_modified

    from app.core.security import encrypt_token

    pds = (account.meta_data or {}).get("pds_url") or BSKY_PDS_DEFAULT
    handle = account.username or ""

    admin = BlueskyClient(handle, main_password, pds_url=pds)
    await admin.create_session()
    # PDS keys app passwords on (did, name); a fixed name collides on the
    # 2nd unattended rotation (AppPasswordNameExists). Keep <=32 chars and
    # match /^[a-zA-Z0-9._-]{4,32}$/.
    import time as _time
    new_name = f"sa-rot-{int(_time.time())}"
    meta = dict(account.meta_data or {})
    previous_name = meta.get("bsky_app_password_name")
    minted = await admin.create_app_password(name=new_name)
    new_password = minted.get("password")
    if not new_password:
        raise BlueskyAPIError(0, "createAppPassword returned no password", pds)

    # Verify before persisting — never store an untested credential.
    check = BlueskyClient(handle, new_password, pds_url=pds)
    await check.create_session()

    # Best-effort revoke of the previous app-password name so rotations
    # do not accumulate orphan credentials on the PDS.
    if previous_name and previous_name != minted.get("name"):
        try:
            await admin.revoke_app_password(previous_name)
        except BlueskyAPIError:
            pass

    account.access_token_enc = encrypt_token(new_password)
    meta["bsky_session"] = check.session_state()
    meta["bsky_app_password_name"] = minted.get("name")
    account.meta_data = meta
    flag_modified(account, "meta_data")
    await db.commit()
    return {
        "rotated": True,
        "app_password_name": minted.get("name"),
        "created_at": minted.get("createdAt"),
    }
