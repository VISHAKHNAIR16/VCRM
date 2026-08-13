"""
features/quotation_attractions/pricing.py
──────────────────────────────────────────
Pricing logic for attraction quotations.

Handles:
  - Ticket-only pricing (attraction admission)
  - Ticket + transfer pricing (attraction + transport)
  - Price breakdown for transparency
  - Per-person calculations (adult/child/senior)
"""

import logging
from typing import Dict, List, Optional, Any

from . import db

log = logging.getLogger("vikram.quotation_attractions.pricing")


# ── Pricing Functions ────────────────────────────────────────────────────────

def calculate_ticket_only(
    attraction: Dict[str, Any],
    adult_count: int = 1,
    child_count: int = 0,
    senior_count: int = 0
) -> Dict[str, Any]:
    """
    Calculate ticket-only price for an attraction.
    
    Args:
        attraction: Attraction product dict
        adult_count: Number of adults
        child_count: Number of children
        senior_count: Number of seniors
    
    Returns:
        Dict with breakdown and total
    """
    breakdown_items = []
    
    # ── Adult tickets ──
    if adult_count > 0:
        adult_price = attraction.get("adult_net_price", 0)
        if adult_price:
            breakdown_items.append({
                "label": f"Adult Tickets × {adult_count}",
                "price_per": adult_price,
                "quantity": adult_count,
                "subtotal": adult_price * adult_count,
                "category": "adult",
            })
    
    # ── Child tickets ──
    child_price = attraction.get("child_net_price")
    if child_count > 0 and child_price:
        breakdown_items.append({
            "label": f"Child Tickets × {child_count}",
            "price_per": child_price,
            "quantity": child_count,
            "subtotal": child_price * child_count,
            "category": "child",
        })
    
    # ── Senior tickets ──
    senior_price = attraction.get("senior_price")
    if senior_count > 0 and senior_price:
        breakdown_items.append({
            "label": f"Senior Tickets × {senior_count}",
            "price_per": senior_price,
            "quantity": senior_count,
            "subtotal": senior_price * senior_count,
            "category": "senior",
        })
    
    # ── Calculate total ──
    total = sum(item["subtotal"] for item in breakdown_items)
    
    return {
        "attraction": {
            "id": attraction.get("id"),
            "name": attraction.get("attraction_name"),
            "city": attraction.get("city"),
            "supplier": attraction.get("supplier"),
            "package_group": attraction.get("package_group"),
        },
        "breakdown": breakdown_items,
        "total": total,
        "currency": "THB",
        "pax_summary": {
            "adults": adult_count,
            "children": child_count,
            "seniors": senior_count,
            "total_pax": adult_count + child_count + senior_count,
        }
    }


def group_transfer_rates(transfer: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Group a transfer service's flat `rates` list into distinct SELLABLE options.

    Why this exists: a single "Transfer" or "Disposal" service can carry several
    unrelated rate rows — e.g. one Private/PerVehicle price per vehicle type
    (CAR, VAN, SUV), OR a pair of SIC rows (Adult price + Child price) that
    together make up ONE sellable per-person option. Naively picking "the first
    matching rate" (the old behavior) silently dropped the Child row whenever
    an Adult row existed for the same group, undercharging families.

    Grouping key: (rate_type, vehicle). vehicle is None for SIC rows, so all
    Adult/Child rows for the same rate_type collapse into one group and get
    priced together as one option with two line items.

    Returns a list of dicts:
        {
            "rate_type": "Private" | "SIC",
            "vehicle": "CAR" | None,
            "rates": [ <raw rate rows in this group> ],
        }
    """
    groups: Dict[tuple, List[Dict[str, Any]]] = {}
    order: List[tuple] = []
    for rate in transfer.get("rates", []):
        key = (rate.get("rate_type"), rate.get("vehicle"))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(rate)

    return [
        {"rate_type": key[0], "vehicle": key[1], "rates": groups[key]}
        for key in order
    ]


def calculate_ticket_with_transfer(
    attraction: Dict[str, Any],
    transfer: Dict[str, Any],
    transfer_rates: List[Dict[str, Any]],
    adult_count: int = 1,
    child_count: int = 0,
    senior_count: int = 0,
) -> Dict[str, Any]:
    """
    Calculate ticket + transfer price for an attraction.

    Args:
        attraction: Attraction product dict
        transfer: Transfer service dict from get_transfer_options_by_city()
        transfer_rates: ONE group's raw rate rows, as produced by
            group_transfer_rates() — e.g. a single PerVehicle row, or an
            Adult row + a Child row belonging to the same SIC group. Every
            row in this list is priced and shown as its own breakdown line,
            so nothing is silently dropped.
        adult_count: Number of adults
        child_count: Number of children
        senior_count: Number of seniors

    Returns:
        Dict with ticket breakdown, transfer breakdown, and total
    """

    # ── Calculate ticket part ──
    ticket_result = calculate_ticket_only(
        attraction,
        adult_count=adult_count,
        child_count=child_count,
        senior_count=senior_count
    )

    # ── Calculate transfer part — one breakdown line PER rate row in the group ──
    transfer_breakdown = []
    transfer_total = 0
    vehicle_label = None
    rate_type_label = None
    notes = []

    for rate in transfer_rates:
        price = rate.get("price_thb")
        cat = rate.get("pax_category")
        vehicle_label = rate.get("vehicle") or vehicle_label
        rate_type_label = rate.get("rate_type") or rate_type_label

        if price is None:
            continue

        if cat == "PerVehicle":
            qty = 1
            label = f"Transfer — {transfer.get('name', 'Transfer')} ({rate.get('vehicle') or 'Vehicle'})"
        elif cat == "Adult":
            qty = adult_count
            label = f"Transfer (Adult) × {adult_count}"
        elif cat == "Child":
            qty = child_count
            label = f"Transfer (Child) × {child_count}"
        else:
            qty = 1
            label = f"Transfer ({cat})"

        if qty <= 0:
            # e.g. a Child rate exists but child_count is 0 this quote — nothing to charge,
            # but we don't silently omit awareness of it either; skip the line, it's correct
            # to charge 0 for 0 people.
            continue

        subtotal = price * qty
        transfer_total += subtotal
        transfer_breakdown.append({
            "label": label,
            "price_per": price,
            "quantity": qty,
            "subtotal": subtotal,
            "vehicle": rate.get("vehicle") or "Standard",
            "pax_category": cat,
            "service_name": transfer.get("name", "Transfer"),
        })

    # ── Transparency: if children are on this quote but the selected group has
    #    no Child rate at all, say so explicitly instead of quietly charging 0. ──
    has_child_rate = any(r.get("pax_category") == "Child" for r in transfer_rates)
    if child_count > 0 and not has_child_rate and rate_type_label != "Private":
        notes.append(
            f"No child transfer rate found for this option — {child_count} "
            f"child(ren) not charged for transfer. Verify with supplier."
        )

    # ── Combine totals ──
    combined_breakdown = ticket_result["breakdown"] + transfer_breakdown
    combined_total = ticket_result["total"] + transfer_total

    return {
        "attraction": ticket_result["attraction"],
        "transfer": {
            "id": transfer.get("id"),
            "name": transfer.get("name"),
            "service_type": transfer.get("service_type"),
            "vehicle": vehicle_label,
            "rate_type": rate_type_label,
        },
        "ticket_breakdown": ticket_result["breakdown"],
        "transfer_breakdown": transfer_breakdown,
        "combined_breakdown": combined_breakdown,
        "ticket_total": ticket_result["total"],
        "transfer_total": transfer_total,
        "total": combined_total,
        "currency": "THB",
        "pax_summary": ticket_result["pax_summary"],
        "notes": notes,
    }


def get_all_transfer_options_with_pricing(
    attraction: Dict[str, Any],
    adult_count: int = 1,
    child_count: int = 0,
    senior_count: int = 0
) -> Dict[str, Any]:
    """
    Get all transfer options for an attraction's city with pricing.
    Used to populate the transfer selection dropdown.

    Each returned option corresponds to ONE (rate_type, vehicle) group — see
    group_transfer_rates(). SIC options with both Adult and Child rates are
    priced using both rows, never just one.

    Args:
        attraction: Attraction product dict
        adult_count: Number of adults
        child_count: Number of children
        senior_count: Number of seniors

    Returns:
        Dict with attraction info and list of transfer options with prices
    """
    city = attraction.get("city")
    if not city:
        return {"attraction": attraction, "transfers": [], "error": "No city specified"}

    # ── Get all transfer options for the city ──
    transfers = db.get_transfer_options_by_city(city)

    # ── Calculate pricing for each transfer option ──
    transfer_options = []

    for transfer in transfers:
        for group in group_transfer_rates(transfer):
            if not group["rates"]:
                continue

            combined = calculate_ticket_with_transfer(
                attraction=attraction,
                transfer=transfer,
                transfer_rates=group["rates"],
                adult_count=adult_count,
                child_count=child_count,
                senior_count=senior_count,
            )

            if combined["transfer_total"] <= 0 and not combined["transfer_breakdown"]:
                # Nothing usable in this group (e.g. all prices were NULL) — skip it,
                # rather than showing a phantom ฿0 option.
                continue

            transfer_options.append({
                "transfer_id": transfer.get("id"),
                "transfer_name": transfer.get("name"),
                "service_type": transfer.get("service_type"),
                "vehicle": group["vehicle"] or "Any",
                "rate_type": group["rate_type"],
                "total_with_ticket": combined["total"],
                "ticket_total": combined["ticket_total"],
                "transfer_total": combined["transfer_total"],
                "combined": combined,
            })

    # Sort by price (cheapest first)
    transfer_options.sort(key=lambda x: x.get("total_with_ticket", 999999))

    return {
        "attraction": {
            "id": attraction.get("id"),
            "name": attraction.get("attraction_name"),
            "city": city,
        },
        "transfers": transfer_options,
        "ticket_only_total": calculate_ticket_only(
            attraction,
            adult_count=adult_count,
            child_count=child_count,
            senior_count=senior_count
        )["total"],
    }


def get_price_range(attraction: Dict[str, Any]) -> Dict[str, Optional[int]]:
    """
    Get min and max prices for an attraction (adult/child/senior).
    Useful for displaying "from X" pricing on cards.
    """
    prices = []
    
    adult = attraction.get("adult_net_price")
    if adult:
        prices.append(adult)
    
    child = attraction.get("child_net_price")
    if child:
        prices.append(child)
    
    senior = attraction.get("senior_price")
    if senior:
        prices.append(senior)
    
    if not prices:
        return {"min": None, "max": None}
    
    return {
        "min": min(prices),
        "max": max(prices) if len(prices) > 1 else None,
    }


def format_price(price: int, currency: str = "THB") -> str:
    """Format price with currency for display."""
    if price is None:
        return "N/A"
    return f"{price:,} {currency}"