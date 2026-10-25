from __future__ import annotations

from app.config import REPO_ROOT, Settings


def test_the_shipped_env_example_loads_and_means_what_it_says(monkeypatch):
    for k in ("YTC_ENV", "YTC_HOST", "YTC_PORT", "ANTHROPIC_API_KEY", "ELEVENLABS_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    s = Settings(_env_file=REPO_ROOT / ".env.example")
    assert s.env == "development" and s.host == "127.0.0.1" and s.port == 8000
    assert s.anthropic_api_key is None and s.elevenlabs_api_key is None  # blank placeholders are "not configured"
    assert s.secret_values() == []


def test_env_example_has_no_inline_comments_after_values():
    for n, line in enumerate((REPO_ROOT / ".env.example").read_text().splitlines(), 1):
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            assert " #" not in stripped, f".env.example line {n} has an inline comment: {line!r}"
