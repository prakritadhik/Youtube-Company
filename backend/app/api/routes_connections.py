"""The Connections page: where the owner connects each outside service, with plain steps and a read-only test.

Secrets are accepted here and never returned. Everything that changes a credential is owner-only and audited without the value.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai import gateway
from app.api.deps import Auth, require
from app.db import get_db, get_wdb
from app.models.youtube import YouTubeConnection
from app.providers import base as providers
from app.providers import claude_code as cc
from app.providers import elevenlabs as el
from app.security.permissions import Actor, P
from app.services import audit, credentials
from app.services import settings as settings_svc
from app.timeutil import iso, utcnow

router = APIRouter(prefix="/api", tags=["connections"])

client_factory = el.ElevenLabsClient  # replaced in tests so no network is touched


class ValueIn(BaseModel):
    value: str = Field(min_length=1, max_length=700)


def _owner_only(auth: Auth) -> None:
    if not auth.actor.is_owner:
        raise HTTPException(403, "Only the owner can change or test connections.")


def _last_tests(db: Session) -> dict[str, Any]:
    return settings_svc.get(db, "connection_tests")


def _cap(db: Session, provider_id: str) -> list[dict[str, Any]]:
    return next((p["capabilities"] for p in providers.status_report(db) if p["id"] == provider_id), [])


def _elevenlabs_detail(has_key: bool, last: dict[str, Any] | None, attested: bool) -> str:
    if not has_key:
        return "No key yet."
    if last is None:
        return "A key is saved but not tested yet. Press Test the key."
    if not last.get("ok"):
        return "A key is saved, but the last test failed (see below). Fix the key or the internet connection and test again."
    if not attested:
        return "The key works. Next: confirm in Settings → Providers that your plan allows commercial use through the API, and set the daily audio limit above 0."
    return "The key works and you have confirmed your plan."


@router.get("/connections")
def connections(db: Session = Depends(get_db), auth: Auth = Depends(require(P.SETTINGS_READ))):
    tests = _last_tests(db)
    att = settings_svc.get(db, "provider_attestations")
    brain = gateway.brain_state(db)
    el_keys = [credentials.describe(db, "elevenlabs_api_key")]
    yt_keys = [credentials.describe(db, "google_oauth_client_id"), credentials.describe(db, "google_oauth_client_secret")]
    el_attested = bool((att.get("elevenlabs") or {}).get("api_access_confirmed"))
    el_works = bool((tests.get("elevenlabs") or {}).get("ok"))
    connected_channels = db.scalar(select(func.count()).select_from(YouTubeConnection).where(YouTubeConnection.status == "CONNECTED")) or 0
    return {"services": [
        {"id": "claude-code", "name": "Claude Code (the AI brain)", "kind": "program",
         "summary": "Does the writing and the managers' reviews, on this computer, with your own Claude account. No key is pasted anywhere.",
         "state": "connected" if brain["ready"] else "needs_setup", "state_detail": "Ready." if brain["ready"] else brain["detail"],
         "program": cc.locate(), "credentials": [], "capabilities": _cap(db, "claude-code"), "attested": bool((att.get("claude-code") or {}).get("api_access_confirmed")),
         "open": "/ai", "test": None,
         "steps": ["Install Claude Code and run `claude` once to sign in.", "Confirm your plan allows this under Settings → Providers.", "Set the daily allowance in Settings.",
                   "Run the connection test on the AI Brain page."]},
        {"id": "elevenlabs", "name": "ElevenLabs (narration)", "kind": "key",
         "summary": "Turns a script into a spoken voice. Narration only: sung music is not built yet.",
         "state": ("connected" if el_works and el_attested else "partial") if el_keys[0]["set"] else "needs_setup",
         "state_detail": _elevenlabs_detail(el_keys[0]["set"], tests.get("elevenlabs"), el_attested),
         "credentials": el_keys, "capabilities": _cap(db, "elevenlabs"), "attested": el_attested,
         "test": {"label": "Test the key", "path": "/api/connections/elevenlabs/test", "last": tests.get("elevenlabs"),
                  "note": "A read-only call: it uses no characters and costs nothing."},
         "links": [{"label": "ElevenLabs API keys", "url": "https://elevenlabs.io/app/settings/api-keys"}],
         "steps": ["In ElevenLabs open Settings → API Keys and create a key. Give it text-to-speech access (and 'user read' so the test can show your usage).",
                   "Paste it below and press Save.", "Press Test the key.",
                   "In Settings → Providers confirm your plan allows commercial use through the API, and set the daily audio limit above 0."]},
        {"id": "youtube", "name": "YouTube (Google)", "kind": "oauth",
         "summary": "Lets the company upload, read performance and answer comments on your channels, only with your approval.",
         "state": "connected" if connected_channels else ("partial" if all(c["set"] for c in yt_keys) else "needs_setup"),
         "state_detail": (f"{connected_channels} channel(s) signed in." if connected_channels else
                          ("The Google client is saved. Now sign in to each channel (Settings → YouTube)." if all(c["set"] for c in yt_keys) else "The Google client is not saved yet.")),
         "credentials": yt_keys, "capabilities": _cap(db, "youtube"), "attested": bool((att.get("youtube") or {}).get("api_access_confirmed")),
         "test": {"label": "Check the client", "path": "/api/connections/youtube/check", "last": tests.get("youtube"),
                  "note": "Checks what can be checked without signing in. Only signing in to a channel proves the connection."},
         "open": "/settings", "links": [{"label": "Google Cloud console", "url": "https://console.cloud.google.com/apis/credentials"}],
         "steps": ["In Google Cloud create a project and enable the YouTube Data API v3 (and the YouTube Analytics API for performance data).",
                   "Set up the OAuth consent screen, adding yourself as a test user.", "Create credentials → OAuth client ID → application type Desktop app.",
                   "Paste the Client ID and the Client secret below and press Save.",
                   "In Settings → YouTube press Connect for each channel, sign in with the Google account that owns it, and answer whether it is made for kids.",
                   "Keep Approval-first mode, then run the one-minute self-test (docs/YOUTUBE.md)."]},
        {"id": "canva", "name": "Canva (design)", "kind": "manual",
         "summary": "Not automated. Design in Canva yourself and use the exported picture; the company does not call Canva.",
         "state": "manual", "state_detail": "Manual by design. An importer for Canva exports is not built.", "credentials": [], "capabilities": [],
         "attested": False, "test": None, "steps": ["Design in Canva.", "Export a PNG.", "Use it as your own artwork; record where it came from and that you may use it."]},
    ]}


@router.put("/connections/credentials/{name}")
def save_credential(name: str, body: ValueIn, db: Session = Depends(get_wdb, scope="function"), auth: Auth = Depends(require(P.SETTINGS_WRITE))):
    _owner_only(auth)
    credentials.save(db, auth.actor, name, body.value)
    return credentials.describe(db, name)


@router.delete("/connections/credentials/{name}")
def remove_credential(name: str, db: Session = Depends(get_wdb, scope="function"), auth: Auth = Depends(require(P.SETTINGS_WRITE))):
    _owner_only(auth)
    if name not in credentials.SPECS:
        raise HTTPException(404, "No such setting.")
    credentials.remove(db, auth.actor, name)
    return credentials.describe(db, name)


def _remember(db: Session, service: str, ok: bool, detail: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    result = {"ok": ok, "detail": detail, "at": iso(utcnow()), **(extra or {})}
    settings_svc._write_internal(db, Actor.system("connection-test"), "connection_tests", {**_last_tests(db), service: result})
    return result


@router.post("/connections/elevenlabs/test")
def test_elevenlabs(db: Session = Depends(get_wdb, scope="function"), auth: Auth = Depends(require(P.SETTINGS_WRITE))):
    _owner_only(auth)
    key = credentials.get("elevenlabs_api_key", db)
    if not key:
        raise HTTPException(409, "No ElevenLabs key is saved yet.")
    client = client_factory(key)
    try:
        info = el.check_account(client)
        out = _remember(db, "elevenlabs", True, info.get("note") or "ElevenLabs accepted the key.", {k: v for k, v in info.items() if k != "ok"})
    except el.AuthError as e:
        out = _remember(db, "elevenlabs", False, str(e))
    except el.RateLimited:
        out = _remember(db, "elevenlabs", False, "ElevenLabs is rate limiting this key right now. Try again in a minute.")
    except el.ProviderError as e:
        out = _remember(db, "elevenlabs", False, str(e))
    except httpx.HTTPError as e:
        out = _remember(db, "elevenlabs", False, f"Could not reach ElevenLabs from this computer ({type(e).__name__}). Check the internet connection.")
    finally:
        client.close()
    audit.record(db, auth.actor, "connection.tested", "connection", "elevenlabs", {"ok": out["ok"]})
    return out


@router.post("/connections/youtube/check")
def check_youtube(db: Session = Depends(get_wdb, scope="function"), auth: Auth = Depends(require(P.SETTINGS_WRITE))):
    _owner_only(auth)
    cid, secret = credentials.get("google_oauth_client_id", db), credentials.get("google_oauth_client_secret", db)
    problems = []
    if not cid:
        problems.append("The client ID is not saved.")
    if not secret:
        problems.append("The client secret is not saved.")
    ok = not problems
    out = _remember(db, "youtube", ok, "The client ID and secret are saved. Now sign in to each channel." if ok else " ".join(problems),
                    {"proves": "only that both values are present; signing in to a channel is what proves the connection"})
    audit.record(db, auth.actor, "connection.tested", "connection", "youtube", {"ok": ok})
    return out
