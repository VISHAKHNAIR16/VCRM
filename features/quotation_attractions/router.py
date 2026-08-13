"""
features/quotation_attractions/router.py
────────────────────────────────────────
Attraction Quotation Router with Cart Support.
"""

import logging
import re
from functools import wraps
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from . import db, pricing

log = logging.getLogger("vikram.quotation_attractions")
router = APIRouter()
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


# ── Template filters ──────────────────────────────────────────────────────────

def _to_locale(value):
    if value is None or value == "":
        return "—"
    try:
        return f"{round(float(value)):,}"
    except (TypeError, ValueError):
        return str(value)


templates.env.filters["to_locale"] = _to_locale


# ── Fuzzy Search Helpers ──────────────────────────────────────────────────────

_SEARCH_STOPWORDS = {
    "to", "from", "and", "or", "the", "a", "an", "of", "for",
    "via", "by", "with", "in", "on", "at", "&", "-", "club", "beach",
    "island", "park", "water", "world", "city", "village",
}

_SEARCH_SPLIT = re.compile(r"[\s\-/,;:|>·–—&]+")


def _tokenize_query(query: str) -> list[str]:
    if not query or not query.strip():
        return []
    raw = _SEARCH_SPLIT.split(query.strip().lower())
    seen, tokens = set(), []
    for t in raw:
        if len(t) > 1 and t not in _SEARCH_STOPWORDS and t not in seen:
            seen.add(t)
            tokens.append(t)
    return tokens


# ── Attraction Search with Fuzzy Logic ──────────────────────────────────────

def search_attractions_fuzzy(
    query: str = "",
    city: str = None,
    supplier: str = None,
    limit: int = 200,
) -> list[dict]:
    """
    Fuzzy search attractions using token-based matching.
    """
    tokens = _tokenize_query(query)
    log.info(f"Fuzzy search: tokens={tokens}, city='{city}', supplier='{supplier}'")

    try:
        with db._db() as conn:
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

            for r in result:
                r["price_range"] = pricing.get_price_range(r)
                r["from_price"] = r["price_range"].get("min")

            log.info(f"Fuzzy search returned {len(result)} results")
            return result

    except Exception as e:
        log.error(f"Fuzzy search error: {e}")
        return []


# ── Page Routes ──────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
@_page_guard
async def attractions_page(request: Request):
    """Main attractions quotation page with search and cart."""
    log.info("Loading quotation attractions page...")

    try:
        filter_options = db.get_attraction_filter_options()
        city_stats = db.get_city_stats()
        total_count = db.get_attraction_count()
    except Exception as e:
        log.error(f"Failed to get filter options: {e}")
        filter_options = {"cities": [], "suppliers": [], "package_groups": []}
        city_stats = {}
        total_count = 0

    return templates.TemplateResponse(
        "quotation_attractions/index.html",
        {
            "request": request,
            "cities": filter_options.get("cities", []),
            "suppliers": filter_options.get("suppliers", []),
            "city_stats": city_stats,
            "total_count": total_count,
        },
    )


# ── API Routes ──────────────────────────────────────────────────────────────

@router.get("/api/search")
@_api_guard
async def api_search_attractions(
    request: Request,
    q: str = Query(default=""),
    city: str = Query(default=""),
    supplier: str = Query(default=""),
    limit: int = Query(default=200, ge=1, le=500),
):
    """Search attractions with fuzzy token matching."""
    try:
        results = search_attractions_fuzzy(
            query=q,
            city=city if city else None,
            supplier=supplier if supplier else None,
            limit=limit,
        )
        return results
    except FileNotFoundError as e:
        log.error(f"Database not found: {e}")
        return JSONResponse({"error": "Database unavailable"}, status_code=503)
    except Exception as e:
        log.error(f"Search error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/attraction/{attraction_id}")
@_api_guard
async def api_get_attraction(request: Request, attraction_id: int):
    """Get full attraction details."""
    try:
        result = db.get_attraction_by_id(attraction_id)
        if not result:
            return JSONResponse({"error": "Attraction not found"}, status_code=404)
        return result
    except FileNotFoundError:
        return JSONResponse({"error": "Database unavailable"}, status_code=503)


@router.get("/api/transfers")
@_api_guard
async def api_get_transfers(
    request: Request,
    city: str = Query(default="", description="City name or 'all' for all transfers")
):
    """
    Get transfer options. 
    If city is 'all' or empty, return all transfers.
    Otherwise return transfers for the specific city.
    """
    try:
        if not city or city.strip() == "" or city.lower() == "all":
            results = db.get_all_transfer_options()
            log.info(f"Returning all {len(results)} transfers")
        else:
            results = db.get_transfer_options_by_city(city)
            log.info(f"Returning {len(results)} transfers for {city}")
        return results
    except FileNotFoundError:
        return JSONResponse({"error": "Database unavailable"}, status_code=503)
    except Exception as e:
        log.error(f"Error getting transfers: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/filter-options")
@_api_guard
async def api_filter_options(request: Request):
    """Get all filter options for dropdowns."""
    try:
        return db.get_attraction_filter_options()
    except FileNotFoundError:
        return JSONResponse({"error": "Database unavailable"}, status_code=503)


@router.get("/api/cart/quote")
@_api_guard
async def api_cart_quote(
    request: Request,
    items: str = Query(..., description="JSON array of cart items"),
    commission: float = Query(default=0, ge=0, description="Commission percentage"),
    usd_rate: float = Query(default=34.0, gt=0, description="USD to THB rate"),
):
    """Generate a quotation from cart items."""
    import json
    try:
        cart_items = json.loads(items)
        total_thb = 0
        lines = []

        for item in cart_items:
            attr = db.get_attraction_by_id(item.get("attraction_id"))
            if not attr:
                continue

            adult_price = attr.get("adult_net_price", 0)
            child_price = attr.get("child_net_price", 0) or 0
            senior_price = attr.get("senior_price", 0) or 0

            adults = item.get("adults", 1)
            children = item.get("children", 0)
            seniors = item.get("seniors", 0)

            total = (adult_price * adults) + (child_price * children) + (senior_price * seniors)

            transfer_id = item.get("transfer_id")
            if transfer_id:
                transfers = db.get_transfer_options_by_city(attr.get("city", ""))
                for t in transfers:
                    if t.get("id") == transfer_id:
                        for rate in t.get("rates", []):
                            if rate.get("pax_category") == "PerVehicle":
                                total += rate.get("price_thb", 0)
                                break
                        break

            total_thb += total
            lines.append({
                "name": attr.get("attraction_name"),
                "city": attr.get("city"),
                "adults": adults,
                "children": children,
                "seniors": seniors,
                "amount": total,
            })

        commission_amount = total_thb * (commission / 100) if commission > 0 else 0
        grand_total = total_thb + commission_amount

        return {
            "items": lines,
            "subtotal_thb": total_thb,
            "commission_percent": commission,
            "commission_thb": commission_amount,
            "grand_total_thb": grand_total,
            "grand_total_usd": grand_total / usd_rate if usd_rate > 0 else 0,
        }

    except Exception as e:
        log.error(f"Cart quote error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)