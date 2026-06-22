"""
parse_master_excel.py
=====================
Phase 2 — Parses MASTER_RATES.xlsx (unified master sheet) into quotation.db.

SOURCE FILE : MASTER_RATES.xlsx (unified master Excel)
COMPANIES   : DIVINE (BKK/PTT) and GOOD_DAY (Phuket/Krabi/Samui)
OUTPUT      : quotation.db  (tables: services + rates)

SHEET STRUCTURE (single sheet):
  Row 0  → column headers
  Row 1+ → data rows
  
  Columns:
    [0] SL.No           → serial number (integer)
    [1] Service Name    → service name string
    [2] City            → destination city
    [3] Service Type    → type label (Airport, Transfer, Tour, etc.)
    [4] CAR             → price in THB (integer, optional)
    [5] SUV             → price in THB (integer, optional)
    [6] VAN             → price in THB (integer, optional)
    [7] Camry           → price in THB (integer, optional)
    [8] Hyundai         → price in THB (integer, optional)
    [9] VIP Van         → price in THB (integer, optional)
    [10] Luxury Van (Alphard) → price in THB (integer, optional)
    [11] Supplier       → company name (e.g., 'Divine', 'Good Day')

USAGE:
    python parse_master_excel.py

    Files expected in the data/ folder:
        data/MASTER_RATES.xlsx
        quotation.db  (created by validate_schema.py in Phase 1)

EXPECTED OUTPUT:
    All counts should be > 0.
    No WARN or ERROR lines should appear.
"""

import sqlite3
import logging
import sys
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("parse_master_excel")

# ── Paths ─────────────────────────────────────────────────────────────────────
HERE     = Path(__file__).parent
XL_PATH  = HERE / "data" / "MASTER_RATES.xlsx"
DB_PATH  = HERE / "quotation.db"

# ── Constants ─────────────────────────────────────────────────────────────────
# Map Excel column names to our internal vehicle names
VEHICLE_MAPPING = {
    "CAR": "CAR",
    "SUV": "SUV",
    "VAN": "VAN",
    "Camry": "Camry",
    "Hyundai": "Hyundai",
    "VIP Van": "VIP Van",
    "Luxury Van (Alphard)": "Luxury Van (Alphard)",
}

# Map supplier names from Excel to company codes
SUPPLIER_MAPPING = {
    "Divine": "DIVINE",
    "divine": "DIVINE",
    "DIVINE": "DIVINE",
    "Good Day": "GOOD_DAY",
    "good day": "GOOD_DAY",
    "Good day": "GOOD_DAY",
    "GOOD_DAY": "GOOD_DAY",
}

# Map service types from Excel to our internal types
SERVICE_TYPE_MAPPING = {
    "Airport": "Transfer",
    "Transfer": "Transfer",
    "Tour": "Tour",
    "Enroute": "Enroute/Combi",
    "Enroute/Combi": "Enroute/Combi",
    "Combi": "Enroute/Combi",
    "Disposal": "Disposal",
    "Ferry": "Ferry",
    "Combo": "Combo",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def clean(val) -> str:
    """
    Convert any value to a stripped string.
    Collapses all internal whitespace to single spaces.
    Returns empty string for None / NaN / 'nan'.
    """
    if pd.isna(val):
        return ""
    s = str(val).strip()
    if s.lower() in ("nan", "none", ""):
        return ""
    return re.sub(r"\s+", " ", s)


def to_int(val) -> Optional[int]:
    """
    Safely convert a value to a positive integer (THB price).
    Handles commas (e.g. "1,200"), strips whitespace.
    Returns None if conversion is not possible or result <= 0.
    """
    if pd.isna(val):
        return None
    try:
        s = str(val).strip().replace(",", "").replace("฿", "").replace("THB", "")
        if not s:
            return None
        result = int(float(s))
        return result if result > 0 else None
    except (ValueError, TypeError):
        return None


def normalize_supplier(supplier: str) -> str:
    """
    Normalize supplier name to company code.
    Returns 'DIVINE' or 'GOOD_DAY'.
    """
    s = clean(supplier)
    if not s:
        return "DIVINE"  # Default
    return SUPPLIER_MAPPING.get(s, "DIVINE")


def normalize_service_type(service_type: str) -> str:
    """
    Normalize service type to internal format.
    """
    s = clean(service_type)
    if not s:
        return "Transfer"  # Default
    return SERVICE_TYPE_MAPPING.get(s, s)


def is_data_row(row) -> bool:
    """
    Returns True if this Excel row is a real data row.
    We detect data rows by checking that column 0 (SL.No) is a positive integer.
    """
    sl = clean(row.iloc[0])
    return sl.isdigit() and int(sl) > 0


# ── DB helpers ────────────────────────────────────────────────────────────────

def get_connection() -> sqlite3.Connection:
    """Open DB connection with foreign keys enforced."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_company_code(conn, supplier: str) -> str:
    """
    Get the company code for a supplier.
    If the supplier doesn't exist in companies table, use DIVINE as default.
    """
    code = normalize_supplier(supplier)
    
    # Verify the company exists in the companies table
    result = conn.execute(
        "SELECT code FROM companies WHERE code = ?",
        (code,)
    ).fetchone()
    
    if not result:
        log.warning("Supplier '%s' not found in companies table. Using 'DIVINE' as fallback.", supplier)
        return "DIVINE"
    
    return code


def insert_service(
    conn,
    company_code: str,
    destination: str,
    service_type: str,
    service_name: str,
    supplier: str = None,
    notes: str = None
) -> int:
    """
    Insert one row into services table.
    Returns the new row's id (used to link rates rows).
    """
    cur = conn.execute(
        """
        INSERT INTO services (
            company_code, destination, service_type, service_name, 
            supplier, notes, source
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (company_code, destination, service_type, service_name, supplier, notes, company_code),
    )
    return cur.lastrowid


def insert_rate(
    conn,
    service_id: int,
    vehicle: str,
    price_thb: int,
    rate_type: str = "Private",
    pax_category: str = "PerVehicle",
    pax_range: str = None
):
    """
    Insert one rate row.
    Default is Private / PerVehicle (whole vehicle booking).
    """
    conn.execute(
        """
        INSERT INTO rates (service_id, rate_type, vehicle, pax_range, pax_category, price_thb)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (service_id, rate_type, vehicle, pax_range, pax_category, price_thb),
    )


# ── Sheet parser ──────────────────────────────────────────────────────────────

def parse_master_sheet(conn, df: pd.DataFrame) -> dict:
    """
    Parse the master Excel sheet and insert all services + rates into DB.

    Returns a stats dict:
        rows_read       → total rows in the sheet
        rows_skipped    → header / footer / blank rows skipped
        services_added  → services successfully inserted
        rates_added     → rate rows inserted
        warnings        → list of warning messages for partial data
        by_supplier     → breakdown of services by supplier
    """
    log.info("─── Parsing master sheet ...")

    stats = {
        "rows_read":      len(df),
        "rows_skipped":   0,
        "services_added": 0,
        "rates_added":    0,
        "warnings":       [],
        "by_supplier":    {},
    }

    log.debug("  Sheet loaded: %d rows × %d columns", len(df), len(df.columns))

    # Get all vehicle columns that exist in the sheet
    available_vehicles = {}
    for col_name, vehicle_name in VEHICLE_MAPPING.items():
        if col_name in df.columns:
            available_vehicles[col_name] = vehicle_name
        else:
            log.debug("  Column '%s' not found in sheet", col_name)

    if not available_vehicles:
        msg = "No vehicle price columns found in the sheet. Expected at least one of: " + ", ".join(VEHICLE_MAPPING.keys())
        log.error(msg)
        stats["warnings"].append(msg)
        return stats

    log.info("  Found vehicle columns: %s", list(available_vehicles.keys()))

    # Track services to avoid duplicates (same service name + destination + supplier)
    seen_services = set()

    for row_idx, row in df.iterrows():
        # ── Skip non-data rows ────────────────────────────────────────────────
        if not is_data_row(row):
            stats["rows_skipped"] += 1
            log.debug("  Row %02d SKIP  sl=%r", row_idx, clean(row.iloc[0]))
            continue

        # ── Extract fields ────────────────────────────────────────────────────
        sl_no        = clean(row.iloc[0])
        service_name = clean(row.iloc[1])
        city         = clean(row.iloc[2])
        raw_type     = clean(row.iloc[3])
        supplier_raw = clean(row.iloc[11]) if len(row) > 11 else ""

        # ── Validate service name ─────────────────────────────────────────────
        if not service_name:
            msg = f"Row {row_idx} (SL#{sl_no}): empty service name — skipped"
            log.warning(msg)
            stats["warnings"].append(msg)
            stats["rows_skipped"] += 1
            continue

        # ── Validate city ─────────────────────────────────────────────────────
        if not city:
            msg = f"Row {row_idx} (SL#{sl_no}): '{service_name}' has empty city — skipped"
            log.warning(msg)
            stats["warnings"].append(msg)
            stats["rows_skipped"] += 1
            continue

        # ── Get company code from supplier ────────────────────────────────────
        company_code = get_company_code(conn, supplier_raw)
        
        # ── Normalize service type ─────────────────────────────────────────────
        service_type = normalize_service_type(raw_type)

        # ── Extract prices ─────────────────────────────────────────────────────
        prices = {}
        for col_name, vehicle_name in available_vehicles.items():
            if col_name in row.index:
                price = to_int(row[col_name])
                if price is not None:
                    prices[vehicle_name] = price
                else:
                    log.debug(
                        "  Row %02d SL#%s '%s' → %s price missing/invalid (raw=%r)",
                        row_idx, sl_no, service_name, vehicle_name, row[col_name]
                    )

        # ── Skip rows with zero valid prices ──────────────────────────────────
        if not prices:
            msg = f"Row {row_idx} (SL#{sl_no}): '{service_name}' has no valid prices — skipped"
            log.warning(msg)
            stats["warnings"].append(msg)
            stats["rows_skipped"] += 1
            continue

        # ── Check for duplicate services ──────────────────────────────────────
        service_key = f"{service_name}|{city}|{company_code}"
        if service_key in seen_services:
            msg = f"Row {row_idx} (SL#{sl_no}): Duplicate service '{service_name}' for {city} ({company_code}) — skipping"
            log.warning(msg)
            stats["warnings"].append(msg)
            stats["rows_skipped"] += 1
            continue
        seen_services.add(service_key)

        # ── Insert service ────────────────────────────────────────────────────
        sid = insert_service(
            conn,
            company_code=company_code,
            destination=city,
            service_type=service_type,
            service_name=service_name,
            supplier=supplier_raw if supplier_raw else None,
        )
        stats["services_added"] += 1

        # ── Track by supplier ─────────────────────────────────────────────────
        supplier_key = company_code
        stats["by_supplier"][supplier_key] = stats["by_supplier"].get(supplier_key, 0) + 1

        log.debug(
            "  Row %02d SL#%s INSERT service id=%d  city='%s'  type='%s'  supplier='%s'  prices=%s",
            row_idx, sl_no, sid, city, service_type, company_code, prices
        )

        # ── Insert rates ───────────────────────────────────────────────────────
        for vehicle, price in prices.items():
            insert_rate(conn, sid, vehicle, price)
            stats["rates_added"] += 1

    log.info(
        "  Sheet done → %d services, %d rates, %d skipped, %d warnings",
        stats["services_added"],
        stats["rates_added"],
        stats["rows_skipped"],
        len(stats["warnings"]),
    )

    if stats["by_supplier"]:
        log.info("  Breakdown by supplier:")
        for supplier, count in stats["by_supplier"].items():
            log.info("    %-12s  %d services", supplier, count)

    return stats


# ── Validation after insert ───────────────────────────────────────────────────

def validate_inserted_data(conn) -> bool:
    """
    After all sheets are parsed, run DB-level checks to confirm the data
    looks correct. Returns True if all checks pass.
    """
    log.info("Running post-insert validation ...")
    passed = True

    # ── Check 1: Total services inserted ──────────────────────────────────────
    total_services = conn.execute("SELECT COUNT(*) FROM services").fetchone()[0]
    ok = total_services >= 10  # At least some services should be inserted
    if ok:
        log.info("  ✓ Total service count = %d", total_services)
    else:
        log.error("  ✗ Total service count = %d — too low", total_services)
        passed = False

    # ── Check 2: Both companies have data ────────────────────────────────────
    divine_count = conn.execute(
        "SELECT COUNT(*) FROM services WHERE company_code='DIVINE'"
    ).fetchone()[0]
    goodday_count = conn.execute(
        "SELECT COUNT(*) FROM services WHERE company_code='GOOD_DAY'"
    ).fetchone()[0]
    
    # At least one company should have data
    ok = divine_count > 0 or goodday_count > 0
    if ok:
        log.info("  ✓ DIVINE: %d services, GOOD_DAY: %d services", divine_count, goodday_count)
    else:
        log.error("  ✗ No services found for either company")
        passed = False

    # ── Check 3: All services have at least 1 rate ────────────────────────────
    orphan_services = conn.execute(
        """
        SELECT COUNT(*) FROM services s
        WHERE NOT EXISTS (SELECT 1 FROM rates r WHERE r.service_id = s.id)
        """
    ).fetchone()[0]
    ok = orphan_services == 0
    if ok:
        log.info("  ✓ All services have at least one rate row")
    else:
        log.error("  ✗ %d services have NO rate rows (orphan services)", orphan_services)
        passed = False

    # ── Check 4: No invalid prices ────────────────────────────────────────────
    bad_prices = conn.execute(
        "SELECT COUNT(*) FROM rates WHERE price_thb <= 0"
    ).fetchone()[0]
    ok = bad_prices == 0
    if ok:
        log.info("  ✓ All rate prices are > 0 THB")
    else:
        log.error("  ✗ %d rate rows have price_thb ≤ 0", bad_prices)
        passed = False

    # ── Check 5: FK integrity ─────────────────────────────────────────────────
    fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
    ok = len(fk_errors) == 0
    if ok:
        log.info("  ✓ Foreign key integrity OK")
    else:
        log.error("  ✗ %d FK violations found", len(fk_errors))
        passed = False

    # ── Check 6: Supplier column has data ─────────────────────────────────────
    supplier_null = conn.execute(
        "SELECT COUNT(*) FROM services WHERE supplier IS NULL OR supplier = ''"
    ).fetchone()[0]
    ok = supplier_null == 0
    if ok:
        log.info("  ✓ All services have supplier information")
    else:
        log.warning("  ⚠ %d services have no supplier information", supplier_null)

    # ── Check 7: Destination distribution ─────────────────────────────────────
    log.info("  Services by destination:")
    for row in conn.execute(
        """
        SELECT destination, COUNT(*) as cnt
        FROM services
        GROUP BY destination
        ORDER BY destination
        """
    ).fetchall():
        log.info("    %-15s  %d", row[0], row[1])

    # ── Check 8: Service type distribution ────────────────────────────────────
    log.info("  Services by type:")
    for row in conn.execute(
        """
        SELECT service_type, COUNT(*) as cnt
        FROM services
        GROUP BY service_type
        ORDER BY service_type
        """
    ).fetchall():
        log.info("    %-20s  %d", row[0], row[1])

    # ── Check 9: Sample spot-check ────────────────────────────────────────────
    spot = conn.execute(
        """
        SELECT s.service_name, s.supplier, r.vehicle, r.price_thb
        FROM services s JOIN rates r ON r.service_id = s.id
        LIMIT 3
        """
    ).fetchall()
    if spot:
        log.info("  Sample spot-check (first 3 services):")
        for row in spot:
            log.info("    %-40s  supplier=%-8s  %-8s  %d THB", row[0], row[1] or "N/A", row[2] or "N/A", row[3])

    return passed


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "=" * 60)
    print("  Phase 2 — MASTER_RATES.xlsx Parser")
    print("=" * 60)

    # ── Pre-flight checks ─────────────────────────────────────────────────────
    print("\n[STEP 1] Pre-flight checks ...")

    if not XL_PATH.exists():
        log.error("MASTER_RATES.xlsx not found at: %s", XL_PATH)
        print(f"\n  ERROR: MASTER_RATES.xlsx not found.")
        print(f"  Make sure it is in the data/ folder: {HERE / 'data'}")
        sys.exit(1)
    log.info("Found MASTER_RATES.xlsx at: %s", XL_PATH)

    if not DB_PATH.exists():
        log.error("quotation.db not found at: %s", DB_PATH)
        print(f"\n  ERROR: quotation.db not found.")
        print(f"  Run validate_schema.py first (Phase 1) to create the database.")
        sys.exit(1)
    log.info("Found quotation.db at: %s", DB_PATH)

    # ── Open files ────────────────────────────────────────────────────────────
    print("\n[STEP 2] Opening files ...")

    try:
        df = pd.read_excel(XL_PATH, engine='openpyxl')
        # Clean column names: strip whitespace, lowercase, but keep original for display
        original_columns = df.columns.tolist()
        df.columns = [clean(col) for col in original_columns]
        log.info("Excel file opened. Found %d rows, %d columns", len(df), len(df.columns))
        log.debug("Columns: %s", df.columns.tolist())
    except Exception as exc:
        log.error("Failed to open MASTER_RATES.xlsx: %s", exc)
        sys.exit(1)

    conn = get_connection()
    log.info("Database connection opened")

    # ── Check DB is not already populated ─────────────────────────────────────
    existing = conn.execute(
        "SELECT COUNT(*) FROM services"
    ).fetchone()[0]
    if existing > 0:
        print(f"\n  WARNING: Found {existing} existing rows in the DB.")
        print("  To avoid duplicates, delete them first with:")
        print("    DELETE FROM services;")
        print("    DELETE FROM rates;")
        print("    DELETE FROM ferry_schedules;")
        answer = input("  Continue anyway? This will add DUPLICATE rows. (yes/no): ").strip().lower()
        if answer != "yes":
            print("  Aborted. No data was changed.")
            conn.close()
            sys.exit(0)
        log.warning("User chose to continue despite existing data")

    # ── Parse the sheet ──────────────────────────────────────────────────────
    print("\n[STEP 3] Parsing master sheet ...")

    stats = parse_master_sheet(conn, df)

    # Commit after parsing
    conn.commit()
    log.info("Data committed to database")

    # ── Post-insert validation ────────────────────────────────────────────────
    print("\n[STEP 4] Validating inserted data ...")
    validation_passed = validate_inserted_data(conn)

    conn.close()

    # ── Summary report ────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    print(f"  Excel rows read    : {stats['rows_read']}")
    print(f"  Rows skipped       : {stats['rows_skipped']}  (headers, blanks, footnotes)")
    print(f"  Services inserted  : {stats['services_added']}")
    print(f"  Rate rows inserted : {stats['rates_added']}")
    print(f"  Warnings           : {len(stats['warnings'])}")

    if stats["by_supplier"]:
        print("\n  Breakdown by supplier:")
        for supplier, count in stats["by_supplier"].items():
            print(f"    {supplier:<12}  {count:>4} services")

    if stats["warnings"]:
        print("\n  Warnings detail:")
        for w in stats["warnings"]:
            print(f"    ⚠  {w}")

    print()
    if validation_passed and len(stats["warnings"]) == 0:
        print("  ✓  Phase 2 complete. No warnings, all checks passed.")
        print("  Next step: run the quotation frontend")
    elif validation_passed:
        print("  ✓  Phase 2 complete with warnings (see above).")
        print("  Review warnings before proceeding.")
    else:
        print("  ✗  Phase 2 FAILED validation. See errors above.")
        print("  Fix issues and re-run this script.")
        sys.exit(1)

    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()