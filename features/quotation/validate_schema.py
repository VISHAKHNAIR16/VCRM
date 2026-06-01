"""
validate_schema.py
==================
Phase 1 deliverable — creates quotation.db from schema.sql and runs
a full suite of validation checks to confirm everything is correct.

Run this FIRST before any data is loaded.

Usage:
    python validate_schema.py

Expected output (all checks must show ✓):
    [STEP 1] Creating database ...
    [STEP 2] Applying schema ...
    [STEP 3] Running validation checks ...
      ✓  All 4 tables exist
      ✓  All indexes exist
      ✓  companies seed data present (2 rows)
      ✓  Foreign key constraints enforced
      ✓  CHECK constraints enforced on rates.rate_type
      ✓  CHECK constraints enforced on rates.pax_category
      ✓  CHECK constraints enforced on rates.price_thb
      ✓  CASCADE delete works correctly
    [DONE] Schema is valid and ready for data loading.
"""

import sqlite3
import logging
import sys
from pathlib import Path

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("validate_schema")

# ── Paths ─────────────────────────────────────────────────────────────────────
HERE       = Path(__file__).parent
SCHEMA_SQL = HERE / "schema.sql"
DB_PATH    = HERE / "quotation.db"

# ── Helpers ───────────────────────────────────────────────────────────────────
PASS = "  ✓ "
FAIL = "  ✗ "

def check(label: str, condition: bool, detail: str = "") -> bool:
    """Print a pass/fail line. Returns True if passed."""
    if condition:
        print(f"{PASS} {label}")
        if detail:
            log.debug("    detail: %s", detail)
    else:
        print(f"{FAIL} {label}")
        if detail:
            log.error("    detail: %s", detail)
    return condition


def abort(message: str):
    """Print error and exit with code 1."""
    log.error("FATAL: %s", message)
    print(f"\n{'='*60}")
    print(f"  ABORTED: {message}")
    print(f"{'='*60}\n")
    sys.exit(1)


# ── Main validation ───────────────────────────────────────────────────────────
def main():
    all_passed = True

    # ──────────────────────────────────────────────────────────────────────────
    print("\n[STEP 1] Creating database ...")
    # ──────────────────────────────────────────────────────────────────────────

    # Delete existing DB so we always start fresh during validation
    if DB_PATH.exists():
        log.debug("Removing existing %s", DB_PATH)
        DB_PATH.unlink()
        print(f"  Removed existing {DB_PATH.name}")

    log.debug("Connecting to %s", DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    print(f"  Created {DB_PATH.name}")

    # ──────────────────────────────────────────────────────────────────────────
    print("\n[STEP 2] Applying schema ...")
    # ──────────────────────────────────────────────────────────────────────────

    if not SCHEMA_SQL.exists():
        abort(f"schema.sql not found at {SCHEMA_SQL}")

    schema_text = SCHEMA_SQL.read_text(encoding="utf-8")
    log.debug("schema.sql is %d characters", len(schema_text))

    try:
        conn.executescript(schema_text)
        print("  schema.sql executed successfully")
        log.debug("All SQL statements in schema.sql ran without error")
    except sqlite3.Error as exc:
        abort(f"schema.sql failed to execute: {exc}")

    # ──────────────────────────────────────────────────────────────────────────
    print("\n[STEP 3] Running validation checks ...")
    # ──────────────────────────────────────────────────────────────────────────

    # ── Check 1: All expected tables exist ────────────────────────────────────
    expected_tables = {"companies", "services", "rates", "ferry_schedules"}
    existing_tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    log.debug("Tables found: %s", existing_tables)
    missing = expected_tables - existing_tables
    all_passed &= check(
        "All 4 tables exist",
        len(missing) == 0,
        detail=f"Missing: {missing}" if missing else "companies, services, rates, ferry_schedules"
    )

    # ── Check 2: All expected indexes exist ───────────────────────────────────
    expected_indexes = {
        "idx_services_name",
        "idx_services_destination",
        "idx_services_type",
        "idx_services_company",
        "idx_rates_service",
        "idx_ferry_service",
    }
    existing_indexes = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
    }
    log.debug("Indexes found: %s", existing_indexes)
    missing_idx = expected_indexes - existing_indexes
    all_passed &= check(
        "All 6 indexes exist",
        len(missing_idx) == 0,
        detail=f"Missing: {missing_idx}" if missing_idx else "all present"
    )

    # ── Check 3: Seed data present ────────────────────────────────────────────
    company_count = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    companies     = conn.execute("SELECT code, name FROM companies ORDER BY code").fetchall()
    log.debug("Companies: %s", [(r["code"], r["name"]) for r in companies])
    all_passed &= check(
        "companies seed data present (2 rows)",
        company_count == 2,
        detail=f"Found {company_count} rows: {[r['code'] for r in companies]}"
    )

    # ── Check 4: DIVINE row has correct data ──────────────────────────────────
    divine = conn.execute(
        "SELECT * FROM companies WHERE code='DIVINE'"
    ).fetchone()
    all_passed &= check(
        "DIVINE company row has correct values",
        divine is not None and divine["includes_vat"] == 0 and divine["currency"] == "THB",
        detail=dict(divine) if divine else "row not found"
    )

    # ── Check 5: GOOD_DAY row has correct data ────────────────────────────────
    good_day = conn.execute(
        "SELECT * FROM companies WHERE code='GOOD_DAY'"
    ).fetchone()
    all_passed &= check(
        "GOOD_DAY company row has correct values",
        good_day is not None and good_day["includes_vat"] == 0 and good_day["currency"] == "THB",
        detail=dict(good_day) if good_day else "row not found"
    )

    # ── Check 6: Foreign key constraints are enforced ─────────────────────────
    fk_setting = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    log.debug("PRAGMA foreign_keys = %s", fk_setting)
    # schema.sql sets foreign_keys ON via PRAGMA, verify it took effect
    # We test by trying to insert a rate pointing to a non-existent service
    fk_caught = False
    conn.execute("PRAGMA foreign_keys = ON")  # ensure it's on for our test
    try:
        conn.execute(
            "INSERT INTO rates (service_id, rate_type, pax_category, price_thb) VALUES (9999, 'Private', 'PerVehicle', 100)"
        )
        conn.rollback()
        log.warning("FK constraint was NOT enforced — insert into rates with bad service_id succeeded")
    except sqlite3.IntegrityError as exc:
        fk_caught = True
        log.debug("FK constraint correctly raised IntegrityError: %s", exc)
    all_passed &= check(
        "Foreign key constraint enforced (rates → services)",
        fk_caught,
        detail="Correctly rejected INSERT with non-existent service_id=9999"
    )

    # ── Check 7: CHECK constraint on rate_type ─────────────────────────────────
    # First insert a real service so we can test rates against it
    conn.execute(
        """INSERT INTO services (company_code, destination, service_type, service_name)
           VALUES ('DIVINE', 'Bangkok', 'Transfer', '__test_service__')"""
    )
    test_sid = conn.execute(
        "SELECT id FROM services WHERE service_name='__test_service__'"
    ).fetchone()[0]
    log.debug("Inserted test service with id=%d", test_sid)

    bad_rate_type_caught = False
    try:
        conn.execute(
            "INSERT INTO rates (service_id, rate_type, pax_category, price_thb) VALUES (?, 'INVALID', 'PerVehicle', 100)",
            (test_sid,)
        )
        conn.rollback()
        log.warning("CHECK constraint on rate_type was NOT enforced")
    except sqlite3.IntegrityError as exc:
        bad_rate_type_caught = True
        log.debug("CHECK on rate_type correctly raised IntegrityError: %s", exc)
    all_passed &= check(
        "CHECK constraint enforced on rates.rate_type",
        bad_rate_type_caught,
        detail="Correctly rejected rate_type='INVALID' (must be Private or SIC)"
    )

    # ── Check 8: CHECK constraint on pax_category ──────────────────────────────
    bad_pax_caught = False
    try:
        conn.execute(
            "INSERT INTO rates (service_id, rate_type, pax_category, price_thb) VALUES (?, 'Private', 'INVALID', 100)",
            (test_sid,)
        )
        conn.rollback()
        log.warning("CHECK constraint on pax_category was NOT enforced")
    except sqlite3.IntegrityError as exc:
        bad_pax_caught = True
        log.debug("CHECK on pax_category correctly raised IntegrityError: %s", exc)
    all_passed &= check(
        "CHECK constraint enforced on rates.pax_category",
        bad_pax_caught,
        detail="Correctly rejected pax_category='INVALID' (must be PerVehicle/Adult/Child)"
    )

    # ── Check 9: CHECK constraint on price_thb > 0 ─────────────────────────────
    bad_price_caught = False
    try:
        conn.execute(
            "INSERT INTO rates (service_id, rate_type, pax_category, price_thb) VALUES (?, 'Private', 'PerVehicle', 0)",
            (test_sid,)
        )
        conn.rollback()
        log.warning("CHECK constraint on price_thb > 0 was NOT enforced")
    except sqlite3.IntegrityError as exc:
        bad_price_caught = True
        log.debug("CHECK on price_thb correctly raised IntegrityError: %s", exc)
    all_passed &= check(
        "CHECK constraint enforced on rates.price_thb > 0",
        bad_price_caught,
        detail="Correctly rejected price_thb=0 (must be > 0)"
    )

    # ── Check 10: CASCADE delete (deleting a service removes its rates) ─────────
    # Insert a real rate for our test service
    conn.execute(
        "INSERT INTO rates (service_id, rate_type, pax_category, price_thb) VALUES (?, 'Private', 'PerVehicle', 500)",
        (test_sid,)
    )
    rate_before = conn.execute(
        "SELECT COUNT(*) FROM rates WHERE service_id=?", (test_sid,)
    ).fetchone()[0]
    log.debug("Rate rows before delete: %d", rate_before)

    conn.execute("DELETE FROM services WHERE id=?", (test_sid,))
    rate_after = conn.execute(
        "SELECT COUNT(*) FROM rates WHERE service_id=?", (test_sid,)
    ).fetchone()[0]
    log.debug("Rate rows after service delete: %d", rate_after)

    all_passed &= check(
        "CASCADE delete works (deleting service removes its rates)",
        rate_before == 1 and rate_after == 0,
        detail=f"Rates before delete: {rate_before}, after: {rate_after}"
    )

    # ── Check 11: All tables start empty (no leftover test data) ───────────────
    # Clean up any test rows that didn't get cascade-deleted
    conn.execute("DELETE FROM services WHERE service_name='__test_service__'")
    conn.commit()

    all_empty = True
    for table in ["services", "rates", "ferry_schedules"]:
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if count != 0:
            all_empty = False
            log.warning("Table %s still has %d rows after cleanup", table, count)
    all_passed &= check(
        "All data tables empty (ready for parsers)",
        all_empty,
        detail="services=0, rates=0, ferry_schedules=0"
    )

    # ── Print column definitions for developer reference ───────────────────────
    print("\n  Column structure (for reference):")
    for table in ["companies", "services", "rates", "ferry_schedules"]:
        cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
        col_summary = ", ".join(
            f"{c['name']}:{c['type']}" + (" NOT NULL" if c['notnull'] else "")
            for c in cols
        )
        print(f"    {table:<20} → {col_summary}")

    conn.close()

    # ── Final result ────────────────────────────────────────────────────────────
    print()
    if all_passed:
        print("=" * 60)
        print("  [DONE] All checks passed. Schema is valid.")
        print(f"  Database saved to: {DB_PATH}")
        print("  Next step: run parse_bkk_ptt.py (Phase 2)")
        print("=" * 60)
    else:
        print("=" * 60)
        print("  [FAILED] One or more checks failed.")
        print("  Fix the errors above before proceeding to Phase 2.")
        print("=" * 60)
        sys.exit(1)


if __name__ == "__main__":
    main()
