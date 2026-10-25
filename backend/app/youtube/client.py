"""Thin, strict HTTP client for the Google OAuth and YouTube Data API endpoints this app uses.

Written from the public API documentation and **not yet exercised against the live service** (the build environment has no
Google credentials and its network policy blocks the hosts). It is tested against a mock server that follows the documented
protocol, including the awkward parts: interrupted uploads, resumed sessions, and quota and permission errors. The owner's
first real connection and ``selftest`` are its live test; docs/YOUTUBE.md says exactly what that will confirm.

Nothing here decides policy. It makes one documented call at a time and reports what Google answered.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD_API = "https://www.googleapis.com/upload/youtube/v3"

# Minimal permissions for Phase 4: upload, and read back what was uploaded. Analytics and comment scopes are requested
# later, when those features exist (incremental authorisation).
SCOPE_UPLOAD = "https://www.googleapis.com/auth/youtube.upload"
SCOPE_READONLY = "https://www.googleapis.com/auth/youtube.readonly"
PHASE4_SCOPES = (SCOPE_UPLOAD, SCOPE_READONLY)
SCOPE_ANALYTICS = "https://www.googleapis.com/auth/yt-analytics.readonly"
SCOPE_COMMENTS = "https://www.googleapis.com/auth/youtube.force-ssl"  # read comments and post replies
ANALYTICS_API = "https://youtubeanalytics.googleapis.com/v2"
# Permissions are requested in groups, only when the feature that needs them is switched on (incremental authorisation).
SCOPE_GROUPS = {"analytics": (SCOPE_ANALYTICS,), "comments": (SCOPE_COMMENTS,)}

CHUNK_MULTIPLE = 256 * 1024  # resumable uploads must send chunks in multiples of 256 KiB (except the last)
MAX_THUMBNAIL_BYTES = 2 * 1024 * 1024
PRIVACY = ("private", "unlisted", "public")

QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded", "userRateLimitExceeded"}


class YouTubeApiError(Exception):
    """Google refused or failed a request. ``reason`` is Google's own reason code when it gave one."""

    def __init__(self, status: int, reason: str, message: str, *, retryable: bool = False):
        super().__init__(f"{status} {reason}: {message}")
        self.status, self.reason, self.message, self.retryable = status, reason, message, retryable

    @property
    def is_quota(self) -> bool:
        return self.reason in QUOTA_REASONS

    @property
    def is_auth(self) -> bool:
        return self.status == 401 or self.reason in ("invalid_grant", "authError", "unauthorized_client")


def parse_error(resp: httpx.Response) -> YouTubeApiError:
    reason, message = "", resp.text[:300]
    try:
        body = resp.json()
        err = body.get("error")
        if isinstance(err, dict):
            message = str(err.get("message", message))
            errs = err.get("errors") or []
            reason = str(errs[0].get("reason", "")) if errs and isinstance(errs[0], dict) else str(err.get("status", ""))
        elif isinstance(err, str):  # OAuth endpoints: {"error": "invalid_grant", "error_description": "..."}
            reason, message = err, str(body.get("error_description", message))
    except (ValueError, AttributeError):
        pass
    retry = resp.status_code >= 500 or resp.status_code == 429 and reason not in QUOTA_REASONS
    return YouTubeApiError(resp.status_code, reason or f"http_{resp.status_code}", message, retryable=retry)


# --- OAuth ----------------------------------------------------------------------------------------------------------------


def pkce_pair() -> tuple[str, str]:
    """``(verifier, challenge)`` for PKCE with S256."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


def auth_url(client_id: str, redirect_uri: str, state: str, challenge: str, scopes: tuple[str, ...] = PHASE4_SCOPES) -> str:
    return AUTH_URL + "?" + urlencode({
        "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": " ".join(scopes), "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256", "access_type": "offline", "prompt": "consent",
        "include_granted_scopes": "true",
    })


class OAuthClient:
    def __init__(self, client_id: str, client_secret: str, *, transport: httpx.BaseTransport | None = None, timeout: float = 30.0):
        self.client_id, self._secret = client_id, client_secret
        self._transport, self._timeout = transport, timeout

    def _post(self, url: str, data: dict[str, str]) -> dict[str, Any]:
        try:
            with httpx.Client(transport=self._transport, timeout=self._timeout, trust_env=False) as c:
                resp = c.post(url, data=data)
        except httpx.HTTPError as e:
            raise YouTubeApiError(0, "network_error", f"could not reach Google ({type(e).__name__})", retryable=True) from e
        if resp.status_code >= 400:
            raise parse_error(resp)
        try:
            return resp.json() if resp.content else {}
        except ValueError:
            return {}

    def exchange_code(self, code: str, redirect_uri: str, verifier: str) -> dict[str, Any]:
        return self._post(TOKEN_URL, {"code": code, "client_id": self.client_id, "client_secret": self._secret, "redirect_uri": redirect_uri,
                                      "grant_type": "authorization_code", "code_verifier": verifier})

    def refresh(self, refresh_token: str) -> dict[str, Any]:
        return self._post(TOKEN_URL, {"refresh_token": refresh_token, "client_id": self.client_id, "client_secret": self._secret,
                                      "grant_type": "refresh_token"})

    def revoke(self, token: str) -> None:
        self._post(REVOKE_URL, {"token": token})


# --- Data API -------------------------------------------------------------------------------------------------------------


Sleep = Callable[[float], None]


class YouTubeClient:
    def __init__(self, access_token: str, *, transport: httpx.BaseTransport | None = None, timeout: float = 60.0, sleep: Sleep | None = None):
        self._token = access_token
        self._transport, self._timeout = transport, timeout
        self._sleep = sleep or time.sleep

    def _client(self) -> httpx.Client:
        return httpx.Client(transport=self._transport, timeout=self._timeout, trust_env=False)

    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def _json(self, method: str, url: str, **kw: Any) -> dict[str, Any]:
        try:
            with self._client() as c:
                resp = c.request(method, url, headers={**self._auth(), **kw.pop("headers", {})}, **kw)
        except httpx.HTTPError as e:
            raise YouTubeApiError(0, "network_error", f"could not reach YouTube ({type(e).__name__})", retryable=True) from e
        if resp.status_code >= 400:
            raise parse_error(resp)
        return resp.json() if resp.content else {}

    def channels_mine(self) -> dict[str, Any]:
        """The channel the signed-in Google account manages (``channels.list?mine=true``)."""
        return self._json("GET", f"{API}/channels", params={"part": "snippet", "mine": "true"})

    def videos_list(self, video_id: str) -> dict[str, Any]:
        return self._json("GET", f"{API}/videos", params={"part": "snippet,status,processingDetails,contentDetails", "id": video_id})

    def video_statistics(self, ids: list[str]) -> dict[str, Any]:
        """Cumulative counts as of now (``videos.list`` part=statistics), up to 50 ids per call."""
        return self._json("GET", f"{API}/videos", params={"part": "statistics,status", "id": ",".join(ids[:50])})

    def analytics_report(self, *, start: str, end: str, metrics: str, dimensions: str | None = None, filters: str | None = None,
                         sort: str | None = None, max_results: int | None = None) -> dict[str, Any]:
        """One YouTube Analytics report for the signed-in channel. Dates are YYYY-MM-DD."""
        params = {"ids": "channel==MINE", "startDate": start, "endDate": end, "metrics": metrics}
        for k, v in (("dimensions", dimensions), ("filters", filters), ("sort", sort), ("maxResults", max_results)):
            if v is not None:
                params[k] = str(v)
        return self._json("GET", f"{ANALYTICS_API}/reports", params=params)

    def comment_threads(self, video_id: str, *, page_token: str | None = None, max_results: int = 100) -> dict[str, Any]:
        params = {"part": "snippet,replies", "videoId": video_id, "maxResults": str(max_results), "order": "time", "textFormat": "plainText"}
        if page_token:
            params["pageToken"] = page_token
        return self._json("GET", f"{API}/commentThreads", params=params)

    def comment_reply(self, parent_id: str, text: str) -> dict[str, Any]:
        """Post a reply to a top-level comment (``comments.insert``). Irreversible from here: the caller records intent first."""
        return self._json("POST", f"{API}/comments", params={"part": "snippet"}, json={"snippet": {"parentId": parent_id, "textOriginal": text}})

    def comment_replies(self, parent_id: str) -> dict[str, Any]:
        """Replies already posted under one top-level comment (``comments.list?parentId=``); used to avoid ever posting the same reply twice."""
        return self._json("GET", f"{API}/comments", params={"part": "snippet", "parentId": parent_id, "maxResults": "100", "textFormat": "plainText"})

    def comment_get(self, comment_id: str) -> dict[str, Any]:
        return self._json("GET", f"{API}/comments", params={"part": "snippet", "id": comment_id, "textFormat": "plainText"})

    def videos_delete(self, video_id: str) -> None:
        try:
            with self._client() as c:
                resp = c.delete(f"{API}/videos", params={"id": video_id}, headers=self._auth())
        except httpx.HTTPError as e:
            raise YouTubeApiError(0, "network_error", f"could not reach YouTube ({type(e).__name__})", retryable=True) from e
        if resp.status_code >= 400:
            raise parse_error(resp)

    def thumbnails_set(self, video_id: str, path: Path) -> dict[str, Any]:
        size = path.stat().st_size
        if size == 0 or size > MAX_THUMBNAIL_BYTES:
            raise YouTubeApiError(0, "thumbnail_size", f"a thumbnail must be between 1 byte and {MAX_THUMBNAIL_BYTES} bytes (this one is {size})")
        mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(path.suffix.lower())
        if mime is None:
            raise YouTubeApiError(0, "thumbnail_type", "a thumbnail must be a PNG or JPEG")
        return self._json("POST", f"{UPLOAD_API}/thumbnails/set", params={"videoId": video_id, "uploadType": "media"},
                          headers={"Content-Type": mime}, content=path.read_bytes())

    # --- resumable upload -------------------------------------------------------------------------------------------------
    def start_upload(self, body: dict[str, Any], size: int, mime: str) -> str:
        """Open a resumable upload session. Returns the session URI (a capability: treat it as a secret)."""
        try:
            with self._client() as c:
                resp = c.post(f"{UPLOAD_API}/videos", params={"uploadType": "resumable", "part": "snippet,status"},
                              headers={**self._auth(), "Content-Type": "application/json; charset=UTF-8", "X-Upload-Content-Length": str(size),
                                       "X-Upload-Content-Type": mime},
                              content=json.dumps(body).encode("utf-8"))
        except httpx.HTTPError as e:
            raise YouTubeApiError(0, "network_error", f"could not reach YouTube ({type(e).__name__})", retryable=True) from e
        if resp.status_code >= 400:
            raise parse_error(resp)
        uri = resp.headers.get("location", "")
        check_session_uri(uri)
        return uri

    def _put(self, uri: str, *, headers: dict[str, str], content: bytes) -> httpx.Response:
        check_session_uri(uri)
        try:
            with self._client() as c:
                return c.put(uri, headers={**self._auth(), **headers}, content=content)
        except httpx.HTTPError as e:
            raise YouTubeApiError(0, "network_error", f"upload connection failed ({type(e).__name__})", retryable=True) from e

    @staticmethod
    def _next_offset(resp: httpx.Response) -> int:
        m = re.match(r"bytes=0-(\d+)", resp.headers.get("range", ""))
        return int(m.group(1)) + 1 if m else 0

    def query_upload(self, uri: str, total: int) -> tuple[str, int | dict[str, Any]]:
        """Ask the session how much it has. ``("incomplete", next_offset)`` or ``("complete", video_resource)``."""
        resp = self._put(uri, headers={"Content-Range": f"bytes */{total}", "Content-Length": "0"}, content=b"")
        if resp.status_code in (200, 201):
            return "complete", resp.json()
        if resp.status_code == 308:
            return "incomplete", self._next_offset(resp)
        raise parse_error(resp)

    def upload(self, uri: str, path: Path, *, start: int, chunk_bytes: int, on_progress: Callable[[int], None] | None = None,
               retries: int = 5) -> dict[str, Any]:
        """Send the file from ``start`` to the end, resuming after interruptions. Returns the video resource Google answered with.

        After a failed chunk the session is asked where it is (never assumed), so bytes are neither skipped nor duplicated.
        """
        total = path.stat().st_size
        if total == 0:
            raise YouTubeApiError(0, "empty_file", "the video file is empty")
        chunk_bytes = max(CHUNK_MULTIPLE, chunk_bytes - chunk_bytes % CHUNK_MULTIPLE)
        offset, failures, ask = start, 0, False
        with path.open("rb") as f:
            while True:
                try:
                    if ask or offset >= total:  # after any failure, and once every byte is sent: ask the session, never guess
                        state, val = self.query_upload(uri, total)
                        ask = False
                        if state == "complete":
                            return val  # type: ignore[return-value]
                        offset = int(val)  # type: ignore[arg-type]
                        if offset >= total:
                            raise YouTubeApiError(0, "upload_incomplete", "YouTube reports all bytes received but has not finished the upload", retryable=True)
                        continue
                    f.seek(offset)
                    data = f.read(chunk_bytes)
                    resp = self._put(uri, headers={"Content-Range": f"bytes {offset}-{offset + len(data) - 1}/{total}", "Content-Type": "application/octet-stream"},
                                     content=data)
                    if resp.status_code in (200, 201):
                        return resp.json()
                    if resp.status_code != 308:
                        raise parse_error(resp)
                    offset, failures = self._next_offset(resp), 0
                    if on_progress:
                        on_progress(offset)
                except YouTubeApiError as e:
                    if not e.retryable or failures >= retries:
                        raise
                    failures += 1
                    self._sleep(min(30.0, 2.0 ** failures))
                    ask = True


def check_session_uri(uri: str) -> None:
    """A session URI must be an https Google address; the access token is never sent anywhere else."""
    p = urlsplit(uri or "")
    host = (p.hostname or "").lower()
    if p.scheme != "https" or not (host == "googleapis.com" or host.endswith(".googleapis.com") or host.endswith(".google.com") or host == "google.com"):
        raise YouTubeApiError(0, "bad_session_uri", "YouTube returned an upload address that is not a Google https address; refusing to send data to it")
