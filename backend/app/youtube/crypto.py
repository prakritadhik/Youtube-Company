"""Encryption for the OAuth tokens and upload-session URLs kept in the database.

A refresh token is a standing permission to publish on the owner's channel, so it is never stored in clear text. The key
lives in ``data/secrets/token.key`` (file mode 0600 where the OS supports it), *outside the database*: a copy of the
database or of a database backup alone is not enough to use the tokens.

What this does not protect against: anyone who can read the whole ``data`` folder (they get the key as well), or malware
running as the owner. Windows disk encryption (BitLocker) and a private user account are the answer to those.

If the key file is lost the tokens cannot be read; the owner reconnects the channel. The key is never regenerated while
encrypted values exist, so a missing key cannot silently turn into a second, incompatible key.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


class KeyMissing(RuntimeError):
    """Encrypted values exist but the key that opens them does not."""


class TokenBox:
    def __init__(self, key_path: Path | None = None) -> None:
        self.key_path = key_path or (get_settings().secrets_dir / "token.key")

    def _load(self, *, create: bool) -> Fernet:
        if self.key_path.is_file():
            return Fernet(self.key_path.read_bytes().strip())
        if not create:
            raise KeyMissing(
                f"The token key {self.key_path} is missing, so stored YouTube permissions cannot be read. Reconnect the channel "
                "(Settings, YouTube) to create new ones.")
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        tmp = self.key_path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(key)
        tmp.replace(self.key_path)
        with contextlib.suppress(OSError):
            os.chmod(self.key_path, 0o600)
        return Fernet(key)

    def encrypt(self, plain: str) -> str:
        return self._load(create=True).encrypt(plain.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        try:
            return self._load(create=False).decrypt(token.encode("ascii")).decode("utf-8")
        except InvalidToken as e:
            raise KeyMissing("The stored value cannot be decrypted with the current token key. Reconnect the channel.") from e
