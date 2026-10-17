"""Officer login for /portal and /dashboard, plus startup validation for DEMO_MODE=false."""

import time

import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from app.config import Settings, get_settings, insecure_settings
from app.web import auth

PASSWORD = "rahasia-uji-123"


@pytest.fixture
def login_enabled(monkeypatch):
    monkeypatch.setattr(get_settings(), "portal_password", SecretStr(PASSWORD))
    auth._failures.clear()
    yield
    auth._failures.clear()


async def test_portal_is_open_when_no_password_configured(client):
    assert (await client.get("/portal")).status_code == 200
    assert (await client.get("/auth/check")).status_code == 204


async def test_portal_redirects_to_login_when_enabled(client, login_enabled):
    r = await client.get("/portal?officer_id=officer-02")
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login?next=")
    assert "officer-02" in r.headers["location"] or "officer_id" in r.headers["location"]
    assert (await client.get("/auth/check")).status_code == 401


async def test_login_flow_sets_httponly_cookie_and_unlocks_portal_and_dashboard_check(client, login_enabled):
    r = await client.post("/login", data={"password": "salah", "next": "/portal"})
    assert r.status_code == 401
    assert "Kata sandi salah" in r.text

    r = await client.post("/login", data={"password": PASSWORD, "next": "/portal"})
    assert r.status_code == 303 and r.headers["location"] == "/portal"
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie

    assert (await client.get("/portal")).status_code == 200
    assert (await client.get("/auth/check")).status_code == 204

    r = await client.post("/logout")
    assert r.status_code == 303
    assert (await client.get("/portal")).status_code == 303


async def test_login_next_cannot_redirect_off_site(client, login_enabled):
    for evil in ("//evil.example", "/\\evil.example", "https://evil.example"):
        r = await client.post("/login", data={"password": PASSWORD, "next": evil})
        assert r.status_code == 303
        assert r.headers["location"] == "/portal", evil


async def test_login_is_rate_limited_after_repeated_failures(client, login_enabled):
    for _ in range(auth._MAX_FAILURES):
        assert (await client.post("/login", data={"password": "x"})).status_code == 401
    r = await client.post("/login", data={"password": PASSWORD})
    assert r.status_code == 429


def test_session_tokens_reject_tampering_and_expiry(monkeypatch):
    token = auth.make_session_token()
    assert auth.is_valid_session(token)

    expires, _, sig = token.partition(".")
    assert not auth.is_valid_session(f"{int(expires) + 99999}.{sig}")  # extended expiry, old signature
    assert not auth.is_valid_session(f"{expires}.{'0' * len(sig)}")
    assert not auth.is_valid_session("garbage") and not auth.is_valid_session(None)

    real_now = time.time()
    monkeypatch.setattr(auth.time, "time", lambda: real_now + 10 * 3600 * 24)
    assert not auth.is_valid_session(token)


def test_insecure_settings_flags_published_defaults():
    weak = Settings(_env_file=None)
    flagged = " ".join(insecure_settings(weak))
    for name in ("ADMIN_API_KEY", "INTERNAL_API_KEY", "SECRET_KEY", "TELEGRAM_WEBHOOK_SECRET", "PORTAL_PASSWORD"):
        assert name in flagged

    strong = Settings(
        _env_file=None,
        admin_api_key="a" * 40, internal_api_key="b" * 40, secret_key="c" * 40,
        telegram_webhook_secret="d" * 40, portal_password="pw-strong-12345",
    )
    assert insecure_settings(strong) == []


async def test_app_refuses_to_start_outside_demo_mode_with_default_keys(monkeypatch):
    from app.main import lifespan

    monkeypatch.setattr(get_settings(), "demo_mode", False)
    with pytest.raises(RuntimeError, match="DEMO_MODE=false"):
        async with lifespan(FastAPI()):
            pass
