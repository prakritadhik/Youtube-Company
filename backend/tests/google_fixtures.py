"""Fixtures that point the YouTube integration at the mock Google and configure a (fake) OAuth client."""

from __future__ import annotations

import pytest

from app.config import reset_settings_cache
from app.youtube import connection
from tests.fake_google import CLIENT_ID, CLIENT_SECRET, FakeGoogle


@pytest.fixture
def google(env, monkeypatch) -> FakeGoogle:
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", CLIENT_SECRET)
    reset_settings_cache()
    fake = FakeGoogle()
    monkeypatch.setattr(connection, "TRANSPORT", fake.transport)
    return fake
