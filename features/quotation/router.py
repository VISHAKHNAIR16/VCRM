"""
features/quotation/router.py
─────────────────────────────
Quotation lookup tool — mounted in main.py at /quotation.

Auth is handled locally (reads SECRET_KEY from env).
This avoids any import chain issues — main.py's require_auth
stays in main.py; this router is fully self-contained.

HTML routes  → _page_guard()  — 302 redirect to /login on failure
API  routes  → _api_guard()   — 401 JSON on failure so JS fetch()
                                 can catch it and redirect the user
"""

import logging
import os
import sqlite3
import time
from functools import wraps
from pathlib import Path

import requests
from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

log       = logging.getLogger("vikram.quotation")
router    = APIRouter()
BASE      = Path(__file__).parent
DB_PATH   = BASE / "quotation.db"
templates = Jinja2Templates(directory="templates")

# ── Local auth ─────────────────────────────────────────────────────────────────
# Mirrors main.py's logic exactly (same SECRET_KEY env var, same cookie name).
# Kept local to avoid a shared module that could create import-order issues.
_serializer = URLSafeTimedSerializer(os.environ.get("SECRET_KEY", ""))

def _is_authenticated(request: Request) -> bool:
    token = request.cookies.get("vikram_session")
    if not token:
        return False
    try:
        _serializer.loads(token, max_age=60 * 60 * 24 * 7)
        return True
    except (BadSignature, SignatureExpired):
        return False


def _page_guard(func):
    """For HTML routes — unauthenticated → redirect to /login."""
    @wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        if not _is_authenticated(request):
            log.warning("Unauth page: %s", request.url.path)
            return RedirectResponse("/login", status_code=302)
        return await func(request, *args, **kwargs)
    return wrapper


def _api_guard(func):
    """
    For API (fetch) routes — unauthenticated → 401 JSON.
    Never return 302 on a fetch() endpoint: the browser silently follows
    the redirect and returns the login HTML as if it were JSON.
    The JS checks resp.status === 401 and does window.location.href='/login'.
    """
    @wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        if not _is_authenticated(request):
            log.warning("Unauth API: %s", request.url.path)
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        return await func(request, *args, **kwargs)
    return wrapper


# ── FX rate cache ──────────────────────────────────────────────────────────────
_fx_cache: dict = {"rate": None, "fetched_at": 0}
FX_TTL = 3600   # seconds


def get_usd_rate() -> float | None:
    now = time.time()
    if _fx_cache["rate"] and (now - _fx_cache["fetched_at"]) < FX_TTL:
        return _fx_cache["rate"]
    try:
        resp = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5)
        rate = resp.json()["rates"].get("THB")
        if rate:
            _fx_cache["rate"] = rate
            _fx_cache["fetched_at"] = now
            log.info("FX updated: 1 USD = %.2f THB", rate)
        return rate
    except Exception as exc:
        log.warning("FX fetch failed: %s", exc)
        return _fx_cache.get("rate")


# ── DB helpers ─────────────────────────────────────────────────────────────────

def _db():
    if not DB_PATH.exists():
        raise FileNotFoundError(f"quotation.db not found at {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_destinations() -> list[str]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT destination FROM services ORDER BY destination"
        ).fetchall()
    return [r["destination"] for r in rows]


def get_service_types(destination: str) -> list[str]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT service_type FROM services "
            "WHERE destination=? ORDER BY service_type",
            (destination,),
        ).fetchall()
    return [r["service_type"] for r in rows]


def get_vehicles(destination: str, service_type: str) -> list[str]:
    with _db() as conn:
        rows = conn.execute(
            """SELECT DISTINCT r.vehicle
               FROM rates r JOIN services s ON s.id = r.service_id
               WHERE s.destination=? AND s.service_type=?
                 AND r.vehicle IS NOT NULL
               ORDER BY r.vehicle""",
            (destination, service_type),
        ).fetchall()
    return [r["vehicle"] for r in rows]


def search_services(
    destination: str,
    service_type: str,
    vehicle: str | None,
    rate_type: str | None,
    search: str,
) -> list[dict]:
    with _db() as conn:
        sql = """
            SELECT DISTINCT s.id, s.service_name, s.tour_code,
                   s.duration, s.includes_vat, s.notes, s.source
            FROM services s
            JOIN rates r ON r.service_id = s.id
            WHERE s.destination = ? AND s.service_type = ?
        """
        params: list = [destination, service_type]
        if vehicle:
            sql += " AND (r.vehicle = ? OR r.vehicle IS NULL)"
            params.append(vehicle)
        if rate_type:
            sql += " AND r.rate_type = ?"
            params.append(rate_type)
        if search:
            sql += " AND LOWER(s.service_name) LIKE ?"
            params.append(f"%{search.lower()}%")
        sql += " ORDER BY s.service_name LIMIT 80"

        service_rows = conn.execute(sql, params).fetchall()
        result = []

        for svc in service_rows:
            sid = svc["id"]
            rate_rows = conn.execute(
                "SELECT rate_type, vehicle, pax_range, pax_category, price_thb "
                "FROM rates WHERE service_id=? ORDER BY rate_type, vehicle, pax_category",
                (sid,),
            ).fetchall()
            try:
                zone_rows = conn.execute(
                    "SELECT zone_name, surcharge, per FROM zone_surcharges WHERE service_id=?",
                    (sid,),
                ).fetchall()
            except Exception:
                zone_rows = []
            try:
                addon_rows = conn.execute(
                    "SELECT addon_name, price_adult, price_child FROM addons WHERE service_id=?",
                    (sid,),
                ).fetchall()
            except Exception:
                addon_rows = []

            result.append({
                "id":           sid,
                "name":         svc["service_name"],
                "tour_code":    svc["tour_code"],
                "duration":     svc["duration"],
                "includes_vat": bool(svc["includes_vat"]) if svc["includes_vat"] is not None else False,
                "notes":        svc["notes"],
                "source":       svc["source"],
                "rates":        [dict(r) for r in rate_rows],
                "zones":        [dict(z) for z in zone_rows],
                "addons":       [dict(a) for a in addon_rows],
            })
    return result


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
@_page_guard
async def quotation_page(request: Request):
    try:
        destinations = get_destinations()
    except FileNotFoundError:
        destinations = []
        log.error("quotation.db missing — destinations list will be empty")
    return templates.TemplateResponse(
        "quotation.html", {"request": request, "destinations": destinations}
    )


@router.get("/api/service-types")
@_api_guard
async def api_service_types(request: Request, destination: str = Query(...)):
    return get_service_types(destination)


@router.get("/api/vehicles")
@_api_guard
async def api_vehicles(
    request: Request,
    destination: str  = Query(...),
    service_type: str = Query(...),
):
    return get_vehicles(destination, service_type)


@router.get("/api/search")
@_api_guard
async def api_search(
    request: Request,
    destination: str  = Query(...),
    service_type: str = Query(...),
    vehicle: str      = Query(default=""),
    rate_type: str    = Query(default=""),
    search: str       = Query(default=""),
):
    try:
        return search_services(
            destination=destination,
            service_type=service_type,
            vehicle=vehicle or None,
            rate_type=rate_type or None,
            search=search,
        )
    except FileNotFoundError:
        return JSONResponse({"error": "Database unavailable"}, status_code=503)


@router.get("/api/fx")
@_api_guard
async def api_fx(request: Request):
    return {"thb_per_usd": get_usd_rate(), "source": "exchangerate-api.com"}