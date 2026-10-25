"""Secret redaction for logs, audit details and API output."""

from __future__ import annotations

import logging
import re
from typing import Any

REDACTED = "[REDACTED]"

_SENSITIVE_KEY = re.compile(
    r"(pass(word)?|secret|token|api[_-]?key|authorization|cookie|credential|private[_-]?key|client[_-]?secret)",
    re.IGNORECASE,
)
_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),  # Anthropic
    re.compile(r"\bsk_[A-Za-z0-9]{16,}"),  # ElevenLabs style
    re.compile(r"\bsk-[A-Za-z0-9]{20,}"),  # generic sk- keys
    re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),  # Google API key
    re.compile(r"ya29\.[0-9A-Za-z_\-]{10,}"),  # Google OAuth access token
    re.compile(r"\b1//[0-9A-Za-z_\-]{20,}"),  # Google refresh token
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=\-]{8,}"),
    re.compile(r"(?i)(xi-api-key|x-api-key)\s*[:=]\s*\S+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
]
_known_secrets: set[str] = set()


def register_secrets(values: list[str]) -> None:
    """Exact secret values (from config) are always masked, whatever they look like."""
    for v in values:
        if v and len(v) >= 6:
            _known_secrets.add(v)


def redact_text(text: str) -> str:
    for secret in _known_secrets:
        if secret in text:
            text = text.replace(secret, REDACTED)
    for pat in _PATTERNS:
        text = pat.sub(REDACTED, text)
    return text


def redact(obj: Any) -> Any:
    """Return a deep copy of ``obj`` with secrets masked (never mutates the input)."""
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, dict):
        out: dict[Any, Any] = {}
        for k, v in obj.items():
            if isinstance(k, str) and _SENSITIVE_KEY.search(k) and v not in (None, "", False, 0):
                out[k] = REDACTED
            else:
                out[k] = redact(v)
        return out
    if isinstance(obj, (list, tuple)):
        return [redact(v) for v in obj]
    return obj


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact_text(record.getMessage())
            record.args = None
        except Exception:  # never let logging fail because of redaction
            pass
        return True


def install_log_redaction() -> None:
    root = logging.getLogger()
    for handler in root.handlers:
        if not any(isinstance(f, RedactingFilter) for f in handler.filters):
            handler.addFilter(RedactingFilter())
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        for handler in logging.getLogger(name).handlers:
            if not any(isinstance(f, RedactingFilter) for f in handler.filters):
                handler.addFilter(RedactingFilter())
