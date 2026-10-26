"""ElevenLabs text-to-speech client.

HONESTY NOTE: written against ElevenLabs' public REST API (header ``xi-api-key``; ``GET /v1/voices``;
``POST /v1/text-to-speech/{voice_id}``). It has been exercised **only against a mock transport** in the tests.
It has NOT been run against a real account, because no key was available, and the provider's own documentation could
not be opened from the build environment. ``verified_live`` therefore stays false until the owner runs
``python -m app.cli provider-check elevenlabs`` with a key. Music, image and sound-effect endpoints are not implemented.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

BASE_URL = "https://api.elevenlabs.io"


class ProviderError(Exception):
    def __init__(self, message: str, status: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class AuthError(ProviderError):
    pass


class RateLimited(ProviderError):
    pass


@dataclass
class Speech:
    audio: bytes
    request_id: str | None
    characters: int
    model_id: str


class ElevenLabsClient:
    def __init__(self, api_key: str, *, transport: httpx.BaseTransport | None = None, timeout: float = 90.0, base_url: str = BASE_URL):
        if not api_key.strip():
            raise AuthError("no ElevenLabs API key")
        self._client = httpx.Client(base_url=base_url, timeout=timeout, transport=transport,
                                    headers={"xi-api-key": api_key.strip(), "User-Agent": "ai-media-company/0.1"})

    def close(self) -> None:
        self._client.close()

    def _check(self, r: httpx.Response) -> httpx.Response:
        if r.status_code in (401, 403):
            raise AuthError("ElevenLabs rejected the API key or the plan does not allow this call", r.status_code)
        if r.status_code == 429:
            ra = r.headers.get("retry-after")
            raise RateLimited("ElevenLabs rate limit or quota reached", 429, float(ra) if ra and ra.replace(".", "", 1).isdigit() else None)
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail")
            except ValueError:
                detail = None
            raise ProviderError(f"ElevenLabs returned {r.status_code}: {str(detail or r.text)[:200]}", r.status_code)
        return r

    def list_voices(self) -> list[dict]:
        """Free to call; used by the provider check to prove the key works without generating anything."""
        r = self._check(self._client.get("/v1/voices"))
        return r.json().get("voices", [])

    def synthesize(self, text: str, voice_id: str, *, model_id: str = "eleven_multilingual_v2",
                   output_format: str = "mp3_44100_128") -> Speech:
        if not text.strip():
            raise ProviderError("nothing to synthesize")
        r = self._check(self._client.post(f"/v1/text-to-speech/{voice_id}", params={"output_format": output_format},
                                          json={"text": text, "model_id": model_id}, headers={"Accept": "audio/mpeg"}))
        if not r.content:
            raise ProviderError("ElevenLabs returned an empty audio response")
        return Speech(r.content, r.headers.get("request-id") or r.headers.get("x-request-id"), len(text), model_id)


def check_account(client: ElevenLabsClient) -> dict:
    """Prove a key works without generating anything (no characters are used): read the account, or the voice list if the key may not read the account.

    Unconfirmed against a live account (no key was available when this was written): the account route and its fields follow the public API.
    A field the service does not return is reported as None (unavailable), never as zero.
    """
    r = client._client.get("/v1/user")
    if r.status_code == 200:
        sub = (r.json() or {}).get("subscription") or {}
        return {"ok": True, "limited": False, "tier": sub.get("tier"), "characters_used": sub.get("character_count"), "character_limit": sub.get("character_limit"),
                "via": "GET /v1/user"}
    if r.status_code in (401, 403):
        try:
            status = ((r.json() or {}).get("detail") or {}).get("status")
        except ValueError:
            status = None
        if status == "missing_permissions":  # a restricted key: valid, but not allowed to read the account
            v = client._client.get("/v1/voices")
            if v.status_code == 200:
                return {"ok": True, "limited": True, "tier": None, "characters_used": None, "character_limit": None, "via": "GET /v1/voices",
                        "note": "The key works, but it is restricted: it cannot read your account or usage. Text-to-speech needs its permission switched on in ElevenLabs."}
        raise AuthError("ElevenLabs rejected the key. Check that you copied the whole key and that it has not been deleted or restricted.", r.status_code)
    client._check(r)
    return {"ok": False, "via": "GET /v1/user"}
