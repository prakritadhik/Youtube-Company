"""Password hashing with stdlib scrypt (no native dependency, works on Windows)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

_N, _R, _P = 2**15, 8, 1
_MAXMEM = 128 * _N * _R * 2
MIN_PASSWORD_LENGTH = 12


class WeakPassword(ValueError):
    pass


def validate_password_strength(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise WeakPassword(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if password.lower() == password or password.upper() == password or not any(c.isdigit() for c in password):
        raise WeakPassword("Password must mix upper and lower case letters and include a digit.")


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def hash_password(password: str, *, n: int | None = None, r: int = _R, p: int = _P) -> str:
    n = n or _N  # looked up at call time so tests can lower the cost
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=128 * n * r * 2, dklen=32)
    return f"scrypt${n}${r}${p}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n_s, r_s, p_s, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        n, r, p = int(n_s), int(r_s), int(p_s)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        dk = hashlib.scrypt(
            password.encode(), salt=salt, n=n, r=r, p=p, maxmem=128 * n * r * 2, dklen=len(expected)
        )
        return hmac.compare_digest(dk, expected)
    except (ValueError, TypeError):
        return False


# Used to burn comparable time when the username does not exist (reduces user enumeration).
DUMMY_HASH = hash_password("not-a-real-password-Dummy1")
