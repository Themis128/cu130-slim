"""Remote media sources — browse + import photos/files into the library.

Two providers today:

- **Nextcloud** (cloud.cloudless.gr) — WebDAV PROPFIND listing + GET
  download; creds already in settings (NEXTCLOUD_DAV_URL /
  NEXTCLOUD_USERNAME / NEXTCLOUD_APP_PASSWORD).
- **Google Drive** — files.list + files.get?alt=media. Needs a refresh
  token carrying ``drive.readonly`` — minted once via
  ``gdrive_connect_url()`` and stored as GOOGLE_DRIVE_REFRESH_TOKEN.
"""

from __future__ import annotations

import logging
import mimetypes
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
_DAV_NS = "{DAV:}"


class MediaSourceError(RuntimeError):
    pass


@dataclass
class RemoteFile:
    id: str  # provider id: dav path or drive file_id
    name: str
    mime_type: str
    size: int
    is_dir: bool


def _nc_client() -> httpx.AsyncClient:
    s = get_settings()
    return httpx.AsyncClient(
        auth=(s.NEXTCLOUD_USERNAME, s.NEXTCLOUD_APP_PASSWORD),
        timeout=httpx.Timeout(60.0, connect=15.0),
        follow_redirects=True,
        headers={"User-Agent": "socialauto-media-sources/1.0"},
    )


async def nextcloud_list(path: str = "") -> list[RemoteFile]:
    """PROPFIND Depth:1 — list one folder level."""
    s = get_settings()
    base = s.NEXTCLOUD_DAV_URL.rstrip("/")
    rel = "/".join(urllib.parse.quote(p) for p in path.strip("/").split("/") if p)
    url = f"{base}/{rel}".rstrip("/")
    body = """<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop>
        <d:getcontenttype/><d:getcontentlength/><d:resourcetype/>
        </d:prop></d:propfind>"""
    async with _nc_client() as c:
        r = await c.request("PROPFIND", url, content=body, headers={"Depth": "1"})
        if r.status_code not in (207, 200):
            raise MediaSourceError(f"PROPFIND {r.status_code}: {r.text[:200]}")
        root = ET.fromstring(r.text)
        files: list[RemoteFile] = []
        for resp in root.findall(f"{_DAV_NS}response"):
            href = resp.findtext(f"{_DAV_NS}href") or ""
            prop = resp.find(f"{_DAV_NS}propstat/{_DAV_NS}prop")
            if prop is None:
                continue
            is_dir = prop.find(f"{_DAV_NS}resourcetype/{_DAV_NS}collection") is not None
            mime = prop.findtext(f"{_DAV_NS}getcontenttype") or ""
            size = int(prop.findtext(f"{_DAV_NS}getcontentlength") or 0)
            # href is the absolute dav path — strip the dav base to a rel path
            dec = urllib.parse.unquote(href)
            rel_path = dec.split("/remote.php/dav/files/", 1)[-1]
            rel_path = rel_path.split("/", 1)[-1] if "/" in rel_path else rel_path
            name = rel_path.rstrip("/").rsplit("/", 1)[-1]
            if rel_path.rstrip("/") == rel.rstrip("/") or not name:
                continue  # the folder itself
            files.append(RemoteFile(id=rel_path, name=name, mime_type=mime, size=size, is_dir=is_dir))
        return files


async def nextcloud_download(path: str) -> tuple[bytes, str]:
    """GET a file's bytes; returns (bytes, mime)."""
    s = get_settings()
    base = s.NEXTCLOUD_DAV_URL.rstrip("/")
    rel = "/".join(urllib.parse.quote(p) for p in path.strip("/").split("/") if p)
    async with _nc_client() as c:
        r = await c.get(f"{base}/{rel}")
        if r.status_code != 200:
            raise MediaSourceError(f"GET {path}: {r.status_code}")
        mime = r.headers.get("content-type", "").split(";")[0] or mimetypes.guess_type(path)[0] or "application/octet-stream"
        return r.content, mime


# ── Google Drive ──────────────────────────────────────────────────────────────


def gdrive_connect_url(redirect_uri: str) -> str:
    """Mint the OAuth consent URL for drive.readonly (+ keep photos scopes)."""
    s = get_settings()
    scopes = " ".join(
        [
            DRIVE_SCOPE,
            "https://www.googleapis.com/auth/photospicker.mediaitems.readonly",
            "https://www.googleapis.com/auth/photoslibrary.appendonly",
            "https://www.googleapis.com/auth/photoslibrary.readonly.appcreateddata",
            "https://www.googleapis.com/auth/photoslibrary.edit.appcreateddata",
        ]
    )
    q = urllib.parse.urlencode(
        {
            "client_id": s.GOOGLE_PHOTOS_CLIENT_ID,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": scopes,
            "access_type": "offline",
            "prompt": "consent",
        }
    )
    return f"https://accounts.google.com/o/oauth2/v2/auth?{q}"


async def gdrive_exchange_code(code: str, redirect_uri: str) -> str:
    """Swap auth code → refresh token covering drive+photos scopes."""
    s = get_settings()
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": s.GOOGLE_PHOTOS_CLIENT_ID,
                "client_secret": s.GOOGLE_PHOTOS_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
        )
    data = r.json()
    if "refresh_token" not in data:
        raise MediaSourceError(f"no refresh_token in response: {list(data)}")
    return data["refresh_token"]


async def _gdrive_token() -> str:
    s = get_settings()
    rt = (getattr(s, "GOOGLE_DRIVE_REFRESH_TOKEN", "") or s.GOOGLE_PHOTOS_REFRESH_TOKEN or "").strip()
    if not rt:
        raise MediaSourceError("no Google refresh token — run gdrive connect")
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": s.GOOGLE_PHOTOS_CLIENT_ID,
                "client_secret": s.GOOGLE_PHOTOS_CLIENT_SECRET,
                "refresh_token": rt,
                "grant_type": "refresh_token",
            },
        )
    tok = r.json().get("access_token")
    if not tok:
        raise MediaSourceError(f"token refresh failed: {r.text[:200]}")
    return tok


async def gdrive_list(query: str = "", folder_id: str | None = None, page_size: int = 50) -> list[RemoteFile]:
    """List Drive files (images/media by default)."""
    tok = await _gdrive_token()
    clauses = ["trashed=false"]
    if folder_id:
        clauses.append(f"'{folder_id}' in parents")
    elif query:
        clauses.append(f"name contains '{query}'")
    else:
        clauses.append("(mimeType contains 'image/' or mimeType contains 'video/' or mimeType contains 'application/vnd.google-apps.folder')")
    params = {
        "q": " and ".join(clauses),
        "pageSize": page_size,
        "fields": "files(id,name,mimeType,size)",
        "orderBy": "modifiedTime desc",
    }
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get("https://www.googleapis.com/drive/v3/files", params=params, headers={"Authorization": f"Bearer {tok}"})
        if r.status_code != 200:
            raise MediaSourceError(f"drive list {r.status_code}: {r.text[:200]}")
        return [
            RemoteFile(
                id=f["id"],
                name=f["name"],
                mime_type=f.get("mimeType", ""),
                size=int(f.get("size") or 0),
                is_dir=f.get("mimeType", "").endswith("folder"),
            )
            for f in r.json().get("files", [])
        ]


async def gdrive_download(file_id: str) -> tuple[bytes, str]:
    """Download a Drive file's bytes."""
    tok = await _gdrive_token()
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as c:
        meta = (
            await c.get(f"https://www.googleapis.com/drive/v3/files/{file_id}", params={"fields": "mimeType,name"}, headers={"Authorization": f"Bearer {tok}"})
        ).json()
        r = await c.get(f"https://www.googleapis.com/drive/v3/files/{file_id}", params={"alt": "media"}, headers={"Authorization": f"Bearer {tok}"})
        if r.status_code != 200:
            raise MediaSourceError(f"drive get {file_id}: {r.status_code}")
        return r.content, meta.get("mimeType") or "application/octet-stream"
