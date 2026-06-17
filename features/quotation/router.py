"""
features/quotation/router.py
─────────────────────────────
Quotation lookup tool — simplified search-first interface.
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

log = logging.getLogger("vikram.quotation")
router = APIRouter()
BASE = Path(__file__).parent
DB_PATH = BASE / "quotation.db"
templates = Jinja2Templates(directory="templates")

# ── Local auth ─────────────────────────────────────────────────────────────────
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
    @wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        if not _is_authenticated(request):
            log.warning("Unauth page: %s", request.url.path)
            return RedirectResponse("/login", status_code=302)
        return await func(request, *args, **kwargs)
    return wrapper

def _api_guard(func):
    @wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        if not _is_authenticated(request):
            log.warning("Unauth API: %s", request.url.path)
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        return await func(request, *args, **kwargs)
    return wrapper

# ── FX rate cache ──────────────────────────────────────────────────────────────
_fx_cache: dict = {"rate": None, "fetched_at": 0}
FX_TTL = 3600

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

def search_services(query: str, limit: int = 50) -> list[dict]:
    """
    Simple fuzzy search across service names, destinations, and types.
    Returns matching services with their rates.
    """
    with _db() as conn:
        search_term = f"%{query}%"
        
        sql = """
            SELECT DISTINCT s.id, s.service_name, s.destination, s.service_type,
                   s.tour_code, s.duration, s.includes_vat, s.notes, s.source,
                   s.company_code
            FROM services s
            WHERE s.service_name LIKE ? 
               OR s.destination LIKE ?
               OR s.service_type LIKE ?
            ORDER BY 
                CASE 
                    WHEN s.service_name LIKE ? THEN 1
                    WHEN s.destination LIKE ? THEN 2
                    WHEN s.service_type LIKE ? THEN 3
                    ELSE 4
                END,
                s.service_name
            LIMIT ?
        """
        params = [search_term] * 6 + [limit]
        
        service_rows = conn.execute(sql, params).fetchall()
        result = []

        for svc in service_rows:
            sid = svc["id"]
            rate_rows = conn.execute(
                """SELECT rate_type, vehicle, pax_range, pax_category, price_thb 
                   FROM rates WHERE service_id=? 
                   ORDER BY rate_type, vehicle, pax_category""",
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
                "destination":  svc["destination"],
                "service_type": svc["service_type"],
                "tour_code":    svc["tour_code"],
                "duration":     svc["duration"],
                "includes_vat": bool(svc["includes_vat"]) if svc["includes_vat"] is not None else False,
                "notes":        svc["notes"],
                "source":       svc["source"],
                "company_code": svc["company_code"],
                "rates":        [dict(r) for r in rate_rows],
                "zones":        [dict(z) for z in zone_rows],
                "addons":       [dict(a) for a in addon_rows],
            })
    return result

def get_service_by_id(service_id: int) -> dict | None:
    """Get a single service with all its details."""
    with _db() as conn:
        svc = conn.execute(
            """SELECT id, service_name, destination, service_type, tour_code,
                      duration, includes_vat, notes, source, company_code
               FROM services WHERE id = ?""",
            (service_id,)
        ).fetchone()
        
        if not svc:
            return None
        
        rate_rows = conn.execute(
            """SELECT rate_type, vehicle, pax_range, pax_category, price_thb 
               FROM rates WHERE service_id=? 
               ORDER BY rate_type, vehicle, pax_category""",
            (service_id,),
        ).fetchall()
        
        return {
            "id":           svc["id"],
            "name":         svc["service_name"],
            "destination":  svc["destination"],
            "service_type": svc["service_type"],
            "tour_code":    svc["tour_code"],
            "duration":     svc["duration"],
            "includes_vat": bool(svc["includes_vat"]) if svc["includes_vat"] is not None else False,
            "notes":        svc["notes"],
            "source":       svc["source"],
            "company_code": svc["company_code"],
            "rates":        [dict(r) for r in rate_rows],
        }

# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
@_page_guard
async def quotation_page(request: Request):
    """Main quotation page with search-first interface."""
    return templates.TemplateResponse(
        "quotation.html", 
        {"request": request}
    )

@router.get("/api/search")
@_api_guard
async def api_search(
    request: Request,
    q: str = Query(default="", min_length=1),
    limit: int = Query(default=50, ge=1, le=100),
):
    """Search services by name, destination, or type."""
    try:
        results = search_services(q, limit)
        return results
    except FileNotFoundError:
        return JSONResponse({"error": "Database unavailable"}, status_code=503)

@router.get("/api/service/{service_id}")
@_api_guard
async def api_get_service(request: Request, service_id: int):
    """Get full details for a specific service."""
    try:
        result = get_service_by_id(service_id)
        if not result:
            return JSONResponse({"error": "Service not found"}, status_code=404)
        return result
    except FileNotFoundError:
        return JSONResponse({"error": "Database unavailable"}, status_code=503)

@router.get("/api/fx")
@_api_guard
async def api_fx(request: Request):
    return {"thb_per_usd": get_usd_rate(), "source": "exchangerate-api.com"}