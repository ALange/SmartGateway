import pytest
from fastapi.testclient import TestClient

from app import config, db, main
from app.main import app


@pytest.fixture()
def client(monkeypatch):
    db.init_db()
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "pw-for-test")
    main._fails.clear()
    yield TestClient(app)
    main._fails.clear()


def test_wrong_bearer_passwords_are_rate_limited(client):
    for _ in range(5):
        assert client.get("/api/gateways", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/api/gateways", headers={"Authorization": "Bearer nope"}).status_code == 429
    assert client.post("/api/login", json={"password": "pw-for-test"}).status_code == 429   # lock is shared with the login form


def test_requests_without_credentials_do_not_lock_anyone_out(client):
    for _ in range(10):
        assert client.get("/api/gateways").status_code == 401
    assert client.get("/api/gateways", headers={"Authorization": "Bearer pw-for-test"}).status_code == 200


def test_session_dies_when_password_changes(client, monkeypatch):
    token = main.make_session()
    assert main.valid_session(token)
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "another-password")
    assert not main.valid_session(token)


def test_security_headers(client):
    r = client.get("/healthz")
    assert r.headers["x-frame-options"] == "DENY" and "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "x-frame-options" not in client.get("/gw/nope/v1/models").headers   # API surface can be embedded by tools
