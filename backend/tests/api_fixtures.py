"""Shared fixtures for the API test modules."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.agents.seed import seed_organization
from app.db import session_scope
from app.main import create_app
from app.security import passwords

PW = "Correct-Horse-9"
PUBLIC = {("GET", "/api/health"), ("GET", "/api/auth/status"), ("POST", "/api/auth/login"),
          ("POST", "/api/auth/bootstrap")}
DOCS = {"/api/docs", "/api/openapi.json"}


@pytest.fixture(autouse=True)
def fast_hashing(monkeypatch):
    monkeypatch.setattr(passwords, "_N", 2**10)


@pytest.fixture
def api_env(env):
    with session_scope() as s:
        seed_organization(s)
    return env


def make_client(host="127.0.0.1") -> TestClient:
    return TestClient(create_app(), base_url="http://localhost", client=(host, 50000), raise_server_exceptions=False)


@pytest.fixture
def client(api_env) -> TestClient:
    return make_client()


def bootstrap(c: TestClient) -> str:
    r = c.post("/api/auth/bootstrap", json={"username": "owner", "password": PW})
    assert r.status_code == 200, r.text
    return r.json()["csrf_token"]


@pytest.fixture
def owner_client(client) -> TestClient:
    client.headers["x-csrf-token"] = bootstrap(client)
    return client


@pytest.fixture
def viewer_client(owner_client, api_env) -> TestClient:
    r = owner_client.post("/api/auth/users", json={"username": "viewer", "password": PW, "role": "viewer"})
    assert r.status_code == 200, r.text
    c = make_client()
    r = c.post("/api/auth/login", json={"username": "viewer", "password": PW})
    assert r.status_code == 200
    c.headers["x-csrf-token"] = r.json()["csrf_token"]
    return c
