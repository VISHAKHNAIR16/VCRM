"""
features/quotation/router.py
─────────────────────────────
Quotation lookup tool — search-first interface with City / Supplier / Service-Type
filters.

What changed in this version
────────────────────────────
The text search is now *tolerant* instead of literal. The old behaviour matched
the whole query as a single substring (`LIKE '%airport to hotel%'`), so a service
stored as "Airport - Hotel" never matched the phrase "airport to hotel".

`search_services()` now:
  1. Tokenizes the query, splitting on whitespace AND punctuation
     (`-  /  ,  ;  |  >  –  —  &`) so "airport - hotel", "airport/hotel" and
     "airport to hotel" all reduce to the same tokens: ["airport", "hotel"].
  2. Strips connective stopwords ("to", "from", "and", "the", "of", "via" …)
     that would otherwise match almost every row and drown out real signal.
  3. Matches a row if ANY token appears in ANY searchable field (loose OR), then
     RANKS rows by how many tokens they match, so the closest hit lands first.

The City / Supplier / Service-Type dropdowns are applied as hard AND filters on
top of the text match.
"""

import logging
import os
import re
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

# NOTE ON THE IMPORT BELOW:
# catalog_search imports THIS module (`from . import router`) to reuse
# search_services()/_tokenize_query()/get_filter_options() rather than
# duplicating that logic. To avoid a circular import at module-load time,
# catalog_search is imported lazily, inside the route handlers that need it
# (see api_catalog_search / unified_quotation_page below), not at the top
# of this file.
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


def get_filter_options() -> dict:
    """
    Get unique filter options for the City / Supplier / Service-Type dropdowns.
    Returns dict with cities, suppliers, and service_types.
    """
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


# ── Flexible search ────────────────────────────────────────────────────────────

# Connective words stripped from queries. They appear in almost every row, so
# matching them adds noise and ruins ranking. Add domain words here as needed.
_SEARCH_STOPWORDS = {
    "to", "from", "and", "or", "the", "a", "an", "of", "for",
    "via", "by", "with", "in", "on", "at", "&", "-",
}

# Characters treated as word separators, so "airport-hotel", "airport / hotel",
# "airport > hotel" and "airport to hotel" all tokenize the same way.
_SEARCH_SPLIT = re.compile(r"[\s\-/,;:|>·–—&]+")

# Columns folded into one searchable blob. COALESCE stops a NULL column from
# wiping out the whole concatenation. Order is irrelevant to matching.
_SEARCHABLE = (
    "LOWER("
    "COALESCE(s.service_name,'') || ' ' || "
    "COALESCE(s.destination,'')  || ' ' || "
    "COALESCE(s.service_type,'') || ' ' || "
    "COALESCE(s.supplier,'')     || ' ' || "
    "COALESCE(s.tour_code,'')"
    ")"
)


def _tokenize_query(query: str) -> list[str]:
    """
    Break a raw search string into clean, de-duplicated, lowercase tokens.

    Examples
    --------
    "airport to hotel"   -> ["airport", "hotel"]   ("to" is a stopword)
    "Airport - Hotel"    -> ["airport", "hotel"]   (hyphen is a separator)
    "phuket/krabi"       -> ["phuket", "krabi"]
    "to from the"        -> []                      (all stopwords -> no text query)

    A token is kept only if it is longer than 1 character and is not a stopword.
    Returns [] when nothing meaningful survives; callers treat that as
    "no text query" and fall back to filter-only / browse-all behaviour.
    """
    if not query or not query.strip():
        return []
    raw = _SEARCH_SPLIT.split(query.strip().lower())
    seen, tokens = set(), []
    for t in raw:
        if len(t) > 1 and t not in _SEARCH_STOPWORDS and t not in seen:
            seen.add(t)
            tokens.append(t)
    return tokens


def search_services(
    query: str = "",
    city: str = None,
    supplier: str = None,
    service_type: str = None,
    limit: int = 100,
) -> list[dict]:
    """
    Tolerant fuzzy search across service name, destination, type, supplier and
    tour code, narrowed by optional City / Supplier / Service-Type filters.

    Matching logic
    --------------
    - Text: a row qualifies if ANY query token appears anywhere in its searchable
      blob (loose OR). Rows are then ordered by how many distinct tokens they
      match, descending, so the strongest hit is first.
    - Filters: City / Supplier / Service-Type are exact AND constraints layered
      on top of the text match.
    - Empty query + no filters -> browse the first `limit` services by name.
    """
    tokens = _tokenize_query(query)
    log.info(
        f"Searching: tokens={tokens}, city='{city}', "
        f"supplier='{supplier}', type='{service_type}'"
    )

    try:
        with _db() as conn:
            sql = """
                SELECT s.id, s.service_name, s.destination, s.service_type,
                       s.tour_code, s.duration, s.includes_vat, s.notes, s.source,
                       s.company_code, s.supplier
                FROM services s
                WHERE 1=1
            """
            params: list = []

            # ── Text match: OR across every token ──
            if tokens:
                ors = " OR ".join(f"{_SEARCHABLE} LIKE ?" for _ in tokens)
                sql += f" AND ({ors})"
                params.extend(f"%{t}%" for t in tokens)

            # ── Hard filters (AND) ──
            if city and city.strip():
                sql += " AND s.destination = ?"
                params.append(city)
            if supplier and supplier.strip():
                sql += " AND s.supplier = ?"
                params.append(supplier)
            if service_type and service_type.strip():
                sql += " AND s.service_type = ?"
                params.append(service_type)

            # ── Relevance: more matched tokens rank higher ──
            if tokens:
                score = " + ".join(
                    f"(CASE WHEN {_SEARCHABLE} LIKE ? THEN 1 ELSE 0 END)"
                    for _ in tokens
                )
                sql += f" ORDER BY ({score}) DESC, s.service_name ASC LIMIT ?"
                params.extend(f"%{t}%" for t in tokens)  # second pass for scoring
            else:
                sql += " ORDER BY s.service_name ASC LIMIT ?"

            params.append(limit)

            log.debug(f"SQL: {sql}")
            log.debug(f"Params: {params}")

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
                    "supplier":     svc["supplier"],
                    "rates":        [dict(r) for r in rate_rows],
                    "zones":        [dict(z) for z in zone_rows],
                    "addons":       [dict(a) for a in addon_rows],
                })

            log.info(f"Search returned {len(result)} results")
            return result

    except Exception as e:
        log.error(f"Search error: {e}")
        return []


def get_service_by_id(service_id: int) -> dict | None:
    """Get a single service with all its rate details."""
    try:
        with _db() as conn:
            svc = conn.execute(
                """SELECT id, service_name, destination, service_type, tour_code,
                          duration, includes_vat, notes, source, company_code, supplier
                   FROM services WHERE id = ?""",
                (service_id,),
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
                "supplier":     svc["supplier"],
                "rates":        [dict(r) for r in rate_rows],
            }
    except Exception as e:
        log.error(f"Error getting service {service_id}: {e}")
        return None


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
@_page_guard
async def quotation_page(request: Request):
    """Main quotation page with search-first interface + filter dropdowns."""
    log.info("Loading quotation page...")

    try:
        if not DB_PATH.exists():
            log.error(f"Database not found at {DB_PATH}")
            filter_options = {"cities": [], "suppliers": [], "service_types": []}
        else:
            filter_options = get_filter_options()
    except Exception as e:
        log.error(f"Failed to get filter options: {e}")
        filter_options = {"cities": [], "suppliers": [], "service_types": []}

    return templates.TemplateResponse(
        "quotation.html",
        {
            "request": request,
            "cities": filter_options.get("cities", []),
            "suppliers": filter_options.get("suppliers", []),
            "service_types": filter_options.get("service_types", []),
        },
    )


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
    """Search services with tolerant token matching plus dropdown filters."""
    try:
        results = search_services(q, city, supplier, service_type, limit)
        return results
    except FileNotFoundError as e:
        log.error(f"Database not found: {e}")
        return JSONResponse({"error": "Database unavailable"}, status_code=503)
    except Exception as e:
        log.error(f"Search error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


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


@router.get("/api/filter-options")
@_api_guard
async def api_filter_options(request: Request):
    """Get all filter options for the dropdowns."""
    try:
        return get_filter_options()
    except FileNotFoundError:
        return JSONResponse({"error": "Database unavailable"}, status_code=503)


# ── Unified catalog search (Step 1 of the quotation/quotation-attractions merge) ──
#
# This is the endpoint the single search bar calls. It returns BOTH transfers
# and attraction tickets in one ranked list — see catalog_search.py for the
# full rationale and result shape. It is intentionally additive: the existing
# /api/search (transfers-only) and features/quotation_attractions' own search
# endpoint are untouched, so nothing that currently depends on them breaks
# while the frontend migration (Steps 2-3) is rolled out behind the scenes.

@router.get("/api/catalog-search")
@_api_guard
async def api_catalog_search(
    request: Request,
    q: str = Query(default="", description="Free-text search across transfers and attraction tickets"),
    city: str = Query(default="", description="Optional exact-match city/destination filter"),
    supplier: str = Query(default="", description="Optional exact-match supplier filter"),
    type: str = Query(default="all", description="'all' | 'transfer' | 'attraction'"),
    limit: int = Query(default=100, ge=1, le=200),
):
    """
    Search transfers AND attraction tickets in a single call and return one
    ranked, normalized result list — the backing endpoint for the unified
    search bar (client requirement: "search once, add either as one entity").
    """
    # Imported lazily to avoid a circular import (catalog_search imports this
    # module for search_services / _tokenize_query / get_filter_options).
    from . import catalog_search

    try:
        results = catalog_search.search_catalog(
            query=q,
            city=city or None,
            supplier=supplier or None,
            result_type=type,
            limit=limit,
        )
        return results
    except FileNotFoundError as e:
        log.error(f"Database not found: {e}")
        return JSONResponse({"error": "Database unavailable"}, status_code=503)
    except Exception as e:
        log.error(f"Unified catalog search error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/catalog-filter-options")
@_api_guard
async def api_catalog_filter_options(request: Request):
    """
    Combined City/Supplier dropdown options across both transfers and
    attraction tickets, for the unified search page's filter bar.
    """
    from . import catalog_search

    try:
        return catalog_search.get_unified_filter_options()
    except FileNotFoundError:
        return JSONResponse({"error": "Database unavailable"}, status_code=503)


# ── Attraction pricing endpoints (Step 2 of the merge) ─────────────────────────
#
# Before this, attraction ticket pricing (features/quotation_attractions/pricing.py)
# was only reachable from the separate /quotation-attractions page. The unified
# search bar (Step 1) can now find attraction results, but the frontend cart
# still needs a way to (a) price a ticket for a given pax mix, and (b) fetch the
# "add a transfer to this ticket" combo options for the advanced bundle flow
# (client requirement #2). These two endpoints expose exactly that, reusing the
# existing, already-correct pricing.py logic untouched — no pricing rules are
# duplicated or reimplemented here.

@router.get("/api/attraction/{attraction_id}")
@_api_guard
async def api_get_attraction(
    request: Request,
    attraction_id: int,
    adults: int = Query(default=1, ge=0),
    children: int = Query(default=0, ge=0),
    seniors: int = Query(default=0, ge=0),
):
    """
    Full detail for a single attraction ticket, priced for the given pax mix.
    Used when a user opens an attraction result card and picks pax counts,
    mirroring how a transfer's rate options are fetched today.
    """
    from features.quotation_attractions import db as attractions_db, pricing as attractions_pricing

    attraction = attractions_db.get_attraction_by_id(attraction_id)
    if not attraction:
        return JSONResponse({"error": "Attraction not found"}, status_code=404)

    priced = attractions_pricing.calculate_ticket_only(
        attraction, adult_count=adults, child_count=children, senior_count=seniors
    )
    return {"attraction": attraction, "pricing": priced}


@router.get("/api/attraction/{attraction_id}/transfer-options")
@_api_guard
async def api_attraction_transfer_options(
    request: Request,
    attraction_id: int,
    adults: int = Query(default=1, ge=0),
    children: int = Query(default=0, ge=0),
    seniors: int = Query(default=0, ge=0),
):
    """
    "Add a transfer to this ticket" combo options for the given attraction and
    pax mix — cheapest first. This backs the advanced bundle flow: a contextual
    action on an attraction cart line item, rather than a separate page/mode.

    Returns ticket-only total alongside each priced transfer option so the
    frontend can show "Ticket alone: ฿X" vs "Ticket + Transfer: ฿Y" side by
    side without a second round trip.
    """
    from features.quotation_attractions import db as attractions_db, pricing as attractions_pricing

    attraction = attractions_db.get_attraction_by_id(attraction_id)
    if not attraction:
        return JSONResponse({"error": "Attraction not found"}, status_code=404)

    result = attractions_pricing.get_all_transfer_options_with_pricing(
        attraction, adult_count=adults, child_count=children, senior_count=seniors
    )
    return result