"""A mock Google that follows the *documented* OAuth and YouTube Data API v3 behaviour used by this app.

It exists so the integration can be tested without credentials. It is a model of the documentation, not of Google: the
owner's first real run is what proves the model right (docs/YOUTUBE.md lists what that run confirms).
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

CLIENT_ID, CLIENT_SECRET = "test-client.apps.example", "test-secret"
UPLOAD_HOST = "https://upload.googleapis.com"


class FakeGoogle:
    def __init__(self) -> None:
        self.channel = {"id": "UC_birthday_123", "snippet": {"title": "Happy Birthday Songs (test)", "customUrl": "@hbsongs"}}
        self.no_channel = False
        self.granted_scopes = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"]
        self.send_refresh_token = True
        self.revoked = False
        self.challenge: str | None = None
        self.expect_redirect: str | None = None
        self.requests: list[httpx.Request] = []
        self.token_counter = 0
        # data API behaviour
        self.videos: dict[str, dict[str, Any]] = {}
        self.sessions: dict[str, dict[str, Any]] = {}
        self.fail: dict[str, list[tuple[int, str, str]]] = {}  # route -> queue of (status, reason, message)
        self.drop_after_bytes: list[int] = []  # simulate a connection that dies after N bytes of a chunk are accepted
        self.lock_private = False  # the "unverified project" behaviour: whatever was asked, it comes back private
        self.thumbnail_error: tuple[int, str, str] | None = None
        self.processing = "processing"
        self.channel_for_videos: str | None = None  # override channelId reported for uploads (simulates a wrong account)
        self.transport = httpx.MockTransport(self._handle)

    # --- helpers ---
    def calls(self, needle: str) -> list[httpx.Request]:
        return [r for r in self.requests if needle in str(r.url)]

    def _err(self, status: int, reason: str, message: str, oauth: bool = False) -> httpx.Response:
        if oauth:
            return httpx.Response(status, json={"error": reason, "error_description": message})
        return httpx.Response(status, json={"error": {"code": status, "message": message, "errors": [{"reason": reason, "message": message}]}})

    def _queued_failure(self, route: str) -> httpx.Response | None:
        q = self.fail.get(route)
        if q:
            status, reason, message = q.pop(0)
            return self._err(status, reason, message)
        return None

    # --- dispatch ---
    def _handle(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        url = urlsplit(str(req.url))
        path, q = url.path, parse_qs(url.query)
        if url.hostname == "oauth2.googleapis.com" and path == "/token":
            return self._token(req)
        if url.hostname == "oauth2.googleapis.com" and path == "/revoke":
            self.revoked = True
            return httpx.Response(200, json={})
        if path == "/youtube/v3/channels":
            if (failure := self._queued_failure("channels.list")):
                return failure
            if req.headers.get("authorization", "") == "Bearer expired":
                return self._err(401, "authError", "Invalid Credentials")
            return httpx.Response(200, json={"items": [] if self.no_channel else [self.channel]})
        if url.hostname == "upload.googleapis.com" and path == "/upload/youtube/v3/videos" and "upload_id" in q:
            return self._chunk(req, q["upload_id"][0])
        if path == "/upload/youtube/v3/videos" and q.get("uploadType") == ["resumable"]:
            return self._start(req)
        if path == "/upload/youtube/v3/thumbnails/set":
            if (failure := self._queued_failure("thumbnails.set")):
                return failure
            if self.thumbnail_error:
                return self._err(*self.thumbnail_error)
            return httpx.Response(200, json={"items": [{"default": {"url": "https://i.ytimg.com/vi/x/default.jpg"}}]})
        if path == "/youtube/v3/videos" and req.method == "GET":
            if (failure := self._queued_failure("videos.list")):
                return failure
            vid = q.get("id", [""])[0]
            v = self.videos.get(vid)
            return httpx.Response(200, json={"items": [self._resource(v)] if v else []})
        if path == "/youtube/v3/videos" and req.method == "DELETE":
            self.videos.pop(q.get("id", [""])[0], None)
            return httpx.Response(204)
        return httpx.Response(404, json={"error": {"code": 404, "message": f"unhandled {req.method} {url.path}"}})

    def _token(self, req: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
        if form.get("client_id") != CLIENT_ID or form.get("client_secret") != CLIENT_SECRET:
            return self._err(401, "invalid_client", "The OAuth client was not found.", oauth=True)
        if form.get("grant_type") == "authorization_code":
            if form.get("code") != "good-code":
                return self._err(400, "invalid_grant", "Bad Request", oauth=True)
            if self.challenge is not None:
                digest = base64.urlsafe_b64encode(hashlib.sha256(form.get("code_verifier", "").encode()).digest()).rstrip(b"=").decode()
                if digest != self.challenge:
                    return self._err(400, "invalid_grant", "code_verifier does not match code_challenge", oauth=True)
            if self.expect_redirect and form.get("redirect_uri") != self.expect_redirect:
                return self._err(400, "redirect_uri_mismatch", "redirect_uri mismatch", oauth=True)
            self.token_counter += 1
            body = {"access_token": f"access-{self.token_counter}", "expires_in": 3599, "scope": " ".join(self.granted_scopes), "token_type": "Bearer"}
            if self.send_refresh_token:
                body["refresh_token"] = "refresh-secret-1"
            return httpx.Response(200, json=body)
        if form.get("grant_type") == "refresh_token":
            if self.revoked or form.get("refresh_token") != "refresh-secret-1":
                return self._err(400, "invalid_grant", "Token has been expired or revoked.", oauth=True)
            self.token_counter += 1
            return httpx.Response(200, json={"access_token": f"access-{self.token_counter}", "expires_in": 3599, "scope": " ".join(self.granted_scopes),
                                             "token_type": "Bearer"})
        return self._err(400, "unsupported_grant_type", "bad grant", oauth=True)

    # --- resumable uploads ---
    def _start(self, req: httpx.Request) -> httpx.Response:
        if (failure := self._queued_failure("videos.insert")):
            return failure
        if not req.headers.get("authorization", "").startswith("Bearer "):
            return self._err(401, "authError", "Login Required")
        meta = json.loads(req.content)
        sid = f"sess{len(self.sessions) + 1}"
        self.sessions[sid] = {"meta": meta, "size": int(req.headers["x-upload-content-length"]), "mime": req.headers["x-upload-content-type"],
                              "data": bytearray(), "done": None}
        return httpx.Response(200, headers={"Location": f"{UPLOAD_HOST}/upload/youtube/v3/videos?uploadType=resumable&upload_id={sid}"})

    def _chunk(self, req: httpx.Request, sid: str) -> httpx.Response:
        s = self.sessions.get(sid)
        if s is None:
            return self._err(404, "notFound", "Upload session not found")
        if (failure := self._queued_failure("upload.chunk")):
            return failure
        cr = req.headers.get("content-range", "")
        if s["done"] is not None:
            return httpx.Response(200, json=s["done"])
        if re.fullmatch(r"bytes \*/\d+", cr):  # status query
            return self._progress(s)
        m = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", cr)
        if not m:
            return self._err(400, "badRequest", f"bad Content-Range {cr}")
        start, end, total = (int(x) for x in m.groups())
        if start != len(s["data"]):
            return self._progress(s)  # a client that is out of step is told where the session really is
        body = req.content
        if self.drop_after_bytes:
            keep = self.drop_after_bytes.pop(0)
            s["data"] += body[:keep]
            raise httpx.ReadError("connection dropped mid-chunk")
        s["data"] += body
        if len(s["data"]) >= total:
            vid = f"vid{len(self.videos) + 1:03d}"
            meta = s["meta"]
            self.videos[vid] = {"id": vid, "snippet": {**meta["snippet"], "channelId": self.channel_for_videos or self.channel["id"]},
                                "status": {**meta["status"], "uploadStatus": "uploaded"}, "bytes": bytes(s["data"])}
            if self.lock_private and meta["status"].get("privacyStatus") != "private":
                self.videos[vid]["status"]["privacyStatus"] = "private"
            s["done"] = self._resource(self.videos[vid])
            return httpx.Response(200, json=s["done"])
        return self._progress(s)

    @staticmethod
    def _progress(s: dict[str, Any]) -> httpx.Response:
        n = len(s["data"])
        return httpx.Response(308, headers={"Range": f"bytes=0-{n - 1}"} if n else {})

    def _resource(self, v: dict[str, Any]) -> dict[str, Any]:
        return {"id": v["id"], "snippet": v["snippet"], "status": v["status"],
                "processingDetails": {"processingStatus": self.processing}, "contentDetails": {"duration": "PT38S"}}
