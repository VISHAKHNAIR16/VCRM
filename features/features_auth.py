"""
features/auth.py
────────────────
Shared authentication helpers for VIKRAM CMS.

Imported by:
    main.py                      → make_session_token, verify_session_token, require_auth
    features/quotation/router.py → require_auth, require_auth_api

TWO decorator patterns are provided because HTML routes and JSON API
routes need different failure behaviour:
    @require_auth     → redirects to /login (for pages served as HTML)
    @require_auth_api → returns 401 JSON  (for fetch() endpoints;
                        a 302 on fetch() is silently swallowed by the browser)

The JS in quotation.html handles the 401 and redirects the user itself.
"""

import logging
import os
from functools import wraps

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

log = logging.getLogger("vikram.auth")

_SECRET_KEY = os.environ.get("SECRET_KEY", "")
if not _SECRET_KEY:
    log.warning("SECRET_KEY env var is not set — session tokens will be insecure!")

_serializer = URLSafeTimedSerializer(_SECRET_KEY)

SESSION_MAX_AGE = 60 * 60 * 24 * 7   # 7 days
COOKIE_NAME     = "vikram_session"


# ── Token helpers ──────────────────────────────────────────────────────────────

def make_session_token() -> str:
    """Create a signed session token for a freshly-authenticated user."""
    return _serializer.dumps("authenticated")


def verify_session_token(token: str) -> bool:
    """Return True if the token is valid and not expired."""
    try:
        _serializer.loads(token, max_age=SESSION_MAX_AGE)
        return True
    except (BadSignature, SignatureExpired):
        return False


def _is_authenticated(request: Request) -> bool:
    token = request.cookies.get(COOKIE_NAME)
    return bool(token and verify_session_token(token))


# ── Decorators ─────────────────────────────────────────────────────────────────

def require_auth(func):
    """
    Page-level auth guard.
    Unauthenticated → HTTP 302 redirect to /login.
    Use on routes that return HTML (users land on the login page).
    """
    @wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        if not _is_authenticated(request):
            log.warning("Unauth page access: %s", request.url.path)
            return RedirectResponse("/login", status_code=302)
        return await func(request, *args, **kwargs)
    return wrapper


def require_auth_api(func):
    """
    API-level auth guard.
    Unauthenticated → HTTP 401 JSON {"error": "Unauthorized"}.
    Use on routes called via fetch() from the frontend.

    The JS must handle the 401:
        if (resp.status === 401) { window.location.href = '/login'; return; }
    """
    @wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        if not _is_authenticated(request):
            log.warning("Unauth API access: %s", request.url.path)
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        return await func(request, *args, **kwargs)
    return wrapper
