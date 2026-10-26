"""A write must be committed BEFORE the client is told it succeeded.

Found on a clean Windows machine in CI: right after sign-in the very next request said "Not signed in", because the server sent the login response and
only then committed the new session. A browser sends its next requests the instant the response arrives, so this could sign an owner straight back out.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3

from app.config import get_settings
from app.main import create_app
from tests.api_fixtures import PW, api_env, fast_hashing  # noqa: F401


def rows_visible_elsewhere(table: str) -> int:
    path = get_settings().database_url.removeprefix("sqlite:///")
    con = sqlite3.connect(path, timeout=5)
    try:
        return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608 - a fixed table name from this test
    finally:
        con.close()


async def post(app, path: str, body: dict, on_start) -> int:
    """Call the ASGI app directly and run ``on_start`` at the instant the response headers would go out to the client."""
    raw = json.dumps(body).encode()
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "scheme": "http", "path": path, "raw_path": path.encode(),
             "query_string": b"", "root_path": "", "client": ("127.0.0.1", 50000), "server": ("localhost", 80),
             "headers": [(b"host", b"localhost"), (b"content-type", b"application/json"), (b"content-length", str(len(raw)).encode())]}
    sent = {"done": False}
    status = {"code": 0}

    async def receive():
        if not sent["done"]:
            sent["done"] = True
            return {"type": "http.request", "body": raw, "more_body": False}
        await asyncio.sleep(3600)

    async def send(message):
        if message["type"] == "http.response.start":
            status["code"] = message["status"]
            on_start()

    await app(scope, receive, send)
    return status["code"]


def test_the_sign_in_is_committed_before_the_response_is_sent(api_env):
    app = create_app()
    seen = {}

    async def run():
        assert await post(app, "/api/auth/bootstrap", {"username": "owner", "password": PW}, lambda: seen.setdefault("bootstrap", rows_visible_elsewhere("user_sessions"))) == 200
        assert await post(app, "/api/auth/login", {"username": "owner", "password": PW}, lambda: seen.setdefault("login", rows_visible_elsewhere("user_sessions"))) == 200

    asyncio.run(run())
    assert seen["bootstrap"] == 1, "the owner's first session must already be saved when the response goes out"
    assert seen["login"] == 2, "the new session must already be saved when the response goes out"
