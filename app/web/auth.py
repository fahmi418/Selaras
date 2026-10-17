"""
Officer login shared by /portal (FastAPI) and /dashboard (Streamlit, via nginx auth_request).

A single shared password (PORTAL_PASSWORD) issues an HMAC-signed, HttpOnly session cookie.
When PORTAL_PASSWORD is empty the gate is open; startup validation only allows that in demo_mode.
Case pages (/case/{token}) are NOT behind this login: their URL token is the bearer credential.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections import deque
from urllib.parse import quote

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import get_settings

router = APIRouter()
templates = Jinja2Templates(directory="app/web/templates")

COOKIE_NAME = "selaras_session"

# Global (not per-IP) limiter: client IPs behind the proxy chain are spoofable via X-Forwarded-For.
_MAX_FAILURES = 10
_WINDOW_SECONDS = 300
_failures: deque[float] = deque()


class LoginRequired(Exception):
    """Raised by require_login; main.py turns it into a redirect to /login."""

    def __init__(self, next_path: str) -> None:
        self.next_path = next_path


def auth_enabled() -> bool:
    return bool(get_settings().portal_password.get_secret_value())


def _sign(payload: str) -> str:
    key = get_settings().secret_key.get_secret_value().encode()
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()


def make_session_token() -> str:
    expires = int(time.time()) + get_settings().session_ttl_hours * 3600
    return f"{expires}.{_sign(f'session:{expires}')}"


def is_valid_session(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    expires_str, _, signature = token.partition(".")
    if not expires_str.isdigit() or int(expires_str) < time.time():
        return False
    return hmac.compare_digest(signature, _sign(f"session:{expires_str}"))


def require_login(request: Request) -> None:
    """FastAPI dependency: redirect to /login unless authenticated (or auth is disabled)."""
    if not auth_enabled() or is_valid_session(request.cookies.get(COOKIE_NAME)):
        return
    target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    raise LoginRequired(target)


def _safe_next(value: str | None) -> str:
    """Only same-site absolute paths; blocks open redirects such as //evil.com or /\\evil.com."""
    if value and value.startswith("/") and not value.startswith(("//", "/\\")):
        return value
    return "/portal"


def _too_many_failures() -> bool:
    cutoff = time.time() - _WINDOW_SECONDS
    while _failures and _failures[0] < cutoff:
        _failures.popleft()
    return len(_failures) >= _MAX_FAILURES


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/portal"):
    if not auth_enabled() or is_valid_session(request.cookies.get(COOKIE_NAME)):
        return RedirectResponse(_safe_next(next), status_code=303)
    return templates.TemplateResponse(
        request=request, name="login.html", context={"next": _safe_next(next), "error": None}
    )


@router.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request, password: str = Form(...), next: str = Form("/portal")):
    if not auth_enabled():
        return RedirectResponse(_safe_next(next), status_code=303)

    if _too_many_failures():
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"next": _safe_next(next), "error": "Terlalu banyak percobaan. Coba lagi beberapa menit lagi."},
            status_code=429,
        )

    expected = get_settings().portal_password.get_secret_value()
    if not hmac.compare_digest(password.encode(), expected.encode()):
        _failures.append(time.time())
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"next": _safe_next(next), "error": "Kata sandi salah."},
            status_code=401,
        )

    response = RedirectResponse(_safe_next(next), status_code=303)
    secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    response.set_cookie(
        COOKIE_NAME,
        make_session_token(),
        max_age=get_settings().session_ttl_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )
    return response


@router.post("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@router.get("/auth/check")
async def auth_check(request: Request):
    """nginx auth_request target: 204 if the session cookie is valid (or auth disabled), else 401."""
    if not auth_enabled() or is_valid_session(request.cookies.get(COOKIE_NAME)):
        return Response(status_code=204)
    return Response(status_code=401)
