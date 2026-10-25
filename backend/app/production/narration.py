"""Narration through ElevenLabs text-to-speech (medicine and story channels, where the script is spoken).

NOT verified against a live account — see ``app/providers/elevenlabs.py``. Everything around the call is real:
the provider gate, the owner's attestation, the budget, the pause, the one-billing-per-script rule, provenance and
usage records.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from typing import Any

from sqlalchemy import select

from app.config import get_settings
from app.db import session_scope
from app.enums import CostKind
from app.models.production import Asset
from app.production import assets as assets_svc
from app.production import briefs, scripts
from app.production.pipeline import _role_actor, adir
from app.providers import base as providers
from app.providers.elevenlabs import AuthError, ElevenLabsClient, ProviderError, RateLimited
from app.security.permissions import Actor
from app.services import finance
from app.services import settings as settings_svc
from app.worker.runner import BlockedError, HandlerResult, NonRetryableError, TaskContext, handler

# Tests replace this to inject a mock transport; production uses the real API.
client_factory: Callable[[str], ElevenLabsClient] = lambda key: ElevenLabsClient(key)  # noqa: E731


def _duration(path) -> float | None:
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                             capture_output=True, text=True, timeout=60, check=False)
        return float(out.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


@handler("production.generate_narration")
def generate_narration(ctx: TaskContext) -> HandlerResult:
    ctx.check_paused()
    spec: dict[str, Any] = ctx.input_refs.get("spec") or {}
    voice_id = spec.get("voice_id")
    model_id = spec.get("tts_model", "eleven_multilingual_v2")
    if not voice_id:
        raise NonRetryableError("a voice_id must be chosen (and its licence checked) before narration can be generated")
    with session_scope() as db:
        try:
            briefs.require_approved(db, ctx.video_id)  # type: ignore[arg-type]
        except briefs.BriefError as e:
            raise BlockedError("BRIEF_NOT_APPROVED", str(e)) from e
        sv = scripts.latest(db, ctx.video_id, "narration") or scripts.latest(db, ctx.video_id, "script")  # type: ignore[arg-type]
        if sv is None:
            raise NonRetryableError("no script has been saved to narrate")
        text, version = scripts.spoken_text(sv.text), sv.version
        # never bill the same script twice
        prior = db.scalar(select(Asset).where(Asset.video_id == ctx.video_id, Asset.kind == "narration", Asset.provider == "elevenlabs"))
        if prior is not None and prior.params.get("script_version") == version and prior.params.get("voice_id") == voice_id:
            return HandlerResult(result={"asset_id": prior.id, "reused": True, "note": "this script was already narrated; nothing was billed again"},
                                 evidence=[{"kind": "file", "ref": prior.path, "note": "existing narration"}])
        providers.require_ready(db, "elevenlabs", "speech.tts", category="audio", video_id=ctx.video_id)
        key = get_settings().elevenlabs_api_key.get_secret_value()  # type: ignore[union-attr]
        att = settings_svc.get(db, "provider_attestations").get("elevenlabs", {})
    client = client_factory(key)
    try:
        ctx.mark_external_action_started({"provider": "elevenlabs", "operation": "text-to-speech", "characters": len(text)})
        speech = client.synthesize(text, voice_id, model_id=model_id)
    except AuthError as e:
        ctx.clear_external_action_started()
        raise BlockedError("PROVIDER_AUTH", f"ElevenLabs refused the request ({e.status}); check the key and that the plan allows API use.") from e
    except RateLimited as e:
        ctx.clear_external_action_started()
        raise ProviderError(f"rate limited; retry after {e.retry_after or 'a while'}s", 429) from e
    except ProviderError:
        raise
    finally:
        client.close()
    out = adir(ctx.video_id) / f"narration-v{version}.mp3"  # type: ignore[arg-type]
    out.write_bytes(speech.audio)  # keep the paid-for audio on disk before anything else can go wrong
    with session_scope() as db:  # the spend is recorded first, in its own transaction, by the system (workers cannot write costs)
        finance.record_usage(db, Actor.system("provider-gate"), provider="elevenlabs", operation="text-to-speech", units=speech.characters,
                             unit_type="characters", cost_micro_usd=0, cost_kind=CostKind.ESTIMATE, category="audio", video_id=ctx.video_id,
                             task_id=ctx.task_id, meta={"note": "price per character unknown; counted against plan credits", "model": speech.model_id})
    with session_scope() as db:
        actor = _role_actor(db, ctx)
        a = assets_svc.register(
            db, actor, video_id=ctx.video_id, kind="narration", role="narration", path=out, provider="elevenlabs", model=speech.model_id,  # type: ignore[arg-type]
            generation_ref=speech.request_id or f"no-request-id:{ctx.task_id}", prompt=text[:2000], license_type="provider-terms",
            license_ref=f"ElevenLabs terms as attested by the owner ({att.get('confirmed_at')}): {att.get('note') or 'no note'}",
            commercial_use="yes" if att.get("commercial_use_confirmed") else "unknown",
            params={"voice_id": voice_id, "script_version": version, "characters": speech.characters, "duration_s": _duration(out),
                    "vocals": False}, task_id=ctx.task_id)
        aid, apath = a.id, a.path
    return HandlerResult(result={"asset_id": aid, "characters": speech.characters, "request_id": speech.request_id, "reused": False},
                         evidence=[{"kind": "file", "ref": apath, "note": "ElevenLabs narration (MP3)"}])
