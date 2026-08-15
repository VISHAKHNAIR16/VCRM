"""
features/quotation/router.py
─────────────────────────────
Quotation tool — primary transfer search with optional attraction add-ons.
Transfers are the main product; attractions can be added as supplementary items.
"""

import logging
import re
import sqlite3
from functools import wraps
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

log = logging.getLogger("vikram.quotation")
router = APIRouter()
BASE = Path(__file__).parent.parent
DB_PATH = BASE/ "quotation" / "quotation.db"
templates = Jinja2Templates(directory="templates")


# ── Auth ──────────────────────────────────────────────────────────────────────

def _is_authenticated(request: Request) -> bool:
    from main import is_authenticated as _check_auth
    return _check_auth(request)


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


# ── Database helpers ──────────────────────────────────────────────────────────

def _db():
    """Get database connection with proper error handling."""
    if not DB_PATH.exists():
        log.error(f"Database not found at {DB_PATH}")
        raise FileNotFoundError(f"quotation.db not found at {DB_PATH}")

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn
    except sqlite3.Error as e:
        log.error(f"Database connection error: {e}")
        raise


# ── Fuzzy Search Helpers ──────────────────────────────────────────────────────

_SEARCH_STOPWORDS = {
    "to", "from", "and", "or", "the", "a", "an", "of", "for",
    "via", "by", "with", "in", "on", "at", "&", "-", "club", "beach",
    "island", "park", "water", "world", "city", "village", "hotel",
    "airport", "resort", "villa", "spa", "tour", "trip",
}

_SEARCH_SPLIT = re.compile(r"[\s\-/,;:|>·–—&]+")


def _tokenize_query(query: str) -> list[str]:
    """Break a raw search string into clean, de-duplicated, lowercase tokens."""
    if not query or not query.strip():
        return []
    raw = _SEARCH_SPLIT.split(query.strip().lower())
    seen, tokens = set(), []
    for t in raw:
        if len(t) > 1 and t not in _SEARCH_STOPWORDS and t not in seen:
            seen.add(t)
            tokens.append(t)
    return tokens


# ── TRANSFER SEARCH ──────────────────────────────────────────────────────────

def search_transfers(
    query: str = "",
    city: str = None,
    supplier: str = None,
    service_type: str = None,
    limit: int = 100,
) -> list[dict]:
    """
    Search transfers with fuzzy token matching.
    Primary search for the quotation tool.
    """
    tokens = _tokenize_query(query)
    log.info(f"Transfer search: tokens={tokens}, city='{city}', supplier='{supplier}', type='{service_type}'")

    try:
        with _db() as conn:
            searchable = (
                "LOWER("
                "COALESCE(s.service_name,'') || ' ' || "
                "COALESCE(s.destination,'')  || ' ' || "
                "COALESCE(s.service_type,'') || ' ' || "
                "COALESCE(s.supplier,'')     || ' ' || "
                "COALESCE(s.tour_code,'')"
                ")"
            )

            sql = """
                SELECT s.id, s.service_name, s.destination, s.service_type,
                       s.tour_code, s.duration, s.includes_vat, s.notes, s.source,
                       s.company_code, s.supplier
                FROM services s
                WHERE 1=1
            """
            params: list = []

            # ── Text match ──
            if tokens:
                ors = " OR ".join(f"{searchable} LIKE ?" for _ in tokens)
                sql += f" AND ({ors})"
                params.extend(f"%{t}%" for t in tokens)

            # ── Hard filters ──
            if city and city.strip():
                sql += " AND s.destination = ?"
                params.append(city)
            if supplier and supplier.strip():
                sql += " AND s.supplier = ?"
                params.append(supplier)
            if service_type and service_type.strip():
                sql += " AND s.service_type = ?"
                params.append(service_type)

            # ── Relevance ranking ──
            if tokens:
                score = " + ".join(
                    f"(CASE WHEN {searchable} LIKE ? THEN 1 ELSE 0 END)"
                    for _ in tokens
                )
                sql += f" ORDER BY ({score}) DESC, s.service_name ASC LIMIT ?"
                params.extend(f"%{t}%" for t in tokens)
            else:
                sql += " ORDER BY s.service_name ASC LIMIT ?"

            params.append(limit)

            rows = conn.execute(sql, params).fetchall()
            result = []

            for svc in rows:
                sid = svc["id"]
                rate_rows = conn.execute(
                    """SELECT rate_type, vehicle, pax_range, pax_category, price_thb
                       FROM rates WHERE service_id=?
                       ORDER BY rate_type, vehicle, pax_category""",
                    (sid,),
                ).fetchall()

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
                    "supplier":     svc["supplier"],
                    "rates":        [dict(r) for r in rate_rows],
                    "addons":       [dict(a) for a in addon_rows],
                })

            log.info(f"Transfer search returned {len(result)} results")
            return result

    except Exception as e:
        log.error(f"Transfer search error: {e}")
        return []


# ── ATTRACTION SEARCH (Optional add-ons) ────────────────────────────────────

def search_attractions(
    query: str = "",
    city: str = None,
    supplier: str = None,
    limit: int = 100,
) -> list[dict]:
    """
    Search attractions as optional add-ons.
    Uses the same database (attraction_products table).
    """
    tokens = _tokenize_query(query)
    log.info(f"Attraction search (add-ons): tokens={tokens}, city='{city}', supplier='{supplier}'")

    try:
        with _db() as conn:
            searchable = (
                "LOWER("
                "COALESCE(attraction_name,'') || ' ' || "
                "COALESCE(package_group,'') || ' ' || "
                "COALESCE(package_label,'') || ' ' || "
                "COALESCE(supplier,'') || ' ' || "
                "COALESCE(city,'')"
                ")"
            )

            sql = """
                SELECT 
                    id, city, attraction_name, package_group, package_label,
                    adult_net_price, child_net_price, senior_price,
                    supplier, remarks, source_row, created_at
                FROM attraction_products
                WHERE 1=1
            """
            params: list = []

            if tokens:
                ors = " OR ".join(f"{searchable} LIKE ?" for _ in tokens)
                sql += f" AND ({ors})"
                params.extend(f"%{t}%" for t in tokens)

            if city and city.strip():
                sql += " AND city = ?"
                params.append(city)
            if supplier and supplier.strip():
                sql += " AND supplier = ?"
                params.append(supplier)

            if tokens:
                score = " + ".join(
                    f"(CASE WHEN {searchable} LIKE ? THEN 1 ELSE 0 END)"
                    for _ in tokens
                )
                sql += f" ORDER BY ({score}) DESC, city, package_group, attraction_name LIMIT ?"
                params.extend(f"%{t}%" for t in tokens)
            else:
                sql += " ORDER BY city, package_group, attraction_name LIMIT ?"

            params.append(limit)

            rows = conn.execute(sql, params).fetchall()
            result = [dict(row) for row in rows]

            log.info(f"Attraction search returned {len(result)} results")
            return result

    except Exception as e:
        log.error(f"Attraction search error: {e}")
        return []


# ── Filter options ────────────────────────────────────────────────────────────

def get_filter_options() -> dict:
    """Get unique filter options for dropdowns."""
    log.info("Getting filter options from database...")

    try:
        with _db() as conn:
            total_services = conn.execute("SELECT COUNT(*) FROM services").fetchone()[0]
            log.info(f"Total services in database: {total_services}")

            if total_services == 0:
                log.warning("No services found in database!")
                return {"cities": [], "suppliers": [], "service_types": []}

            cities = [r[0] for r in conn.execute(
                "SELECT DISTINCT destination FROM services "
                "WHERE destination IS NOT NULL AND destination != '' ORDER BY destination"
            ).fetchall()]

            suppliers = [r[0] for r in conn.execute(
                "SELECT DISTINCT supplier FROM services "
                "WHERE supplier IS NOT NULL AND supplier != '' ORDER BY supplier"
            ).fetchall()]

            service_types = [r[0] for r in conn.execute(
                "SELECT DISTINCT service_type FROM services "
                "WHERE service_type IS NOT NULL AND service_type != '' ORDER BY service_type"
            ).fetchall()]

            log.info(
                f"Filter options loaded: cities={len(cities)}, "
                f"suppliers={len(suppliers)}, types={len(service_types)}"
            )
            return {
                "cities": cities,
                "suppliers": suppliers,
                "service_types": service_types,
            }
    except Exception as e:
        log.error(f"Error getting filter options: {e}")
        return {"cities": [], "suppliers": [], "service_types": []}


def get_attraction_filter_options() -> dict:
    """Get filter options for attraction add-ons."""
    log.info("Getting attraction filter options from database...")
    try:
        with _db() as conn:
            cities = [r[0] for r in conn.execute(
                "SELECT DISTINCT city FROM attraction_products "
                "WHERE city IS NOT NULL AND city != '' ORDER BY city"
            ).fetchall()]

            suppliers = [r[0] for r in conn.execute(
                "SELECT DISTINCT supplier FROM attraction_products "
                "WHERE supplier IS NOT NULL AND supplier != '' ORDER BY supplier"
            ).fetchall()]

            log.info(f"Attraction filter options: cities={len(cities)}, suppliers={len(suppliers)}")
            return {"cities": cities, "suppliers": suppliers}
    except Exception as e:
        log.error(f"Error getting attraction filter options: {e}")
        return {"cities": [], "suppliers": []}


# ── Page Routes ──────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
@_page_guard
async def quotation_page(request: Request):
    """Main quotation page — transfers first, attractions as optional add-ons."""
    log.info("Loading quotation page...")

    try:
        filter_options = get_filter_options()
        attraction_filter_options = get_attraction_filter_options()
    except Exception as e:
        log.error(f"Failed to get filter options: {e}")
        filter_options = {"cities": [], "suppliers": [], "service_types": []}
        attraction_filter_options = {"cities": [], "suppliers": []}

    return templates.TemplateResponse(
        "quotation_attractions/index.html",  # ← Using quotation.html (not index.html)
        {
            "request": request,
            "cities": filter_options.get("cities", []),
            "suppliers": filter_options.get("suppliers", []),
            "service_types": filter_options.get("service_types", []),
            "attraction_cities": attraction_filter_options.get("cities", []),
            "attraction_suppliers": attraction_filter_options.get("suppliers", []),
        },
    )


# ── API Routes ──────────────────────────────────────────────────────────────

@router.get("/api/search")
@_api_guard
async def api_search(
    request: Request,
    q: str = Query(default=""),
    city: str = Query(default=""),
    supplier: str = Query(default=""),
    service_type: str = Query(default=""),
    limit: int = Query(default=100, ge=1, le=200),
):
    """Search transfers with fuzzy token matching."""
    try:
        results = search_transfers(q, city, supplier, service_type, limit)
        return results
    except FileNotFoundError as e:
        log.error(f"Database not found: {e}")
        return JSONResponse({"error": "Database unavailable"}, status_code=503)
    except Exception as e:
        log.error(f"Search error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/quotation-attractions/search")
@_api_guard
async def api_search_attractions(
    request: Request,
    q: str = Query(default=""),
    city: str = Query(default=""),
    supplier: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=200),
):
    """Search attractions as optional add-ons."""
    try:
        results = search_attractions(q, city, supplier, limit)
        return results
    except FileNotFoundError as e:
        log.error(f"Database not found: {e}")
        return JSONResponse({"error": "Database unavailable"}, status_code=503)
    except Exception as e:
        log.error(f"Attraction search error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/service/{service_id}")
@_api_guard
async def api_get_service(request: Request, service_id: int):
    """Get full details for a specific transfer service."""
    try:
        with _db() as conn:
            svc = conn.execute(
                """SELECT id, service_name, destination, service_type, tour_code,
                          duration, includes_vat, notes, source, company_code, supplier
                   FROM services WHERE id = ?""",
                (service_id,),
            ).fetchone()

            if not svc:
                return JSONResponse({"error": "Service not found"}, status_code=404)

            rate_rows = conn.execute(
                """SELECT rate_type, vehicle, pax_range, pax_category, price_thb
                   FROM rates WHERE service_id=?
                   ORDER BY rate_type, vehicle, pax_category""",
                (service_id,),
            ).fetchall()

            try:
                addon_rows = conn.execute(
                    "SELECT addon_name, price_adult, price_child FROM addons WHERE service_id=?",
                    (service_id,),
                ).fetchall()
            except Exception:
                addon_rows = []

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
                "supplier":     svc["supplier"],
                "rates":        [dict(r) for r in rate_rows],
                "addons":       [dict(a) for a in addon_rows],
            }
    except Exception as e:
        log.error(f"Error getting service {service_id}: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/quotation-attractions/{attraction_id}")
@_api_guard
async def api_get_attraction(request: Request, attraction_id: int):
    """Get full attraction details for add-on."""
    try:
        with _db() as conn:
            row = conn.execute(
                """SELECT id, city, attraction_name, package_group, package_label,
                          adult_net_price, child_net_price, senior_price,
                          supplier, remarks, source_row, created_at
                   FROM attraction_products WHERE id = ?""",
                (attraction_id,),
            ).fetchone()

            if not row:
                return JSONResponse({"error": "Attraction not found"}, status_code=404)

            return dict(row)
    except Exception as e:
        log.error(f"Error getting attraction {attraction_id}: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/filter-options")
@_api_guard
async def api_filter_options(request: Request):
    """Get all filter options for dropdowns."""
    try:
        return get_filter_options()
    except FileNotFoundError:
        return JSONResponse({"error": "Database unavailable"}, status_code=503)


@router.get("/api/quotation-attractions/attraction-filter-options")
@_api_guard
async def api_attraction_filter_options(request: Request):
    """Get filter options for attraction add-ons."""
    try:
        return get_attraction_filter_options()
    except FileNotFoundError:
        return JSONResponse({"error": "Database unavailable"}, status_code=503)


@router.get("/api/fx")
@_api_guard
async def api_fx(request: Request):
    """Get current USD/THB exchange rate."""
    import time
    import requests
    
    try:
        resp = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5)
        rate = resp.json()["rates"].get("THB")
        if rate:
            return {"thb_per_usd": rate, "source": "exchangerate-api.com"}
        return {"thb_per_usd": 34.00, "source": "fallback"}
    except Exception as e:
        log.warning(f"FX fetch failed: {e}")
        return {"thb_per_usd": 34.00, "source": "fallback"}