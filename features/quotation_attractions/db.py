"""
features/quotation_attractions/db.py
────────────────────────────────────
Database layer for attraction quotation module.

Provides:
  - Search attractions by name/city/supplier/package_group
  - Get transfer options by city from existing services
  - Get attraction details with pricing
  - Filter options for dropdowns
"""

import logging
import sqlite3
from pathlib import Path
from typing import List, Dict, Optional, Any

log = logging.getLogger("vikram.quotation_attractions")

# ── Database path ─────────────────────────────────────────────────────────────
BASE = Path(__file__).parent.parent  # features/
DB_PATH = BASE / "quotation" / "quotation.db"


def _db() -> sqlite3.Connection:
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


# ── Attraction Queries ──────────────────────────────────────────────────────

def search_attractions(
    query: str = "",
    city: str = None,
    supplier: str = None,
    package_group: str = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """
    Search attraction products with flexible filters.
    
    Args:
        query: Text search across attraction_name, package_group, supplier
        city: Filter by city (exact match)
        supplier: Filter by supplier (exact match)
        package_group: Filter by package group (exact match)
        limit: Max results to return
    
    Returns:
        List of attraction products with pricing details
    """
    log.info(
        f"Searching attractions: query='{query}', city='{city}', "
        f"supplier='{supplier}', package_group='{package_group}'"
    )

    try:
        with _db() as conn:
            sql = """
                SELECT 
                    id,
                    city,
                    attraction_name,
                    package_group,
                    package_label,
                    adult_net_price,
                    child_net_price,
                    senior_price,
                    supplier,
                    remarks,
                    source_row,
                    created_at
                FROM attraction_products
                WHERE 1=1
            """
            params: list = []

            if query and query.strip():
                search_term = f"%{query.strip()}%"
                sql += """ AND (
                    attraction_name LIKE ? OR 
                    package_group LIKE ? OR 
                    package_label LIKE ? OR 
                    supplier LIKE ?
                )"""
                params.extend([search_term] * 4)

            if city and city.strip():
                sql += " AND city = ?"
                params.append(city)

            if supplier and supplier.strip():
                sql += " AND supplier = ?"
                params.append(supplier)

            if package_group and package_group.strip():
                sql += " AND package_group = ?"
                params.append(package_group)

            sql += " ORDER BY city, package_group, attraction_name LIMIT ?"
            params.append(limit)

            rows = conn.execute(sql, params).fetchall()
            result = [dict(row) for row in rows]

            log.info(f"Search returned {len(result)} attractions")
            return result

    except Exception as e:
        log.error(f"Attraction search error: {e}")
        return []


def get_all_transfer_options() -> List[Dict[str, Any]]:
    """
    Get ALL transfer services from the database.
    Includes ALL service types: Transfer, Disposal, On Disposal (WC), On Disposal (IC),
    Airport, Enroute, Enroute/Combi, Tour
    """
    log.info("Getting ALL transfer options...")

    try:
        with _db() as conn:
            sql = """
                SELECT 
                    s.id, s.service_name, s.destination, s.service_type,
                    s.duration, s.notes, s.supplier, s.company_code, s.tour_code
                FROM services s
                WHERE s.service_type IN (
                    'Transfer', 'Disposal', 'On Disposal ( WC)', 'On Disposal ( IC)',
                    'Airport', 'Enroute', 'Enroute/Combi', 'Tour'
                )
                ORDER BY s.service_type, s.destination, s.service_name
            """
            
            rows = conn.execute(sql).fetchall()
            result = []

            for svc in rows:
                rates = conn.execute(
                    """
                    SELECT 
                        rate_type, vehicle, pax_range, pax_category, price_thb
                    FROM rates
                    WHERE service_id = ?
                    ORDER BY rate_type, vehicle, pax_category
                    """,
                    (svc["id"],),
                ).fetchall()

                result.append({
                    "id": svc["id"],
                    "name": svc["service_name"],
                    "destination": svc["destination"],
                    "service_type": svc["service_type"],
                    "duration": svc["duration"],
                    "notes": svc["notes"],
                    "supplier": svc["supplier"],
                    "company_code": svc["company_code"],
                    "tour_code": svc["tour_code"],
                    "rates": [dict(r) for r in rates],
                })

            log.info(f"Found {len(result)} total transfer options")
            return result

    except Exception as e:
        log.error(f"Error getting all transfers: {e}")
        return []


def get_attraction_by_id(attraction_id: int) -> Optional[Dict[str, Any]]:
    """Get a single attraction product by ID."""
    try:
        with _db() as conn:
            row = conn.execute(
                """
                SELECT 
                    id,
                    city,
                    attraction_name,
                    package_group,
                    package_label,
                    adult_net_price,
                    child_net_price,
                    senior_price,
                    supplier,
                    remarks,
                    source_row,
                    created_at
                FROM attraction_products
                WHERE id = ?
                """,
                (attraction_id,),
            ).fetchone()

            return dict(row) if row else None

    except Exception as e:
        log.error(f"Error getting attraction {attraction_id}: {e}")
        return None


def get_attraction_filter_options() -> Dict[str, List[str]]:
    """
    Get unique filter options for dropdowns.
    Returns dict with cities, suppliers, and package_groups.
    """
    log.info("Getting attraction filter options...")

    try:
        with _db() as conn:
            table_exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='attraction_products'"
            ).fetchone()
            
            if not table_exists:
                log.warning("attraction_products table does not exist")
                return {"cities": [], "suppliers": [], "package_groups": []}

            cities = [r[0] for r in conn.execute(
                "SELECT DISTINCT city FROM attraction_products "
                "WHERE city IS NOT NULL AND city != '' ORDER BY city"
            ).fetchall()]

            suppliers = [r[0] for r in conn.execute(
                "SELECT DISTINCT supplier FROM attraction_products "
                "WHERE supplier IS NOT NULL AND supplier != '' ORDER BY supplier"
            ).fetchall()]

            package_groups = [r[0] for r in conn.execute(
                "SELECT DISTINCT package_group FROM attraction_products "
                "WHERE package_group IS NOT NULL AND package_group != '' ORDER BY package_group"
            ).fetchall()]

            log.info(
                f"Filter options loaded: cities={len(cities)}, "
                f"suppliers={len(suppliers)}, groups={len(package_groups)}"
            )
            return {
                "cities": cities,
                "suppliers": suppliers,
                "package_groups": package_groups,
            }

    except Exception as e:
        log.error(f"Error getting filter options: {e}")
        return {"cities": [], "suppliers": [], "package_groups": []}


def get_city_stats() -> Dict[str, int]:
    """Get attraction count by city."""
    try:
        with _db() as conn:
            rows = conn.execute(
                """
                SELECT city, COUNT(*) as count
                FROM attraction_products
                GROUP BY city
                ORDER BY city
                """
            ).fetchall()
            return {r["city"]: r["count"] for r in rows}
    except Exception as e:
        log.error(f"Error getting city stats: {e}")
        return {}


def get_attraction_count() -> int:
    """Get total number of attraction products."""
    try:
        with _db() as conn:
            row = conn.execute("SELECT COUNT(*) FROM attraction_products").fetchone()
            return row[0] if row else 0
    except Exception as e:
        log.error(f"Error counting attractions: {e}")
        return 0


# ── Transfer Queries ─────────────────────────────────────────────────────────

def get_transfer_options_by_city(city: str) -> List[Dict[str, Any]]:
    """
    Get all transfer services for a given city from the existing services table.
    """
    log.info(f"Getting transfer options for city: {city}")

    if not city or not city.strip():
        return []

    try:
        with _db() as conn:
            sql = """
                SELECT 
                    s.id,
                    s.service_name,
                    s.destination,
                    s.service_type,
                    s.duration,
                    s.notes,
                    s.supplier,
                    s.company_code,
                    s.tour_code
                FROM services s
                WHERE s.destination = ?
                  AND s.service_type IN (
                    'Transfer', 'Disposal', 'On Disposal ( WC)', 'On Disposal ( IC)',
                    'Airport', 'Enroute', 'Enroute/Combi', 'Tour'
                  )
                ORDER BY s.service_type, s.service_name
            """
            
            rows = conn.execute(sql, (city,)).fetchall()
            result = []

            for svc in rows:
                rates = conn.execute(
                    """
                    SELECT 
                        rate_type,
                        vehicle,
                        pax_range,
                        pax_category,
                        price_thb
                    FROM rates
                    WHERE service_id = ?
                    ORDER BY rate_type, vehicle, pax_category
                    """,
                    (svc["id"],),
                ).fetchall()

                result.append({
                    "id": svc["id"],
                    "name": svc["service_name"],
                    "destination": svc["destination"],
                    "service_type": svc["service_type"],
                    "duration": svc["duration"],
                    "notes": svc["notes"],
                    "supplier": svc["supplier"],
                    "company_code": svc["company_code"],
                    "tour_code": svc["tour_code"],
                    "rates": [dict(r) for r in rates],
                })

            log.info(f"Found {len(result)} transfer options for {city}")
            return result

    except Exception as e:
        log.error(f"Error getting transfers for {city}: {e}")
        return []


def get_transfer_price(
    transfer_id: int,
    vehicle: str = None,
    pax_category: str = "PerVehicle"
) -> Optional[int]:
    """
    Get the price for a specific transfer option.
    
    Args:
        transfer_id: The service ID
        vehicle: Vehicle type (e.g., 'CAR', 'VAN', 'SUV')
        pax_category: 'PerVehicle', 'Adult', or 'Child'
    
    Returns:
        Price in THB or None if not found
    """
    try:
        with _db() as conn:
            sql = """
                SELECT price_thb
                FROM rates
                WHERE service_id = ?
                  AND pax_category = ?
            """
            params = [transfer_id, pax_category]

            if vehicle:
                sql += " AND vehicle = ?"
                params.append(vehicle)

            sql += " LIMIT 1"

            row = conn.execute(sql, params).fetchone()
            return row["price_thb"] if row else None

    except Exception as e:
        log.error(f"Error getting transfer price: {e}")
        return None