"""
features/quotation/catalog_search.py
──────────────────────────────────────
Unified catalog search — the single source of truth for "search everything"
across both product types the business sells:

    • Transfers / Tours / Disposal / Airport / Enroute  (table: services)
    • Attraction tickets                                 (table: attraction_products)

WHY THIS FILE EXISTS
─────────────────────
Historically these lived behind two separate pages (/quotation and
/quotation-attractions) with two separate search bars, because they were
built as two separate features at two different times. That forced every
user to first decide "am I booking a transfer or an attraction?" before
they'd even typed anything — a decision most users (agents booking on
behalf of clients, in particular) don't reliably know the answer to up
front. A single trip often needs both anyway.

This module does NOT touch the schema and does NOT merge the two tables.
`services` and `attraction_products` remain deliberately separate (see
schema.sql's comment on attraction_products — there is intentionally no
FK between them, transfers are matched to attractions by city at query
time). This module only merges the *query layer*: it calls both existing,
battle-tested search functions and blends their output into one ranked
list the frontend can render from a single search bar.

Both tables already live in the same physical quotation.db (verified: both
features/quotation/router.py and features/quotation_attractions/db.py point
at features/quotation/quotation.db), so there is no cross-database join
concern — this is a pure in-process merge of two Python lists.

RESULT SHAPE
────────────
Every item in the returned list — regardless of whether it came from
`services` or `attraction_products` — has this common "envelope" shape so
the frontend can render a single card component for both:

    {
        "type":        "transfer" | "attraction",   # discriminator
        "id":           int,                         # source-table PK
        "name":         str,                         # display title
        "city":         str | None,                  # destination / city
        "supplier":     str | None,
        "subtitle":     str,                          # short descriptor line
        "price_from":   int | None,                   # cheapest price, THB
        "match_score":  int,                           # relevance, for sorting
        "raw":          dict,                          # original full record
    }

`raw` carries the untouched original dict from search_services() /
search_attractions() so existing detail-rendering code (rates, addons,
zones, package pricing) keeps working unmodified — we are only adding a
new merged view on top, not replacing the existing per-type payloads.
"""

import logging
from typing import Any, Dict, List, Optional

log = logging.getLogger("vikram.quotation.catalog_search")

# ── Import both existing, already-working search implementations ──────────────
# We deliberately reuse these rather than re-implementing search logic, so
# behaviour (tokenizing, stopwords, ranking, filters) stays identical to the
# existing single-purpose pages. This module is purely a merge/normalize layer.
from . import router as _quotation_router  # noqa: E402  (search_services, _tokenize_query)

try:
    from features.quotation_attractions import db as _attractions_db  # noqa: E402
except ImportError:
    # Defensive: allows this module to still be imported (e.g. for unit tests)
    # even if the attractions feature folder is temporarily absent/renamed.
    _attractions_db = None
    log.warning(
        "features.quotation_attractions.db could not be imported — "
        "attraction results will be omitted from unified search."
    )


# ── Result-type filter values accepted by the API layer ───────────────────────
VALID_TYPES = {"all", "transfer", "attraction"}


def _transfer_price_from(svc: Dict[str, Any]) -> Optional[int]:
    """
    Cheapest rate on a service, for the 'from ฿X' badge on result cards.
    Returns None if the service has no priced rates at all (shouldn't happen
    in a healthy DB — build_db.py's integrity check enforces no-orphan-services
    — but we stay defensive since this is a display path, not a pricing path).
    """
    prices = [r["price_thb"] for r in svc.get("rates", []) if r.get("price_thb")]
    return min(prices) if prices else None


def _attraction_price_from(attr: Dict[str, Any]) -> Optional[int]:
    """Cheapest of adult/child/senior net price, for the result card badge."""
    prices = [
        attr.get("adult_net_price"),
        attr.get("child_net_price"),
        attr.get("senior_price"),
    ]
    prices = [p for p in prices if p]
    return min(prices) if prices else None


def _normalize_transfer(svc: Dict[str, Any], match_score: int) -> Dict[str, Any]:
    """Wrap a raw `services` row (as returned by search_services) in the common envelope."""
    subtitle_bits = [b for b in (svc.get("service_type"), svc.get("duration")) if b]
    return {
        "type": "transfer",
        "id": svc["id"],
        "name": svc.get("name"),
        "city": svc.get("destination"),
        "supplier": svc.get("supplier"),
        "subtitle": " · ".join(subtitle_bits) if subtitle_bits else "Transfer",
        "price_from": _transfer_price_from(svc),
        "match_score": match_score,
        "raw": svc,
    }


def _normalize_attraction(attr: Dict[str, Any], match_score: int) -> Dict[str, Any]:
    """Wrap a raw `attraction_products` row (as returned by search_attractions) in the common envelope."""
    subtitle_bits = [b for b in (attr.get("package_label"), attr.get("city")) if b]
    return {
        "type": "attraction",
        "id": attr["id"],
        "name": attr.get("attraction_name"),
        "city": attr.get("city"),
        "supplier": attr.get("supplier"),
        "subtitle": " · ".join(subtitle_bits) if subtitle_bits else "Attraction Ticket",
        "price_from": _attraction_price_from(attr),
        "match_score": match_score,
        "raw": attr,
    }


def _score_transfer(svc: Dict[str, Any], tokens: List[str]) -> int:
    """
    Recompute a comparable match score for a transfer row so it can be
    ranked against attraction rows on equal footing (search_services()
    already sorts internally, but doesn't expose a numeric score we can
    merge against a second list — so we derive one the same way it does:
    count how many query tokens appear in the same searchable fields).
    """
    if not tokens:
        return 0
    blob = " ".join(
        str(svc.get(f, "") or "")
        for f in ("name", "destination", "service_type", "supplier", "tour_code")
    ).lower()
    return sum(1 for t in tokens if t in blob)


def _score_attraction(attr: Dict[str, Any], tokens: List[str]) -> int:
    """Same idea as _score_transfer, but over attraction_products fields."""
    if not tokens:
        return 0
    blob = " ".join(
        str(attr.get(f, "") or "")
        for f in ("attraction_name", "package_group", "package_label", "supplier", "city")
    ).lower()
    return sum(1 for t in tokens if t in blob)


def search_catalog(
    query: str = "",
    city: Optional[str] = None,
    supplier: Optional[str] = None,
    result_type: str = "all",
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """
    Search transfers AND attraction tickets in one call, and return a single
    ranked, normalized list. This is the function the unified search bar's
    API endpoint calls.

    Args:
        query:        Free-text search string (same tolerant tokenized
                       matching as the existing per-type searches).
        city:         Optional exact-match city/destination filter, applied
                       to both product types.
        supplier:     Optional exact-match supplier filter, applied to both
                       product types.
        result_type:  "all" (default) | "transfer" | "attraction" — lets the
                       frontend offer quick filter chips without needing a
                       second endpoint.
        limit:        Max combined results returned (applied AFTER merging
                       and ranking, so both types get a fair chance to
                       appear even when one type dominates raw match counts).

    Returns:
        List of normalized result dicts (see module docstring for shape),
        sorted by match_score descending, then name ascending. Each item's
        `raw` field contains the original full record for detail rendering
        / add-to-cart, exactly as the existing single-type pages already use.
    """
    if result_type not in VALID_TYPES:
        log.warning("Unknown result_type '%s' — defaulting to 'all'", result_type)
        result_type = "all"

    tokens = _quotation_router._tokenize_query(query)
    log.info(
        "Unified catalog search: tokens=%s city=%r supplier=%r type=%r",
        tokens, city, supplier, result_type,
    )

    merged: List[Dict[str, Any]] = []

    # ── Transfers ───────────────────────────────────────────────────────────
    if result_type in ("all", "transfer"):
        try:
            # Fetch generously (2x limit) pre-merge so ranking across both
            # types has enough material to work with before we truncate.
            transfer_rows = _quotation_router.search_services(
                query=query, city=city, supplier=supplier, service_type=None,
                limit=limit * 2,
            )
            for svc in transfer_rows:
                merged.append(_normalize_transfer(svc, _score_transfer(svc, tokens)))
        except Exception as exc:
            # A failure in one half of the catalog must not take down the
            # other half's results — degrade gracefully, log loudly.
            log.error("Transfer search failed inside unified catalog search: %s", exc)

    # ── Attraction tickets ──────────────────────────────────────────────────
    if result_type in ("all", "attraction") and _attractions_db is not None:
        try:
            attraction_rows = _attractions_db.search_attractions(
                query=query, city=city, supplier=supplier, package_group=None,
                limit=limit * 2,
            )
            for attr in attraction_rows:
                merged.append(_normalize_attraction(attr, _score_attraction(attr, tokens)))
        except Exception as exc:
            log.error("Attraction search failed inside unified catalog search: %s", exc)

    # ── Rank: strongest text match first, then alphabetical for stability ─────
    merged.sort(key=lambda r: (-r["match_score"], (r["name"] or "").lower()))

    result = merged[:limit]
    log.info(
        "Unified catalog search returned %d results (%d transfer, %d attraction)",
        len(result),
        sum(1 for r in result if r["type"] == "transfer"),
        sum(1 for r in result if r["type"] == "attraction"),
    )
    return result


def get_unified_filter_options() -> Dict[str, List[str]]:
    """
    Combined City / Supplier dropdown options across BOTH product types, for
    the unified search page's filter bar. Cities/suppliers that only exist
    in one table (e.g. a city with attractions but no transfer routes yet)
    still show up, so filters never silently hide a product type.
    """
    cities: set = set()
    suppliers: set = set()

    try:
        transfer_opts = _quotation_router.get_filter_options()
        cities.update(c for c in transfer_opts.get("cities", []) if c)
        suppliers.update(s for s in transfer_opts.get("suppliers", []) if s)
    except Exception as exc:
        log.error("Failed to load transfer filter options: %s", exc)

    if _attractions_db is not None:
        try:
            attr_opts = _attractions_db.get_attraction_filter_options()
            cities.update(c for c in attr_opts.get("cities", []) if c)
            suppliers.update(s for s in attr_opts.get("suppliers", []) if s)
        except Exception as exc:
            log.error("Failed to load attraction filter options: %s", exc)

    return {
        "cities": sorted(cities),
        "suppliers": sorted(suppliers),
    }