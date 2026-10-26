"""Provider registry and the gate every external or generating call must pass.

A capability is *ready* only when all of these hold: it is implemented, the credential is configured, the owner
has attested the plan's terms, the company is not paused, and (for paid use) the budget allows it. Anything else is
reported honestly as unavailable, with the reason.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.providers import claude_code
from app.services import control, finance
from app.services import settings as settings_svc


class ProviderUnavailable(Exception):
    """The requested capability cannot be used right now. ``state`` says why."""

    def __init__(self, provider: str, capability: str, state: str, detail: str):
        super().__init__(f"{provider}/{capability} unavailable ({state}): {detail}")
        self.provider, self.capability, self.state, self.detail = provider, capability, state, detail
        self.code = state.upper()
        self.reason = str(self)


@dataclass(frozen=True)
class Capability:
    id: str
    description: str
    implemented: bool  # code exists for it
    verified_live: bool = False  # exercised against the real service by the owner's key (never true until then)
    paid: bool = False
    note: str = ""


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    needs_key: bool
    key_setting: str | None
    needs_attestation: bool
    needs_tool: str | None
    capabilities: tuple[Capability, ...]
    docs: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


REGISTRY: dict[str, Provider] = {
    "local-procedural": Provider(
        "local-procedural", "Built-in generators", False, None, False, None,
        (Capability("scene.image", "Procedural illustrated backdrops (deterministic, owner-owned)", True),
         Capability("thumbnail.image", "Procedural thumbnails", True),
         Capability("music.instrumental", "Seeded algorithmic instrumental with a bar-accurate timeline (no vocals)", True,
                    note="a working stand-in, not a produced song")),
    ),
    "local-render": Provider(
        "local-render", "FFmpeg assembly", False, None, False, "ffmpeg",
        (Capability("video.render", "Assemble scenes, audio and captions into H.264/AAC video", True),
         Capability("video.validate", "Decode, loudness, silence, black-frame, sync and caption checks", True)),
    ),
    "elevenlabs": Provider(
        "elevenlabs", "ElevenLabs", True, "elevenlabs_api_key", True, None,
        (Capability("speech.tts", "Narration (text to speech)", True, paid=True,
                    note="request shape follows the public API; NOT yet verified against a live account"),
         Capability("music.sung", "Sung music with supplied lyrics (the account lists music models with custom lyrics)", False, paid=True,
                    note="adapter not written: the music request format has not been verified"),
         Capability("image.generate", "Image generation", False, paid=True, note="adapter not written"),
         Capability("sfx", "Sound effects", False, paid=True, note="adapter not written")),
        docs="docs/PROVIDERS.md",
    ),
    "claude-code": Provider(
        "claude-code", "Claude Code on this computer", False, None, True, None,
        (Capability("llm.generate", "Agent drafting and review (briefs, lyrics, scripts, ideas, manager reviews), by running the Claude Code program on this computer "
                    "with your own signed-in Claude account; no API key, no tools", True,
                    note="needs `claude` installed and signed in; each call uses your plan's usage; tested against a stand-in program and, in one opt-in test, the real one"),),
        docs="docs/AI_BRAIN.md", extra={"locator": claude_code.locate},
    ),
    "anthropic": Provider(
        "anthropic", "Anthropic Claude API", True, "anthropic_api_key", True, None,
        (Capability("llm.reasoning", "Agent reasoning, writing and analysis through the paid API", False, paid=True,
                    note="not built: Claude Code on this computer is the brain instead; a Claude subscription does not include API usage"),),
    ),
    "youtube": Provider(
        "youtube", "YouTube Data/Analytics API", True, "google_oauth_client_secret", True, None,
        (Capability("youtube.oauth", "Sign in to a channel with OAuth (PKCE) and confirm its identity", True,
                    note="tested against a mock Google; not yet run against the real service"),
         Capability("youtube.upload", "Upload, thumbnail, scheduling and read-back verification", True,
                    note="tested against a mock Google; the owner's first real upload (selftest) is its live test"),
         Capability("youtube.analytics", "Read views, watch time, retention and similar figures; unavailable ones are recorded as unavailable", True,
                    note="needs the analytics permission on the channel; tested against a mock Google"),
         Capability("youtube.comments", "Read comments and post owner-approved replies, each read back to confirm", True,
                    note="needs the comments permission on the channel; tested against a mock Google")),
        docs="docs/YOUTUBE.md",
    ),
    "canva": Provider(
        "canva", "Canva", False, None, False, None,
        (Capability("design.manual", "Design in Canva by hand; import the export as an asset", True,
                    note="manual: no automation is assumed"),),
    ),
}


def _key_present(p: Provider) -> bool:
    if not p.needs_key:
        return True
    v = getattr(get_settings(), p.key_setting or "", None)
    return v is not None and bool(v.get_secret_value().strip())


def capability_state(db: Session, provider_id: str, capability_id: str) -> tuple[str, str]:
    """Return ``(state, detail)``; state ``ready`` means the call may proceed."""
    p = REGISTRY.get(provider_id)
    if p is None:
        return "unknown_provider", f"no provider named '{provider_id}'"
    cap = next((c for c in p.capabilities if c.id == capability_id), None)
    if cap is None:
        return "unknown_capability", f"{provider_id} has no capability '{capability_id}'"
    if not cap.implemented:
        return "not_implemented", cap.note or "no adapter has been written for this capability"
    if p.needs_tool and shutil.which(p.needs_tool) is None:
        return "missing_tool", f"{p.needs_tool} is not installed"
    locator = p.extra.get("locator")
    if locator is not None and locator() is None:
        return "missing_tool", "the `claude` program was not found: install Claude Code and sign in, or set YTC_CLAUDE_CODE_BIN in .env"
    if p.needs_key and not _key_present(p):
        return "not_configured", f"no API key is configured ({p.key_setting})"
    if p.needs_attestation:
        att = settings_svc.get(db, "provider_attestations").get(provider_id)
        if not att or not att.get("api_access_confirmed") or (cap.paid and not att.get("commercial_use_confirmed")):
            if not p.needs_key:
                return "needs_attestation", "the owner has not yet confirmed in Settings (Providers) that their own plan allows this use"
            return "needs_attestation", "the owner has not confirmed API access and commercial-use terms for this provider in Settings"
    if settings_svc.is_emergency_paused(db):
        return "paused", "emergency pause is active"
    return "ready", "ready"


def require_ready(db: Session, provider_id: str, capability_id: str, *, estimated_micro_usd: int = 0, category: str = "other",
                  video_id: int | None = None) -> None:
    """Raise ``ProviderUnavailable`` / ``BudgetExceeded`` / ``ExternalActionBlocked`` unless the call may proceed."""
    state, detail = capability_state(db, provider_id, capability_id)
    if state != "ready":
        raise ProviderUnavailable(provider_id, capability_id, state, detail)
    cap = next(c for c in REGISTRY[provider_id].capabilities if c.id == capability_id)
    if cap.paid:
        control.assert_external_action_allowed(db, "paid_generation")
        finance.authorize(db, estimated_micro_usd=estimated_micro_usd, category=category, video_id=video_id)


def status_report(db: Session) -> list[dict[str, Any]]:
    out = []
    att = settings_svc.get(db, "provider_attestations")
    for p in REGISTRY.values():
        caps = []
        for c in p.capabilities:
            state, detail = capability_state(db, p.id, c.id)
            caps.append({"id": c.id, "description": c.description, "implemented": c.implemented, "verified_live": c.verified_live,
                         "paid": c.paid, "state": state, "detail": detail, "note": c.note})
        out.append({"id": p.id, "name": p.name, "needs_key": p.needs_key, "key_configured": _key_present(p) if p.needs_key else None,
                    "needs_attestation": p.needs_attestation, "attestation": att.get(p.id), "capabilities": caps})
    return out
