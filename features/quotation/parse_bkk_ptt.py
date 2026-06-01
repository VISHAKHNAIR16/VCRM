"""
parse_bkk_ptt.py
================
Phase 2 — Parses BKK_PTT.xlsx and loads all 4 sheets into quotation.db.

SOURCE FILE : BKK_PTT.xlsx  (company: DIVINE)
SHEETS      : TRANSFER | TOUR | ENROUTE COMBI | DISPOSAL
OUTPUT      : quotation.db  (tables: services + rates)

SHEET STRUCTURE (all 4 sheets are identical):
  Row 0  → company header ("DIVINE")          ← SKIP
  Row 1  → column headers (SL.No, Name, ...)  ← SKIP
  Row 2+ → data rows
  Columns:
    [0] SL.No        → serial number (integer means valid data row)
    [1] Service Name → service name string
    [2] City         → destination city
    [3] Service Type → type label (Transfer, Tour, etc.)
    [4] CAR price    → integer THB
    [5] SUV price    → integer THB
    [6] VAN price    → integer THB

USAGE:
    python parse_bkk_ptt.py

    The script expects these files in the SAME folder:
        data/BKK_PTT.xlsx
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

import pandas as pd

# ── Logging setup ─────────────────────────────────────────────────────────────
# Shows timestamp + level + message. DEBUG lines give row-by-row detail.
# Change level=logging.INFO to suppress DEBUG lines in production.
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("parse_bkk_ptt")

# ── Paths ─────────────────────────────────────────────────────────────────────
HERE     = Path(__file__).parent
XL_PATH  = HERE / "data" / "BKK_PTT.xlsx"
DB_PATH  = HERE / "quotation.db"

# ── Constants ─────────────────────────────────────────────────────────────────
COMPANY_CODE = "DIVINE"

# Maps Excel column index (within the price block) → vehicle label
# Col 4 = CAR, Col 5 = SUV, Col 6 = VAN
VEHICLE_COLS = {4: "CAR", 5: "SUV", 6: "VAN"}

# How the sheet names in the Excel map to our service_type values in the DB.
# Key   = exact sheet name in Excel (case-sensitive)
# Value = service_type string stored in DB
SHEET_CONFIG = {
    "TRANSFER":      "Transfer",
    "TOUR":          "Tour",
    "ENROUTE COMBI": "Enroute/Combi",
    "DISPOSAL":      "Disposal",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def clean(val) -> str:
    """
    Convert any value to a stripped string.
    Collapses all internal whitespace to single spaces.
    Returns empty string for None / NaN / 'nan'.
    """
    s = str(val).strip()
    if s.lower() == "nan":
        return ""
    return re.sub(r"\s+", " ", s)


def to_int(val) -> int | None:
    """
    Safely convert a value to a positive integer (THB price).
    Handles commas (e.g. "1,200"), strips whitespace.
    Returns None if conversion is not possible or result <= 0.
    """
    try:
        s = str(val).strip().replace(",", "")
        result = int(float(s))
        return result if result > 0 else None
    except (ValueError, TypeError):
        return None


def is_data_row(row) -> bool:
    """
    Returns True if this Excel row is a real data row.
    We detect data rows by checking that column 0 (SL.No) is a positive integer.
    This naturally skips:
      - The company header row (row 0): contains 'DIVINE' or NaN
      - The column header row (row 1): contains 'SL.No'
      - Footer/footnote rows: contain '* WC - Within City', NaN, etc.
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


def insert_service(conn, destination: str, service_type: str, service_name: str, notes: str = None) -> int:
    """
    Insert one row into services table.
    Returns the new row's id (used to link rates rows).
    """
    cur = conn.execute(
        """
        INSERT INTO services (company_code, destination, service_type, service_name, notes)
        VALUES (?, ?, ?, ?, ?)
        """,
        (COMPANY_CODE, destination, service_type, service_name, notes),
    )
    return cur.lastrowid


def insert_rate(conn, service_id: int, vehicle: str, price_thb: int):
    """
    Insert one rate row.
    All BKK_PTT rates are:
      - Private (whole vehicle booked)
      - PerVehicle (single flat price, no per-person split)
    """
    conn.execute(
        """
        INSERT INTO rates (service_id, rate_type, vehicle, pax_category, price_thb)
        VALUES (?, 'Private', ?, 'PerVehicle', ?)
        """,
        (service_id, vehicle, price_thb),
    )


# ── Sheet parser ──────────────────────────────────────────────────────────────

def parse_sheet(conn, xl: pd.ExcelFile, sheet_name: str, service_type: str) -> dict:
    """
    Parse one sheet from BKK_PTT.xlsx and insert all services + rates into DB.

    Returns a stats dict:
        rows_read       → total rows in the sheet
        rows_skipped    → header / footer / blank rows skipped
        services_added  → services successfully inserted
        rates_added     → rate rows inserted
        warnings        → list of warning messages for partial data
    """
    log.info("─── Parsing sheet: '%s' → service_type='%s'", sheet_name, service_type)

    stats = {
        "rows_read":      0,
        "rows_skipped":   0,
        "services_added": 0,
        "rates_added":    0,
        "warnings":       [],
    }

    # Load the full sheet with no header inference (header=None keeps raw row numbers)
    df = pd.read_excel(xl, sheet_name=sheet_name, header=None)
    stats["rows_read"] = len(df)
    log.debug("  Sheet loaded: %d rows × %d columns", len(df), len(df.columns))

    # Validate that expected columns exist
    if len(df.columns) < 7:
        msg = f"Sheet '{sheet_name}' has only {len(df.columns)} columns, expected 7. Skipping entire sheet."
        log.error(msg)
        stats["warnings"].append(msg)
        return stats

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
        raw_type     = clean(row.iloc[3])  # read but we use service_type from sheet config

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

        # ── Extract prices ────────────────────────────────────────────────────
        prices = {}
        for col_idx, vehicle in VEHICLE_COLS.items():
            price = to_int(row.iloc[col_idx])
            if price is not None:
                prices[vehicle] = price
            else:
                log.debug(
                    "  Row %02d SL#%s '%s' → %s price missing/invalid (raw=%r)",
                    row_idx, sl_no, service_name, vehicle, row.iloc[col_idx]
                )

        # ── Skip rows with zero valid prices ──────────────────────────────────
        if not prices:
            msg = f"Row {row_idx} (SL#{sl_no}): '{service_name}' has no valid prices — skipped"
            log.warning(msg)
            stats["warnings"].append(msg)
            stats["rows_skipped"] += 1
            continue

        # ── Warn if only some vehicles have prices (unusual) ──────────────────
        if len(prices) < 3:
            missing_vehicles = [v for v in VEHICLE_COLS.values() if v not in prices]
            msg = (
                f"Row {row_idx} (SL#{sl_no}): '{service_name}' "
                f"missing prices for: {missing_vehicles} — inserting with available prices"
            )
            log.warning(msg)
            stats["warnings"].append(msg)

        # ── Insert service ────────────────────────────────────────────────────
        sid = insert_service(
            conn,
            destination=city,
            service_type=service_type,
            service_name=service_name,
        )
        stats["services_added"] += 1

        log.debug(
            "  Row %02d SL#%s INSERT service id=%d  city='%s'  name='%s'  prices=%s",
            row_idx, sl_no, sid, city, service_name, prices
        )

        # ── Insert rates ───────────────────────────────────────────────────────
        for vehicle, price in prices.items():
            insert_rate(conn, sid, vehicle, price)
            stats["rates_added"] += 1

    log.info(
        "  Sheet '%s' done → %d services, %d rates, %d skipped, %d warnings",
        sheet_name,
        stats["services_added"],
        stats["rates_added"],
        stats["rows_skipped"],
        len(stats["warnings"]),
    )
    return stats


# ── Validation after insert ───────────────────────────────────────────────────

def validate_inserted_data(conn) -> bool:
    """
    After all sheets are parsed, run DB-level checks to confirm the data
    looks correct. Returns True if all checks pass.
    """
    log.info("Running post-insert validation ...")
    passed = True

    # ── Check 1: At least 200 services inserted ───────────────────────────────
    # We know from the Excel audit: 79+98+39+16 = 232 valid rows
    total_services = conn.execute(
        "SELECT COUNT(*) FROM services WHERE company_code='DIVINE'"
    ).fetchone()[0]
    ok = total_services >= 200
    if ok:
        log.info("  ✓ Service count = %d (expected ≥ 200)", total_services)
    else:
        log.error("  ✗ Service count = %d — too low, expected ≥ 200", total_services)
        passed = False

    # ── Check 2: Every service has at least 1 rate ────────────────────────────
    orphan_services = conn.execute(
        """
        SELECT COUNT(*) FROM services s
        WHERE s.company_code = 'DIVINE'
          AND NOT EXISTS (SELECT 1 FROM rates r WHERE r.service_id = s.id)
        """
    ).fetchone()[0]
    ok = orphan_services == 0
    if ok:
        log.info("  ✓ All DIVINE services have at least one rate row")
    else:
        log.error("  ✗ %d DIVINE services have NO rate rows (orphan services)", orphan_services)
        passed = False

    # ── Check 3: No rate has price ≤ 0 (constraint should catch this, but double-check) ──
    bad_prices = conn.execute(
        """
        SELECT COUNT(*) FROM rates r
        JOIN services s ON s.id = r.service_id
        WHERE s.company_code = 'DIVINE' AND r.price_thb <= 0
        """
    ).fetchone()[0]
    ok = bad_prices == 0
    if ok:
        log.info("  ✓ All rate prices are > 0 THB")
    else:
        log.error("  ✗ %d rate rows have price_thb ≤ 0", bad_prices)
        passed = False

    # ── Check 4: All expected destinations present ────────────────────────────
    expected_cities = {"Bangkok", "Pattaya", "Hua Hin", "Kanchanaburi"}
    found_cities = {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT destination FROM services WHERE company_code='DIVINE'"
        ).fetchall()
    }
    missing_cities = expected_cities - found_cities
    ok = len(missing_cities) == 0
    if ok:
        log.info("  ✓ All expected cities present: %s", sorted(found_cities))
    else:
        log.error("  ✗ Missing cities: %s (found: %s)", missing_cities, found_cities)
        passed = False

    # ── Check 5: All expected service types present ───────────────────────────
    expected_types = {"Transfer", "Tour", "Enroute/Combi", "Disposal"}
    found_types = {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT service_type FROM services WHERE company_code='DIVINE'"
        ).fetchall()
    }
    missing_types = expected_types - found_types
    ok = len(missing_types) == 0
    if ok:
        log.info("  ✓ All expected service types present: %s", sorted(found_types))
    else:
        log.error("  ✗ Missing service types: %s (found: %s)", missing_types, found_types)
        passed = False

    # ── Check 6: Breakdown by service type ───────────────────────────────────
    log.info("  Service counts by type:")
    for row in conn.execute(
        """
        SELECT service_type, COUNT(*) as cnt
        FROM services WHERE company_code='DIVINE'
        GROUP BY service_type ORDER BY service_type
        """
    ).fetchall():
        log.info("    %-20s %d", row[0], row[1])

    # ── Check 7: Sample spot-check — first Bangkok Transfer ───────────────────
    spot = conn.execute(
        """
        SELECT s.service_name, r.vehicle, r.price_thb
        FROM services s JOIN rates r ON r.service_id = s.id
        WHERE s.company_code='DIVINE' AND s.service_type='Transfer' AND s.destination='Bangkok'
        ORDER BY s.id, r.vehicle
        LIMIT 3
        """
    ).fetchall()
    if spot:
        log.info("  Sample spot-check (first Bangkok Transfer rates):")
        for row in spot:
            log.info("    %-55s  %-4s  %d THB", row[0], row[1], row[2])
    else:
        log.error("  ✗ Spot-check failed: no Bangkok Transfer rates found")
        passed = False

    return passed


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "=" * 60)
    print("  Phase 2 — BKK_PTT.xlsx Parser")
    print("=" * 60)

    # ── Pre-flight checks ─────────────────────────────────────────────────────
    print("\n[STEP 1] Pre-flight checks ...")

    if not XL_PATH.exists():
        log.error("BKK_PTT.xlsx not found at: %s", XL_PATH)
        print(f"\n  ERROR: BKK_PTT.xlsx not found.")
        print(f"  Make sure it is in the same folder as this script: {HERE}")
        sys.exit(1)
    log.info("Found BKK_PTT.xlsx at: %s", XL_PATH)

    if not DB_PATH.exists():
        log.error("quotation.db not found at: %s", DB_PATH)
        print(f"\n  ERROR: quotation.db not found.")
        print(f"  Run validate_schema.py first (Phase 1) to create the database.")
        sys.exit(1)
    log.info("Found quotation.db at: %s", DB_PATH)

    # ── Open files ────────────────────────────────────────────────────────────
    print("\n[STEP 2] Opening files ...")

    try:
        xl = pd.ExcelFile(XL_PATH)
        log.info("Excel file opened. Sheets found: %s", xl.sheet_names)
    except Exception as exc:
        log.error("Failed to open BKK_PTT.xlsx: %s", exc)
        sys.exit(1)

    # Verify all expected sheets exist
    missing_sheets = [s for s in SHEET_CONFIG if s not in xl.sheet_names]
    if missing_sheets:
        log.error("Missing sheets in BKK_PTT.xlsx: %s", missing_sheets)
        print(f"\n  ERROR: These sheets are missing from BKK_PTT.xlsx: {missing_sheets}")
        sys.exit(1)
    log.info("All 4 expected sheets found: %s", list(SHEET_CONFIG.keys()))

    conn = get_connection()
    log.info("Database connection opened")

    # ── Check DB is not already populated with DIVINE data ────────────────────
    existing = conn.execute(
        "SELECT COUNT(*) FROM services WHERE company_code='DIVINE'"
    ).fetchone()[0]
    if existing > 0:
        print(f"\n  WARNING: Found {existing} existing DIVINE rows in the DB.")
        print("  To avoid duplicates, delete them first with:")
        print("    DELETE FROM services WHERE company_code='DIVINE';")
        answer = input("  Continue anyway? This will add DUPLICATE rows. (yes/no): ").strip().lower()
        if answer != "yes":
            print("  Aborted. No data was changed.")
            conn.close()
            sys.exit(0)
        log.warning("User chose to continue despite existing DIVINE data")

    # ── Parse all sheets ──────────────────────────────────────────────────────
    print("\n[STEP 3] Parsing sheets ...")

    grand_total = {"rows_read": 0, "rows_skipped": 0, "services_added": 0, "rates_added": 0}
    all_warnings = []

    for sheet_name, service_type in SHEET_CONFIG.items():
        stats = parse_sheet(conn, xl, sheet_name, service_type)
        for key in grand_total:
            grand_total[key] += stats[key]
        all_warnings.extend(stats["warnings"])

    # Commit only after all 4 sheets succeed
    conn.commit()
    log.info("All 4 sheets committed to database")

    # ── Post-insert validation ────────────────────────────────────────────────
    print("\n[STEP 4] Validating inserted data ...")
    validation_passed = validate_inserted_data(conn)

    conn.close()

    # ── Summary report ────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    print(f"  Excel rows read    : {grand_total['rows_read']}")
    print(f"  Rows skipped       : {grand_total['rows_skipped']}  (headers, blanks, footnotes)")
    print(f"  Services inserted  : {grand_total['services_added']}")
    print(f"  Rate rows inserted : {grand_total['rates_added']}")
    print(f"  Warnings           : {len(all_warnings)}")

    if all_warnings:
        print("\n  Warnings detail:")
        for w in all_warnings:
            print(f"    ⚠  {w}")

    print()
    if validation_passed and len(all_warnings) == 0:
        print("  ✓  Phase 2 complete. No warnings, all checks passed.")
        print("  Next step: run parse_good_day.py (Phase 3)")
    elif validation_passed:
        print("  ✓  Phase 2 complete with warnings (see above).")
        print("  Review warnings before proceeding to Phase 3.")
    else:
        print("  ✗  Phase 2 FAILED validation. See errors above.")
        print("  Fix issues and re-run this script.")
        sys.exit(1)

    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
