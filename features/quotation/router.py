"""
features/quotation/router.py

Quotation lookup tool — mount in main.py:
    from features.quotation.router import router as quotation_router
    app.include_router(quotation_router, prefix="/quotation", tags=["quotation"])
"""

import sqlite3
import time
import logging
from functools import lru_cache
from pathlib import Path

import requests
from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

log = logging.getLogger("vikram.quotation")

router    = APIRouter()
BASE      = Path(__file__).parent
DB_PATH   = BASE / "quotation.db"
templates = Jinja2Templates(directory="templates")

# ── FX cache (refresh every hour) ────────────────────────────────────────────
_fx_cache: dict = {"rate": None, "fetched_at": 0}
FX_TTL = 3600  # seconds


def get_usd_rate() -> float | None:
    """Return THB per 1 USD from exchangerate-api (free tier, no key needed for base)."""
    now = time.time()
    if _fx_cache["rate"] and (now - _fx_cache["fetched_at"]) < FX_TTL:
        return _fx_cache["rate"]
    try:
        resp = requests.get(
            "https://api.exchangerate-api.com/v4/latest/USD",
            timeout=5,
        )
        data = resp.json()
        rate = data["rates"].get("THB")
        if rate:
            _fx_cache["rate"]       = rate
            _fx_cache["fetched_at"] = now
            log.info("FX updated: 1 USD = %.2f THB", rate)
        return rate
    except Exception as exc:
        log.warning("FX fetch failed: %s", exc)
        return _fx_cache.get("rate")  # stale but better than nothing


# ── DB helpers ────────────────────────────────────────────────────────────────
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_destinations() -> list[str]:
    with db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT destination FROM services ORDER BY destination"
        ).fetchall()
    return [r["destination"] for r in rows]


def get_service_types(destination: str) -> list[str]:
    with db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT service_type FROM services WHERE destination=? ORDER BY service_type",
            (destination,),
        ).fetchall()
    return [r["service_type"] for r in rows]


def get_vehicles(destination: str, service_type: str) -> list[str]:
    with db() as conn:
        rows = conn.execute(
            """SELECT DISTINCT r.vehicle
               FROM rates r
               JOIN services s ON s.id = r.service_id
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
    """
    Return list of matching services with their rates, zones, addons.
    """
    with db() as conn:
        sql = """
            SELECT DISTINCT s.id, s.service_name, s.tour_code,
                   s.duration, s.includes_vat, s.notes, s.source
            FROM services s
            JOIN rates r ON r.service_id = s.id
            WHERE s.destination = ?
              AND s.service_type = ?
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

            # Rates
            rate_rows = conn.execute(
                """SELECT rate_type, vehicle, pax_range, pax_category, price_thb
                   FROM rates WHERE service_id=? ORDER BY rate_type, vehicle, pax_category""",
                (sid,),
            ).fetchall()

            # Zone surcharges
            zone_rows = conn.execute(
                "SELECT zone_name, surcharge, per FROM zone_surcharges WHERE service_id=?",
                (sid,),
            ).fetchall()

            # Addons
            addon_rows = conn.execute(
                "SELECT addon_name, price_adult, price_child FROM addons WHERE service_id=?",
                (sid,),
            ).fetchall()

            result.append(
                {
                    "id":           sid,
                    "name":         svc["service_name"],
                    "tour_code":    svc["tour_code"],
                    "duration":     svc["duration"],
                    "includes_vat": bool(svc["includes_vat"]),
                    "notes":        svc["notes"],
                    "source":       svc["source"],
                    "rates":        [dict(r) for r in rate_rows],
                    "zones":        [dict(z) for z in zone_rows],
                    "addons":       [dict(a) for a in addon_rows],
                }
            )
    return result


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def quotation_page(request: Request):
    destinations = get_destinations()
    return templates.TemplateResponse(
        "quotation.html",
        {"request": request, "destinations": destinations},
    )


@router.get("/api/service-types")
async def api_service_types(destination: str = Query(...)):
    return get_service_types(destination)


@router.get("/api/vehicles")
async def api_vehicles(
    destination: str = Query(...),
    service_type: str = Query(...),
):
    return get_vehicles(destination, service_type)


@router.get("/api/search")
async def api_search(
    destination: str  = Query(...),
    service_type: str = Query(...),
    vehicle: str      = Query(default=""),
    rate_type: str    = Query(default=""),
    search: str       = Query(default=""),
):
    services = search_services(
        destination=destination,
        service_type=service_type,
        vehicle=vehicle or None,
        rate_type=rate_type or None,
        search=search,
    )
    return services


@router.get("/api/fx")
async def api_fx():
    rate = get_usd_rate()
    return {"thb_per_usd": rate, "source": "exchangerate-api.com"}
