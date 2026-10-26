"""The Connections page: keys saved in the dashboard (encrypted, owner-only, never shown again), used at once, and tested read-only against a mock service."""

from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import select

from app.api import routes_connections
from app.config import reset_settings_cache
from app.models.audit import AuditEvent
from app.models.credentials import Credential
from app.providers import base as providers
from app.providers import elevenlabs as el
from app.security import redaction
from app.security.permissions import PermissionDenied
from app.services import credentials
from tests.api_fixtures import api_env, client, fast_hashing, make_client, owner_client, viewer_client  # noqa: F401

KEY = "sk_" + "abcd1234" * 4
GID = "1234567890-abc.apps.googleusercontent.com"
GSECRET = "GOCSPX-" + "z9y8x7" * 4


@pytest.fixture(autouse=True)
def no_env_keys(monkeypatch):
    for k in ("ELEVENLABS_API_KEY", "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    reset_settings_cache()


# --- the store -----------------------------------------------------------------------------------------------------------


def test_a_saved_key_is_encrypted_at_rest_readable_by_any_process_and_masked_in_logs(db, owner):
    credentials.save(db, owner, "elevenlabs_api_key", f"  {KEY}  ")
    db.commit()
    row = db.get(Credential, "elevenlabs_api_key")
    assert KEY not in row.value_enc and row.hint == KEY[-4:]  # encrypted; only the last four characters are kept in the clear
    assert credentials.get("elevenlabs_api_key") == KEY  # a fresh session (the worker's view) sees it with no restart
    assert redaction.redact_text(f"the key {KEY} failed") == f"the key {redaction.REDACTED} failed"
    d = credentials.describe(db, "elevenlabs_api_key")
    assert d["set"] and d["source"] == "dashboard" and d["shown"] == f"…{KEY[-4:]}" and KEY not in json.dumps(d)


def test_the_dashboard_value_wins_over_env_and_removing_it_falls_back(db, owner, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_from_env_" + "e" * 20)
    reset_settings_cache()
    assert credentials.source("elevenlabs_api_key", db) == "env" and credentials.get("elevenlabs_api_key", db).startswith("sk_from_env_")
    credentials.save(db, owner, "elevenlabs_api_key", KEY)
    db.commit()
    assert credentials.source("elevenlabs_api_key", db) == "dashboard" and credentials.get("elevenlabs_api_key", db) == KEY
    credentials.remove(db, owner, "elevenlabs_api_key")
    db.commit()
    assert credentials.get("elevenlabs_api_key", db).startswith("sk_from_env_")


def test_only_the_owner_may_change_a_credential_and_the_audit_never_holds_the_value(db, owner, agent):
    for who in (agent("ceo"), agent("coo")):
        with pytest.raises(PermissionDenied):
            credentials.save(db, who, "elevenlabs_api_key", KEY)
    credentials.save(db, owner, "elevenlabs_api_key", KEY)
    credentials.remove(db, owner, "elevenlabs_api_key")
    db.commit()
    events = list(db.scalars(select(AuditEvent).where(AuditEvent.action.in_(("credential.saved", "credential.removed"))).order_by(AuditEvent.id)))
    assert [e.action for e in events] == ["credential.saved", "credential.removed"]
    assert all(KEY not in json.dumps(e.details) for e in events) and events[0].details["ends_with"] == KEY[-4:]


@pytest.mark.parametrize("name, value, why", [
    ("elevenlabs_api_key", "", "Paste the value"), ("elevenlabs_api_key", "sk_abc def_ghijklmn", "space"), ("elevenlabs_api_key", "short", "too short"),
    ("google_oauth_client_id", "1234567890-abc", ".apps.googleusercontent.com"), ("anthropic_api_key", KEY, "not a setting"),
])
def test_obviously_wrong_values_are_refused_with_a_reason(db, owner, name, value, why):
    with pytest.raises(credentials.CredentialError, match=why):
        credentials.save(db, owner, name, value)


def test_removing_something_that_was_never_saved_says_so(db, owner):
    with pytest.raises(credentials.CredentialError, match="Nothing is saved"):
        credentials.remove(db, owner, "elevenlabs_api_key")


def test_a_missing_encryption_key_is_reported_not_guessed(db, owner, tmp_path):
    from app.config import get_settings

    credentials.save(db, owner, "elevenlabs_api_key", KEY)
    db.commit()
    (get_settings().secrets_dir / "token.key").unlink()
    d = credentials.describe(db, "elevenlabs_api_key")
    assert d["set"] is False and "cannot be read" in d["problem"]
    assert credentials.get("elevenlabs_api_key", db) is None  # never a wrong value


def test_providers_become_configured_when_the_dashboard_holds_what_they_need(db, owner):
    assert providers.capability_state(db, "elevenlabs", "speech.tts")[0] == "not_configured"
    credentials.save(db, owner, "elevenlabs_api_key", KEY)
    db.commit()
    assert providers.capability_state(db, "elevenlabs", "speech.tts")[0] == "needs_attestation"  # key present; the owner's confirmation is next
    state, detail = providers.capability_state(db, "youtube", "youtube.upload")
    assert state == "not_configured" and "Google OAuth client" in detail and "Connections page" in detail
    credentials.save(db, owner, "google_oauth_client_id", GID)
    db.commit()
    assert providers.capability_state(db, "youtube", "youtube.upload")[0] == "not_configured"  # the secret is still missing
    credentials.save(db, owner, "google_oauth_client_secret", GSECRET)
    db.commit()
    assert providers.capability_state(db, "youtube", "youtube.upload")[0] == "needs_attestation"


def test_the_youtube_connection_code_reads_the_dashboard_client(db, owner):
    from app.youtube import connection as yt

    assert yt.configured() is False
    credentials.save(db, owner, "google_oauth_client_id", GID)
    credentials.save(db, owner, "google_oauth_client_secret", GSECRET)
    db.commit()
    assert yt.configured() is True and yt.oauth() is not None


# --- the API ---------------------------------------------------------------------------------------------------------------


def serve(monkeypatch, handler):
    monkeypatch.setattr(routes_connections, "client_factory", lambda key: el.ElevenLabsClient(key, transport=httpx.MockTransport(handler)))


def test_everything_requires_signing_in_and_viewers_cannot_even_look(client, owner_client, viewer_client):
    for method, url in (("GET", "/api/connections"), ("PUT", "/api/connections/credentials/elevenlabs_api_key"), ("DELETE", "/api/connections/credentials/elevenlabs_api_key"),
                        ("POST", "/api/connections/elevenlabs/test"), ("POST", "/api/connections/youtube/check")):
        assert make_client().request(method, url, json={"value": KEY}).status_code == 401, (method, url)
        assert viewer_client.request(method, url, json={"value": KEY}).status_code == 403, (method, url)


def test_the_page_lists_every_service_with_honest_states_and_never_returns_a_secret(owner_client):
    r = owner_client.get("/api/connections").json()["services"]
    assert [s["id"] for s in r] == ["claude-code", "elevenlabs", "youtube", "canva"]
    by = {s["id"]: s for s in r}
    assert by["elevenlabs"]["state"] == "needs_setup" and by["youtube"]["state"] == "needs_setup" and by["canva"]["state"] == "manual"
    assert any("sung music" in s["summary"] for s in [by["elevenlabs"]]) and "not built" in by["canva"]["state_detail"]
    assert owner_client.put("/api/connections/credentials/elevenlabs_api_key", json={"value": KEY}).json()["shown"] == f"…{KEY[-4:]}"
    page = owner_client.get("/api/connections")
    assert KEY not in page.text and {c["name"] for c in page.json()["services"][1]["credentials"]} == {"elevenlabs_api_key"}
    assert page.json()["services"][1]["state"] == "partial" and "not tested yet" in page.json()["services"][1]["state_detail"]  # saved is not the same as working


def test_a_bad_value_is_a_clear_422_and_an_unknown_name_is_a_404(owner_client):
    assert owner_client.put("/api/connections/credentials/elevenlabs_api_key", json={"value": "no good"}).status_code == 422
    assert owner_client.put("/api/connections/credentials/whatever", json={"value": KEY}).status_code == 422
    assert owner_client.delete("/api/connections/credentials/whatever").status_code == 404


def test_the_elevenlabs_test_reports_a_working_key_with_the_usage_it_can_read_and_remembers_it(owner_client, monkeypatch):
    assert owner_client.post("/api/connections/elevenlabs/test").status_code == 409  # nothing to test yet
    owner_client.put("/api/connections/credentials/elevenlabs_api_key", json={"value": KEY})
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path, req.headers.get("xi-api-key")))
        return httpx.Response(200, json={"subscription": {"tier": "creator", "character_count": 1200, "character_limit": 100000}})

    serve(monkeypatch, handler)
    r = owner_client.post("/api/connections/elevenlabs/test").json()
    assert r["ok"] is True and r["tier"] == "creator" and r["characters_used"] == 1200 and r["character_limit"] == 100000
    assert seen == [("GET", "/v1/user", KEY)]  # one read-only call, nothing generated
    svc = owner_client.get("/api/connections").json()["services"][1]
    assert svc["test"]["last"]["ok"] is True and svc["test"]["last"]["tier"] == "creator"
    assert svc["state"] == "partial" and "confirm in Settings" in svc["state_detail"]  # the key works, but nothing may run until the owner confirms the plan
    owner_client.put("/api/settings/provider_attestations", json={"value": {"elevenlabs": {"api_access_confirmed": True, "commercial_use_confirmed": True}}})
    assert owner_client.get("/api/connections").json()["services"][1]["state"] == "connected"


def test_a_rejected_key_a_restricted_key_and_an_unreachable_service_are_each_explained(owner_client, monkeypatch):
    owner_client.put("/api/connections/credentials/elevenlabs_api_key", json={"value": KEY})

    serve(monkeypatch, lambda req: httpx.Response(401, json={"detail": {"status": "invalid_api_key", "message": "Invalid API key"}}))
    r = owner_client.post("/api/connections/elevenlabs/test").json()
    assert r["ok"] is False and "rejected the key" in r["detail"]

    def restricted(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/user":
            return httpx.Response(401, json={"detail": {"status": "missing_permissions", "message": "needs user_read"}})
        return httpx.Response(200, json={"voices": []})

    serve(monkeypatch, restricted)
    r = owner_client.post("/api/connections/elevenlabs/test").json()
    assert r["ok"] is True and r["limited"] is True and r["tier"] is None and r["characters_used"] is None  # unavailable, never zero
    assert "restricted" in r["detail"]

    def down(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=req)

    serve(monkeypatch, down)
    r = owner_client.post("/api/connections/elevenlabs/test").json()
    assert r["ok"] is False and "Could not reach ElevenLabs" in r["detail"]

    serve(monkeypatch, lambda req: httpx.Response(429, headers={"retry-after": "30"}))
    assert "rate limiting" in owner_client.post("/api/connections/elevenlabs/test").json()["detail"]


def test_the_google_check_says_exactly_what_it_does_and_does_not_prove(owner_client):
    r = owner_client.post("/api/connections/youtube/check").json()
    assert r["ok"] is False and "client ID is not saved" in r["detail"] and "secret is not saved" in r["detail"]
    owner_client.put("/api/connections/credentials/google_oauth_client_id", json={"value": GID})
    owner_client.put("/api/connections/credentials/google_oauth_client_secret", json={"value": GSECRET})
    r = owner_client.post("/api/connections/youtube/check").json()
    assert r["ok"] is True and "signing in" in r["proves"]
    svc = {s["id"]: s for s in owner_client.get("/api/connections").json()["services"]}["youtube"]
    assert svc["state"] == "partial" and "sign in to each channel" in svc["state_detail"] and GSECRET not in json.dumps(svc)
