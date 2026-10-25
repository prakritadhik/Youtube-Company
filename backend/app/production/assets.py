"""Asset registry: every file in a video has a recorded origin, licence and integrity hash."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.production import Asset
from app.models.videos import Video
from app.security.permissions import Actor, P
from app.services import audit
from app.storage import UnsafePath, relative_to_data, resolve_data_path, sha256_file

PACKAGE_DIR = Path(__file__).resolve().parent
KINDS = {"image", "music", "narration", "caption", "font", "thumbnail", "clip", "video"}
LICENSE_TYPES = {"owner-generated", "ofl-1.1", "cc0", "public-domain", "provider-terms", "licensed-stock", "unknown"}
LOCAL_PROVIDERS = {"local-procedural", "local-render", "bundled"}  # produced by this software; no third party


class AssetError(ValueError):
    pass


def resolve_asset_path(path: str) -> Path:
    """Bundled files are stored as ``resource:<relative path under app/production>``; everything else is under data/."""
    if path.startswith("resource:"):
        rel = path[len("resource:"):]
        full = (PACKAGE_DIR / rel).resolve()
        if PACKAGE_DIR not in full.parents:
            raise UnsafePath(f"bundled path escapes the package: {path!r}")
        return full
    return resolve_data_path(path)


def register(
    db: Session,
    actor: Actor,
    *,
    video_id: int,
    kind: str,
    role: str,
    path: str | Path,
    provider: str,
    license_type: str,
    commercial_use: str,
    license_ref: str | None = None,
    model: str | None = None,
    generation_ref: str | None = None,
    prompt: str | None = None,
    seed: int | None = None,
    params: dict[str, Any] | None = None,
    attribution: str | None = None,
    source_url: str | None = None,
    simulated: bool = False,
    task_id: int | None = None,
) -> Asset:
    actor.require(P.TASK_WORK)
    if kind not in KINDS:
        raise AssetError(f"unknown asset kind '{kind}'")
    if license_type not in LICENSE_TYPES:
        raise AssetError(f"unknown licence type '{license_type}'")
    if commercial_use not in ("yes", "no", "unknown"):
        raise AssetError("commercial_use must be yes, no or unknown")
    if db.get(Video, video_id) is None:
        raise AssetError("video not found")

    if isinstance(path, Path):
        stored = path.as_posix() if str(path).startswith("resource:") else relative_to_data(path)
    else:
        stored = path
    full = resolve_asset_path(stored)
    if not full.is_file() or full.stat().st_size == 0:
        raise AssetError(f"asset file is missing or empty: {stored}")

    if commercial_use == "yes" and (license_type == "unknown" or not (license_ref or "").strip()):
        raise AssetError("claiming commercial use requires a licence type and a licence reference (URL, file or text)")
    if provider not in LOCAL_PROVIDERS and not (prompt or generation_ref or source_url):
        raise AssetError("an asset from an external provider needs its prompt, generation id or source URL recorded")

    a = Asset(
        video_id=video_id, kind=kind, role=role, path=stored, sha256=sha256_file(full), size_bytes=full.stat().st_size,
        provider=provider, model=model, generation_ref=generation_ref, prompt=prompt, seed=seed, params=params or {},
        license_type=license_type, license_ref=license_ref, commercial_use=commercial_use, attribution=attribution,
        source_url=source_url, simulated=simulated, created_by=actor.label, task_id=task_id,
    )
    db.add(a)
    db.flush()
    audit.record(db, actor, "asset.registered", "asset", a.id,
                 {"video": video_id, "kind": kind, "provider": provider, "license": license_type, "commercial_use": commercial_use})
    return a


def problems_for(asset: Asset) -> list[dict[str, str]]:
    """Everything that stops this asset from being cleared for publication."""
    out: list[dict[str, str]] = []

    def add(code: str, msg: str) -> None:
        out.append({"asset_id": str(asset.id), "role": asset.role, "code": code, "message": msg})

    try:
        full = resolve_asset_path(asset.path)
        if not full.is_file():
            add("FILE_MISSING", "The file is missing.")
        elif sha256_file(full) != asset.sha256:
            add("FILE_CHANGED", "The file changed after it was registered; its provenance no longer applies.")
    except UnsafePath:
        add("FILE_MISSING", "The recorded path is invalid.")
    if asset.simulated:
        add("SIMULATED", "This asset is a stand-in, not a real result.")
    if asset.license_type == "unknown" or not asset.license_type:
        add("NO_LICENSE", "No licence is recorded.")
    if asset.commercial_use == "no":
        add("COMMERCIAL_USE_PROHIBITED", "The licence does not permit commercial use.")
    elif asset.commercial_use != "yes":
        add("COMMERCIAL_USE_UNCONFIRMED", "Commercial use has not been confirmed.")
    if asset.provider not in LOCAL_PROVIDERS and not (asset.prompt or asset.generation_ref or asset.source_url):
        add("NO_PROVENANCE", "The provider, prompt or generation id is not recorded.")
    return out


def for_video(db: Session, video_id: int) -> list[Asset]:
    return list(db.scalars(select(Asset).where(Asset.video_id == video_id).order_by(Asset.id)))


def license_problems(db: Session, video_id: int) -> list[dict[str, str]]:
    return [p for a in for_video(db, video_id) for p in problems_for(a)]


def serialize(a: Asset) -> dict[str, Any]:
    return {
        "id": a.id, "kind": a.kind, "role": a.role, "path": a.path, "sha256": a.sha256, "size_bytes": a.size_bytes,
        "provider": a.provider, "model": a.model, "generation_ref": a.generation_ref, "prompt": a.prompt, "seed": a.seed,
        "license_type": a.license_type, "license_ref": a.license_ref, "commercial_use": a.commercial_use,
        "attribution": a.attribution, "source_url": a.source_url, "simulated": a.simulated, "created_by": a.created_by,
        "problems": problems_for(a),
    }
