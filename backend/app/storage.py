"""Artifact storage under ``data/``. All paths are stored relative and validated on access."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

from app.config import get_settings


class UnsafePath(ValueError):
    pass


def data_root() -> Path:
    root = get_settings().data_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_data_path(rel: str) -> Path:
    """Resolve a stored relative path, refusing anything that escapes ``data/``."""
    if not rel or "\x00" in rel:
        raise UnsafePath("empty or invalid path")
    pure = PurePosixPath(rel.replace("\\", "/"))
    if pure.is_absolute() or ".." in pure.parts or (pure.parts and ":" in pure.parts[0]):
        raise UnsafePath(f"path must be relative to the data directory: {rel!r}")
    root = data_root()
    full = (root / Path(*pure.parts)).resolve()
    if root != full and root not in full.parents:
        raise UnsafePath(f"path escapes the data directory: {rel!r}")
    return full


def relative_to_data(path: Path) -> str:
    return path.resolve().relative_to(data_root()).as_posix()


def sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def write_bytes_artifact(subdir: str, name: str, data: bytes) -> str:
    """Write a file under ``data/artifacts/<subdir>/`` and return its relative path."""
    rel = PurePosixPath("artifacts") / subdir / name
    target = resolve_data_path(rel.as_posix())
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(target)
    return rel.as_posix()


def write_json_artifact(subdir: str, name: str, payload: Any) -> str:
    data = json.dumps(payload, indent=2, sort_keys=True, default=str).encode()
    return write_bytes_artifact(subdir, name, data)
