"""
parse_attractions.py
====================
Phase: Attractions Data Loader

Parses Attractions.xlsx and loads all attraction products into quotation.db.

SOURCE FILE : Attractions.xlsx
OUTPUT      : quotation.db (table: attraction_products)

SHEET STRUCTURE (Attractions sheet):
  Row 0  → column headers
  Row 1+ → data rows
  
  Columns:
    [0] City              → destination city
    [1] Attraction Name   → full attraction/product name
    [2] Adult Net Price   → adult price in THB
    [3] Child Net Price   → child price in THB (optional)
    [4] Senior            → senior price in THB (optional)
    [5] Supplier          → supplier name
    [6] Remarks           → additional notes (optional)

EDGE CASES HANDLED:
  - Blank/trailing rows → skipped silently
  - Kid-only attractions (no adult price) → use child price, add [KID-ONLY] tag
  - Child price as '-'  → treated as NULL
  - Multi-dash names    → split on first " - " only
  - Excel formulas      → evaluated (e.g., "=850+400")

USAGE:
    python parse_attractions.py

EXPECTED FILES:
    data/Attractions.xlsx
    quotation.db  (with attraction_products table created by migrate_add_attractions.py)
"""

import sqlite3
import logging
import sys
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

import pandas as pd

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("parse_attractions")

# ── Paths ─────────────────────────────────────────────────────────────────────
HERE = Path(__file__).parent
XL_PATH = HERE / "data" / "Attractions.xlsx"
DB_PATH = HERE / "quotation.db"

# ── Constants ─────────────────────────────────────────────────────────────────
VALID_CITIES = {"Bangkok", "Pattaya", "Phuket", "Krabi", "Koh Samui"}

# Keywords to identify kid-only attractions
KID_ONLY_KEYWORDS = [
    "baby", "child", "kid", "children", "junior", "infant", 
    "toddler", "mini", "small", "young"
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def clean(val: Any) -> str:
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


def to_int(val: Any) -> Optional[int]:
    """
    Safely convert a value to a positive integer (THB price).
    Handles commas (e.g. "1,200"), dashes ("-"), and formulas ("=850+400").
    Returns None if conversion is not possible or result <= 0.
    """
    if pd.isna(val):
        return None
    try:
        s = str(val).strip().replace(",", "").replace("฿", "").replace("THB", "")
        if not s or s == "-" or s.lower() in ("nan", "none", ""):
            return None
        # Handle formulas like "=850+400" 
        if "=" in s:
            parts = re.findall(r"(\d+)", s)
            if parts:
                result = sum(int(p) for p in parts)
                return result if result > 0 else None
        result = int(float(s))
        return result if result > 0 else None
    except (ValueError, TypeError):
        return None


def split_attraction_name(name: str) -> Tuple[str, Optional[str]]:
    """
    Split attraction name into package_group and package_label.
    Splits on the FIRST " - " only, preserving the rest of the name.
    
    Examples:
        "Grand Pearl - Ayutthaya Tour A - River City to Wat Chong Lom"
            → ("Grand Pearl", "Ayutthaya Tour A - River City to Wat Chong Lom")
        "Safari World - Package A (Safari+Marine+Lunch)"
            → ("Safari World", "Package A (Safari+Marine+Lunch)")
        "Dream World - Super Visa"
            → ("Dream World", "Super Visa")
        "Pattaya Kart Speedway - Baby Kart" (kid-only)
            → ("Pattaya Kart Speedway", "Baby Kart")
    """
    name = clean(name)
    if not name:
        return name, None
    
    # Look for " - " separator - split on FIRST occurrence only
    if " - " in name:
        parts = name.split(" - ", 1)  # maxsplit=1 ensures we only split once
        return parts[0].strip(), parts[1].strip()
    
    # Try " – " (en dash) as fallback
    if " – " in name:
        parts = name.split(" – ", 1)
        return parts[0].strip(), parts[1].strip()
    
    return name, None


def is_kid_only_attraction(name: str) -> bool:
    """
    Check if an attraction is kid-only based on its name.
    Returns True if the attraction is likely for children only.
    """
    name_lower = name.lower()
    for keyword in KID_ONLY_KEYWORDS:
        if keyword in name_lower:
            return True
    return False


def is_data_row(row: pd.Series) -> bool:
    """
    Returns True if this Excel row is a real data row.
    Blank rows (no city, no prices) are skipped silently.
    """
    city = clean(row.iloc[0]) if len(row) > 0 else ""
    if not city:
        return False
    
    # Check if row has ANY price (adult, child, or senior)
    adult_raw = row.iloc[2] if len(row) > 2 else None
    child_raw = row.iloc[3] if len(row) > 3 else None
    senior_raw = row.iloc[4] if len(row) > 4 else None
    
    adult_price = to_int(adult_raw)
    child_price = to_int(child_raw)
    senior_price = to_int(senior_raw)
    
    return adult_price is not None or child_price is not None or senior_price is not None


# ── DB helpers ────────────────────────────────────────────────────────────────

def get_connection() -> sqlite3.Connection:
    """Open DB connection with foreign keys enforced."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def insert_attraction_product(
    conn: sqlite3.Connection,
    city: str,
    attraction_name: str,
    package_group: str,
    package_label: Optional[str],
    adult_net_price: int,
    child_net_price: Optional[int],
    senior_price: Optional[int],
    supplier: str,
    remarks: Optional[str],
    source_row: Optional[int] = None,
) -> int:
    """
    Insert one row into attraction_products table.
    Returns the new row's id.
    """
    cur = conn.execute(
        """
        INSERT INTO attraction_products (
            city,
            attraction_name,
            package_group,
            package_label,
            adult_net_price,
            child_net_price,
            senior_price,
            supplier,
            remarks,
            source_row
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
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
        ),
    )
    return cur.lastrowid


def check_attraction_table_exists(conn: sqlite3.Connection) -> bool:
    """Check if the attraction_products table exists in the database."""
    result = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='attraction_products'"
    ).fetchone()
    return result is not None


# ── Sheet parser ──────────────────────────────────────────────────────────────

def parse_attractions_sheet(conn: sqlite3.Connection, df: pd.DataFrame) -> Dict[str, Any]:
    """
    Parse the Attractions sheet and insert all products into DB.

    Returns a stats dict:
        rows_read       → total rows in the sheet
        rows_skipped    → header / footer / blank rows skipped
        products_added  → products successfully inserted
        kid_only_count  → number of kid-only attractions processed
        warnings        → list of warning messages
        errors          → list of error messages
        by_city         → breakdown of products by city
        by_supplier     → breakdown of products by supplier
    """
    log.info("─── Parsing Attractions sheet ...")

    stats = {
        "rows_read": len(df),
        "rows_skipped": 0,
        "products_added": 0,
        "kid_only_count": 0,
        "warnings": [],
        "errors": [],
        "by_city": {},
        "by_supplier": {},
    }

    log.debug("  Sheet loaded: %d rows × %d columns", len(df), len(df.columns))

    # Validate columns
    if len(df.columns) < 7:
        msg = f"Sheet has only {len(df.columns)} columns, expected at least 7."
        log.error(msg)
        stats["errors"].append(msg)
        return stats

    for row_idx, row in df.iterrows():
        # ── Skip non-data rows ────────────────────────────────────────────────
        if not is_data_row(row):
            stats["rows_skipped"] += 1
            log.debug("  Row %02d SKIP (blank/header)", row_idx)
            continue

        # ── Extract fields ────────────────────────────────────────────────────
        city = clean(row.iloc[0]) if len(row) > 0 else ""
        attraction_name = clean(row.iloc[1]) if len(row) > 1 else ""
        adult_raw = row.iloc[2] if len(row) > 2 else None
        child_raw = row.iloc[3] if len(row) > 3 else None
        senior_raw = row.iloc[4] if len(row) > 4 else None
        supplier = clean(row.iloc[5]) if len(row) > 5 else ""
        remarks = clean(row.iloc[6]) if len(row) > 6 else ""

        # ── Convert prices ─────────────────────────────────────────────────────
        adult_price = to_int(adult_raw)
        child_price = to_int(child_raw)
        senior_price = to_int(senior_raw)

        # ── Special handling for kid-only attractions ────────────────────────
        is_kid_only = False
        price_fallback_used = False

        if adult_price is None and child_price is not None:
            # Check if it's a kid-only attraction
            if is_kid_only_attraction(attraction_name):
                is_kid_only = True
                adult_price = child_price
                stats["kid_only_count"] += 1
                
                # Add [KID-ONLY] tag to remarks
                if remarks:
                    remarks = f"[KID-ONLY] {remarks}"
                else:
                    remarks = "[KID-ONLY] Child-only attraction - no adult price available"
                
                msg = f"Row {row_idx}: Kid-only attraction '{attraction_name[:50]}'. Using child price ({child_price}) as adult price."
                log.warning(msg)
                stats["warnings"].append(msg)
            
            # For regular attractions with only child price (fallback)
            else:
                price_fallback_used = True
                adult_price = child_price
                
                # Add [FALLBACK] tag to remarks
                if remarks:
                    remarks = f"[FALLBACK] {remarks}"
                else:
                    remarks = "[FALLBACK] No adult price provided, using child price"
                
                msg = f"Row {row_idx}: No adult price for '{attraction_name[:50]}', using child price ({child_price}) as fallback. Please verify."
                log.warning(msg)
                stats["warnings"].append(msg)

        # ── Skip rows with no valid adult price ───────────────────────────────
        if adult_price is None:
            msg = f"Row {row_idx} (City: {city}, Name: {attraction_name[:50]}): No valid price found — skipped"
            log.warning(msg)
            stats["warnings"].append(msg)
            stats["rows_skipped"] += 1
            continue

        # ── Validate required fields ──────────────────────────────────────────
        if not city:
            msg = f"Row {row_idx}: empty city — skipped"
            log.warning(msg)
            stats["warnings"].append(msg)
            stats["rows_skipped"] += 1
            continue

        if not attraction_name:
            msg = f"Row {row_idx} (City: {city}): empty attraction name — skipped"
            log.warning(msg)
            stats["warnings"].append(msg)
            stats["rows_skipped"] += 1
            continue

        if not supplier:
            supplier = "Unknown"
            msg = f"Row {row_idx} (City: {city}): empty supplier, using 'Unknown'"
            log.warning(msg)
            stats["warnings"].append(msg)

        # ── Split attraction name ─────────────────────────────────────────────
        package_group, package_label = split_attraction_name(attraction_name)

        # ── Insert product ─────────────────────────────────────────────────────
        try:
            product_id = insert_attraction_product(
                conn=conn,
                city=city,
                attraction_name=attraction_name,
                package_group=package_group,
                package_label=package_label,
                adult_net_price=adult_price,
                child_net_price=child_price,
                senior_price=senior_price,
                supplier=supplier,
                remarks=remarks if remarks else None,
                source_row=row_idx + 1,  # 1-based row number
            )

            stats["products_added"] += 1

            # Track by city
            stats["by_city"][city] = stats["by_city"].get(city, 0) + 1
            # Track by supplier
            stats["by_supplier"][supplier] = stats["by_supplier"].get(supplier, 0) + 1

            # Log the insertion with appropriate tags
            tags = []
            if is_kid_only:
                tags.append("KID-ONLY")
            if price_fallback_used:
                tags.append("FALLBACK")
            
            tag_str = f" [{', '.join(tags)}]" if tags else ""
            
            log.debug(
                "  Row %02d INSERT id=%d  city='%s'  group='%s'  adult=%d  child=%s  senior=%s%s",
                row_idx, product_id, city, package_group, adult_price,
                child_price or "None", senior_price or "None", tag_str
            )

        except sqlite3.IntegrityError as e:
            msg = f"Row {row_idx}: database integrity error: {e}"
            log.error(msg)
            stats["errors"].append(msg)
            stats["rows_skipped"] += 1
            continue

    log.info(
        "  Sheet done → %d products, %d skipped, %d kid-only, %d warnings, %d errors",
        stats["products_added"],
        stats["rows_skipped"],
        stats["kid_only_count"],
        len(stats["warnings"]),
        len(stats["errors"]),
    )

    if stats["by_city"]:
        log.info("  Breakdown by city:")
        for city, count in sorted(stats["by_city"].items()):
            log.info("    %-15s  %d products", city, count)

    return stats


# ── Validation after insert ───────────────────────────────────────────────────

def validate_inserted_data(conn: sqlite3.Connection) -> bool:
    """
    After parsing, run DB-level checks to confirm the data looks correct.
    Returns True if all checks pass.
    """
    log.info("Running post-insert validation ...")
    passed = True

    # ── Check 1: Products were inserted ──────────────────────────────────────
    total_products = conn.execute(
        "SELECT COUNT(*) FROM attraction_products"
    ).fetchone()[0]
    ok = total_products > 0
    if ok:
        log.info("  ✓ Total products: %d", total_products)
    else:
        log.error("  ✗ No products found in attraction_products table")
        passed = False

    # ── Check 2: All products have positive prices ──────────────────────────
    invalid_prices = conn.execute(
        "SELECT COUNT(*) FROM attraction_products WHERE adult_net_price <= 0"
    ).fetchone()[0]
    ok = invalid_prices == 0
    if ok:
        log.info("  ✓ All adult prices are > 0")
    else:
        log.error("  ✗ %d products have invalid adult prices", invalid_prices)
        passed = False

    # ── Check 3: Package group is set ────────────────────────────────────────
    empty_group = conn.execute(
        "SELECT COUNT(*) FROM attraction_products WHERE package_group IS NULL OR package_group = ''"
    ).fetchone()[0]
    if empty_group == 0:
        log.info("  ✓ All products have package_group set")
    else:
        log.warning("  ⚠ %d products have empty package_group", empty_group)

    # ── Check 4: Count products with child and senior prices ──────────────────
    with_child = conn.execute(
        "SELECT COUNT(*) FROM attraction_products WHERE child_net_price IS NOT NULL AND child_net_price > 0"
    ).fetchone()[0]
    with_senior = conn.execute(
        "SELECT COUNT(*) FROM attraction_products WHERE senior_price IS NOT NULL AND senior_price > 0"
    ).fetchone()[0]
    log.info("  %d products have child prices", with_child)
    log.info("  %d products have senior prices", with_senior)

    # ── Check 5: Counts by city ──────────────────────────────────────────────
    log.info("  Product counts by city:")
    for row in conn.execute(
        """
        SELECT city, COUNT(*) as cnt
        FROM attraction_products
        GROUP BY city
        ORDER BY city
        """
    ).fetchall():
        log.info("    %-15s  %d", row[0], row[1])

    # ── Check 6: Find kid-only products (for verification) ────────────────────
    kid_only_products = conn.execute(
        """
        SELECT city, attraction_name, adult_net_price, child_net_price
        FROM attraction_products
        WHERE remarks LIKE '%KID-ONLY%'
        LIMIT 5
        """
    ).fetchall()
    
    if kid_only_products:
        log.info("  Sample kid-only products:")
        for row in kid_only_products:
            log.info(
                "    %-12s  %-40s  adult=%d  child=%s",
                row[0],
                row[1][:40],
                row[2],
                row[3] if row[3] else "None"
            )

    # ── Check 7: Sample spot-check ────────────────────────────────────────────
    spot = conn.execute(
        """
        SELECT city, attraction_name, adult_net_price, child_net_price, supplier
        FROM attraction_products
        LIMIT 5
        """
    ).fetchall()
    if spot:
        log.info("  Sample products (first 5):")
        for row in spot:
            log.info(
                "    %-12s  %-45s  adult=%d  child=%s  supplier=%s",
                row[0],
                row[1][:45],
                row[2],
                row[3] if row[3] else "None",
                row[4] or "Unknown"
            )

    return passed


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "=" * 60)
    print("  Attractions.xlsx Parser")
    print("=" * 60)

    # ── Pre-flight checks ─────────────────────────────────────────────────────
    print("\n[STEP 1] Pre-flight checks ...")

    if not XL_PATH.exists():
        log.error("Attractions.xlsx not found at: %s", XL_PATH)
        print(f"\n  ERROR: Attractions.xlsx not found.")
        print(f"  Make sure it is in the data/ folder: {HERE / 'data'}")
        sys.exit(1)
    log.info("Found Attractions.xlsx at: %s", XL_PATH)

    if not DB_PATH.exists():
        log.error("quotation.db not found at: %s", DB_PATH)
        print(f"\n  ERROR: quotation.db not found.")
        print(f"  Run migrate_add_attractions.py first to create the table.")
        sys.exit(1)
    log.info("Found quotation.db at: %s", DB_PATH)

    # ── Open files ────────────────────────────────────────────────────────────
    print("\n[STEP 2] Opening files ...")

    try:
        df = pd.read_excel(XL_PATH, sheet_name="Attractions", engine="openpyxl")
        log.info("Excel file opened. Found %d rows, %d columns", len(df), len(df.columns))
        log.debug("Columns: %s", df.columns.tolist())
    except Exception as exc:
        log.error("Failed to open Attractions.xlsx: %s", exc)
        sys.exit(1)

    # ── Check DB table exists ─────────────────────────────────────────────────
    conn = get_connection()
    log.info("Database connection opened")

    if not check_attraction_table_exists(conn):
        log.error("Table 'attraction_products' does not exist in the database.")
        print("\n  ERROR: attraction_products table not found.")
        print("  Run migrate_add_attractions.py first to create the table.")
        conn.close()
        sys.exit(1)

    # ── Check existing data ──────────────────────────────────────────────────
    existing = conn.execute(
        "SELECT COUNT(*) FROM attraction_products"
    ).fetchone()[0]
    if existing > 0:
        log.warning("Found %d existing attraction products in the DB.", existing)
        print(f"\n  WARNING: Found {existing} existing attraction products.")
        print("  To avoid duplicates, you can delete existing data first:")
        print("    DELETE FROM attraction_products;")
        print()
        answer = input("  Continue anyway? (yes/no): ").strip().lower()
        if answer != "yes":
            print("  Aborted. No data was changed.")
            conn.close()
            sys.exit(0)
        log.warning("User chose to continue despite existing data")

    # ── Parse the sheet ──────────────────────────────────────────────────────
    print("\n[STEP 3] Parsing Attractions sheet ...")

    stats = parse_attractions_sheet(conn, df)

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
    print(f"  Rows skipped       : {stats['rows_skipped']}  (headers, blanks, invalid rows)")
    print(f"  Products inserted  : {stats['products_added']}")
    print(f"  Kid-only products  : {stats['kid_only_count']}  (using child price as adult)")
    print(f"  Warnings           : {len(stats['warnings'])}")
    print(f"  Errors             : {len(stats['errors'])}")

    if stats["by_city"]:
        print("\n  Breakdown by city:")
        for city, count in sorted(stats["by_city"].items()):
            print(f"    {city:<15}  {count:>4} products")

    if stats["by_supplier"]:
        print("\n  Breakdown by supplier:")
        for supplier, count in sorted(stats["by_supplier"].items()):
            print(f"    {supplier:<20}  {count:>4} products")

    if stats["warnings"]:
        print("\n  Warnings detail (first 10):")
        for w in stats["warnings"][:10]:
            print(f"    ⚠  {w}")
        if len(stats["warnings"]) > 10:
            print(f"    ... and {len(stats['warnings']) - 10} more warnings")

    if stats["errors"]:
        print("\n  Errors:")
        for e in stats["errors"]:
            print(f"    ✗  {e}")

    print()
    if validation_passed and len(stats["errors"]) == 0:
        print("  ✓  Attractions parser complete. No errors, all checks passed.")
        if stats["warnings"]:
            print(f"  ⚠  {len(stats['warnings'])} warnings. Review them before using the data.")
        if stats["kid_only_count"] > 0:
            print(f"  ℹ  {stats['kid_only_count']} kid-only attractions processed (child price used as adult).")
    elif validation_passed:
        print("  ✓  Attractions parser complete with warnings (see above).")
    else:
        print("  ✗  Attractions parser FAILED validation. See errors above.")
        print("  Fix issues and re-run this script.")
        sys.exit(1)

    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()