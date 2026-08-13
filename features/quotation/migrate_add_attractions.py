"""
migrate_add_attractions.py
===========================
One-time, SAFE migration: adds the `attraction_products` table (+ indexes)
to an EXISTING quotation.db, without touching companies/services/rates/
ferry_schedules/zone_surcharges/addons.

This is NOT the same as build_db.py / validate_schema.py, which WIPE the
whole database. Do not run those after this — they will delete your real
508 services / 1357 rates. This script only ADDS the new table.

Safe to run multiple times (uses CREATE TABLE IF NOT EXISTS).

Usage:
    python migrate_add_attractions.py
"""

import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).parent
DB_PATH = HERE / "quotation.db"

# Only the NEW statements — never re-run the full schema.sql against a live DB.
ATTRACTION_PRODUCTS_SQL = """
CREATE TABLE IF NOT EXISTS attraction_products (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    city              TEXT    NOT NULL,
    attraction_name   TEXT    NOT NULL,
    package_group     TEXT    NOT NULL,
    package_label     TEXT,
    adult_net_price   INTEGER NOT NULL CHECK(adult_net_price > 0),
    child_net_price   INTEGER CHECK(child_net_price IS NULL OR child_net_price > 0),
    senior_price      INTEGER CHECK(senior_price IS NULL OR senior_price > 0),
    supplier          TEXT    NOT NULL,
    remarks           TEXT,
    source_row        INTEGER,
    created_at        TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_attraction_products_city
    ON attraction_products(city);
CREATE INDEX IF NOT EXISTS idx_attraction_products_supplier
    ON attraction_products(supplier);
CREATE INDEX IF NOT EXISTS idx_attraction_products_name
    ON attraction_products(attraction_name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_attraction_products_group
    ON attraction_products(package_group COLLATE NOCASE);
"""


def main():
    if not DB_PATH.exists():
        print(f"  FATAL: {DB_PATH} not found. Run this from the folder containing quotation.db.")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")

    # ── Snapshot existing row counts BEFORE migration, to prove nothing was touched ──
    before = {}
    for t in ["companies", "services", "rates", "ferry_schedules", "zone_surcharges", "addons"]:
        try:
            before[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except sqlite3.OperationalError:
            before[t] = None  # table didn't exist, fine

    already_existed = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='attraction_products'"
    ).fetchone()

    print("\n" + "=" * 60)
    print("  Migration: add attraction_products table")
    print("=" * 60)
    print(f"  DB: {DB_PATH}")
    print(f"  attraction_products already exists: {bool(already_existed)}")

    conn.executescript(ATTRACTION_PRODUCTS_SQL)
    conn.commit()

    # ── Verify existing tables untouched ──
    print("\n  Row counts (before -> after, must be unchanged for existing tables):")
    all_ok = True
    for t, before_count in before.items():
        after_count = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        status = "OK" if before_count == after_count else "CHANGED (!)"
        if before_count != after_count:
            all_ok = False
        print(f"    {t:<20} {before_count!s:>6} -> {after_count!s:>6}   [{status}]")

    attraction_count = conn.execute("SELECT COUNT(*) FROM attraction_products").fetchone()[0]
    print(f"    {'attraction_products':<20} {'—':>6} -> {attraction_count!s:>6}   [NEW TABLE]")

    conn.close()

    print()
    if all_ok:
        print("  [DONE] Migration successful. Existing data untouched.")
        print("  Next: run parse_attractions_excel.py to load Attractions.xlsx")
    else:
        print("  [WARNING] Some existing table row counts changed! Investigate before proceeding.")
        sys.exit(1)
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()