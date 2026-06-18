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

ROLE-BASED ACCESS (v4):
    - Admin: Full access to all features
    - Staff: Access to basic tools (Quotation, VTOP WEB)
    
    Decorators:
        @require_auth       → Any authenticated user (admin or staff)
        @require_admin      → Admin only (redirects staff to /home)
        @require_staff      → Staff or admin (staff can access basic features)
        @require_auth_api   → API endpoint for any authenticated user
"""

import logging
import os
from functools import wraps
from typing import Tuple, Optional

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

# ── Logging ────────────────────────────────────────────────────────────────────
log = logging.getLogger("vikram.auth")

# ── Configuration ─────────────────────────────────────────────────────────────
_SECRET_KEY = os.environ.get("SECRET_KEY", "")
if not _SECRET_KEY:
    log.warning("SECRET_KEY env var is not set — session tokens will be insecure!")

_serializer = URLSafeTimedSerializer(_SECRET_KEY)

SESSION_MAX_AGE = 60 * 60 * 24 * 7   # 7 days
COOKIE_NAME     = "vikram_session"


# ── User Roles ────────────────────────────────────────────────────────────────

class UserRole:
    """
    User role constants for role-based access control.
    
    Usage:
        role = UserRole.ADMIN  # "admin"
        role = UserRole.STAFF  # "staff"
    """
    ADMIN = "admin"
    STAFF = "staff"


# ── Token helpers ─────────────────────────────────────────────────────────────

def make_session_token(role: str = UserRole.STAFF) -> str:
    """
    Create a signed session token with role information.
    
    Args:
        role: The user's role (UserRole.ADMIN or UserRole.STAFF)
    
    Returns:
        A signed session token string
    
    Example:
        token = make_session_token(UserRole.ADMIN)
        # Returns: "eyJhbGciOiJIUzI1NiIs..."
    """
    return _serializer.dumps({"authenticated": True, "role": role})


def verify_session_token(token: str) -> Tuple[bool, Optional[str]]:
    """
    Verify a session token and extract the user's role.
    
    Args:
        token: The session token to verify
    
    Returns:
        Tuple of (is_valid, role):
            - is_valid: True if token is valid and not expired
            - role: The user's role (UserRole.ADMIN or UserRole.STAFF) if valid
    
    Example:
        is_valid, role = verify_session_token(token)
        if is_valid:
            print(f"User role: {role}")
    """
    try:
        data = _serializer.loads(token, max_age=SESSION_MAX_AGE)
        if isinstance(data, dict) and data.get("authenticated"):
            return True, data.get("role", UserRole.STAFF)
        return False, None
    except (BadSignature, SignatureExpired):
        return False, None


def get_token_from_request(request: Request) -> Optional[str]:
    """
    Extract the session token from the request cookies.
    
    Args:
        request: FastAPI Request object
    
    Returns:
        The session token string, or None if not present
    """
    return request.cookies.get(COOKIE_NAME)


def get_user_role(request: Request) -> Optional[str]:
    """
    Get the user's role from the session token in the request.
    
    Args:
        request: FastAPI Request object
    
    Returns:
        The user's role (UserRole.ADMIN or UserRole.STAFF), or None if not authenticated
    """
    token = get_token_from_request(request)
    if not token:
        return None
    is_valid, role = verify_session_token(token)
    return role if is_valid else None


def is_authenticated(request: Request) -> bool:
    """
    Check if the request has a valid session (admin or staff).
    
    Args:
        request: FastAPI Request object
    
    Returns:
        True if authenticated, False otherwise
    """
    token = get_token_from_request(request)
    if not token:
        return False
    is_valid, _ = verify_session_token(token)
    return is_valid


def is_admin(request: Request) -> bool:
    """
    Check if the current user is an admin.
    
    Args:
        request: FastAPI Request object
    
    Returns:
        True if user is admin, False otherwise
    """
    return get_user_role(request) == UserRole.ADMIN


def is_staff(request: Request) -> bool:
    """
    Check if the current user is a staff member.
    
    Args:
        request: FastAPI Request object
    
    Returns:
        True if user is staff, False otherwise
    """
    return get_user_role(request) == UserRole.STAFF


# ── Backward Compatibility ────────────────────────────────────────────────────

# Legacy functions - kept for backward compatibility with existing code
# These still work but don't support roles (default to staff)

def make_session_token_legacy() -> str:
    """
    Legacy token creator (backward compatible).
    Creates a token without role information.
    Use make_session_token(role) instead for new code.
    """
    return _serializer.dumps("authenticated")


def verify_session_token_legacy(token: str) -> bool:
    """
    Legacy token verifier (backward compatible).
    Returns True if token is valid.
    Use verify_session_token(token) instead for new code.
    """
    try:
        _serializer.loads(token, max_age=SESSION_MAX_AGE)
        return True
    except (BadSignature, SignatureExpired):
        return False


def _is_authenticated_legacy(request: Request) -> bool:
    """
    Legacy authentication check (backward compatible).
    """
    token = get_token_from_request(request)
    return bool(token and verify_session_token_legacy(token))


# ── Decorators ─────────────────────────────────────────────────────────────────

def require_auth(func):
    """
    Page-level auth guard - any authenticated user (admin or staff).
    Unauthenticated → HTTP 302 redirect to /login.
    Use on routes that return HTML.
    
    Example:
        @router.get("/dashboard")
        @require_auth
        async def dashboard(request: Request):
            return templates.TemplateResponse("dashboard.html", {"request": request})
    """
    @wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        # First try the new role-based system
        if not is_authenticated(request):
            # Fall back to legacy system for backward compatibility
            if not _is_authenticated_legacy(request):
                log.warning("Unauth page access: %s", request.url.path)
                return RedirectResponse("/login", status_code=302)
        return await func(request, *args, **kwargs)
    return wrapper


def require_auth_api(func):
    """
    API-level auth guard - any authenticated user (admin or staff).
    Unauthenticated → HTTP 401 JSON {"error": "Unauthorized"}.
    Use on routes called via fetch() from the frontend.
    
    The JS must handle the 401:
        if (resp.status === 401) { window.location.href = '/login'; return; }
    
    Example:
        @router.get("/api/search")
        @require_auth_api
        async def api_search(request: Request):
            return JSONResponse({"results": []})
    """
    @wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        # First try the new role-based system
        if not is_authenticated(request):
            # Fall back to legacy system for backward compatibility
            if not _is_authenticated_legacy(request):
                log.warning("Unauth API access: %s", request.url.path)
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
        return await func(request, *args, **kwargs)
    return wrapper


def require_admin(func):
    """
    Admin-only page-level auth guard.
    Non-admin users → HTTP 302 redirect to /home.
    Use on routes that should only be accessible by admins.
    
    Example:
        @router.get("/dashboard")
        @require_admin
        async def dashboard(request: Request):
            return templates.TemplateResponse("dashboard.html", {"request": request})
    """
    @wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        if not is_admin(request):
            # Check if user is authenticated but not admin
            if is_authenticated(request):
                log.warning("Non-admin access attempt to %s", request.url.path)
                return RedirectResponse("/home", status_code=302)
            # If not authenticated at all, redirect to login
            log.warning("Unauthenticated access attempt to %s", request.url.path)
            return RedirectResponse("/login", status_code=302)
        return await func(request, *args, **kwargs)
    return wrapper


def require_staff(func):
    """
    Staff-level page-level auth guard.
    Staff or admin users can access.
    Unauthenticated → HTTP 302 redirect to /login.
    
    Use on routes that should be accessible by both staff and admins.
    
    Example:
        @router.get("/voucher")
        @require_staff
        async def voucher_home(request: Request):
            return templates.TemplateResponse("voucher.html", {"request": request})
    """
    @wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        # Check if authenticated (admin or staff)
        if not is_authenticated(request):
            # Fall back to legacy system for backward compatibility
            if not _is_authenticated_legacy(request):
                log.warning("Unauthenticated access attempt to %s", request.url.path)
                return RedirectResponse("/login", status_code=302)
        return await func(request, *args, **kwargs)
    return wrapper


# ── Convenience Functions ─────────────────────────────────────────────────────

def get_current_user_info(request: Request) -> dict:
    """
    Get information about the current user.
    
    Args:
        request: FastAPI Request object
    
    Returns:
        Dictionary with user info:
            - authenticated: bool
            - role: str or None
            - is_admin: bool
            - is_staff: bool
    """
    role = get_user_role(request)
    return {
        "authenticated": is_authenticated(request),
        "role": role,
        "is_admin": role == UserRole.ADMIN,
        "is_staff": role == UserRole.STAFF,
    }


# ── Export List ──────────────────────────────────────────────────────────────

__all__ = [
    # Classes
    'UserRole',
    
    # Token helpers
    'make_session_token',
    'verify_session_token',
    'get_token_from_request',
    'get_user_role',
    'is_authenticated',
    'is_admin',
    'is_staff',
    
    # Legacy helpers (backward compatibility)
    'make_session_token_legacy',
    'verify_session_token_legacy',
    
    # Decorators
    'require_auth',
    'require_auth_api',
    'require_admin',
    'require_staff',
    
    # Convenience
    'get_current_user_info',
]