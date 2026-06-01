"""
build_db.py
===========
Phase 4 — Production master script.

Runs all three phases in one command:
    1. Wipes and recreates the database from schema.sql
    2. Parses BKK_PTT.xlsx  → inserts DIVINE services + rates
    3. Parses GOOD_DAY.xlsx → inserts GOOD_DAY services, rates, ferry schedules
    4. Prints a final integrity report

This is the ONE script you run whenever:
    - You update either Excel file with new rates
    - You deploy to a new machine
    - You want a guaranteed clean rebuild

USAGE:
    python build_db.py              ← full rebuild (recommended)
    python build_db.py --dry-run    ← validate files exist, don't touch DB

EXPECTED OUTPUT:
    All checks ✓, final summary shows 626 services, 1539 rates, 36 ferry rows.
"""

import argparse
import logging
import sqlite3
import sys
import time
from pathlib import Path

# ── We import the parsers directly so this is a single-file orchestrator ──────
# They must be in the same folder as this script.
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

try:
    import validate_schema
    import parse_bkk_ptt
    import parse_good_day
except ImportError as exc:
    print(f"\n  ERROR: Could not import a required module: {exc}")
    print(f"  Make sure these files are all in the same folder:")
    print(f"    validate_schema.py")
    print(f"    parse_bkk_ptt.py")
    print(f"    parse_good_day.py")
    print(f"    BKK_PTT.xlsx  (or in a data/ subfolder)")
    print(f"    GOOD_DAY.xlsx (or in a data/ subfolder)")
    sys.exit(1)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,                    # INFO for production; change to DEBUG for verbose
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("build_db")

DB_PATH = HERE / "quotation.db"


# ── Helpers ───────────────────────────────────────────────────────────────────

def divider(title: str = ""):
    width = 60
    if title:
        pad = (width - len(title) - 2) // 2
        print(f"\n{'─' * pad} {title} {'─' * pad}")
    else:
        print("─" * width)


def abort(msg: str):
    print(f"\n  ✗  FATAL: {msg}")
    print("  Build aborted. No data was changed.\n")
    sys.exit(1)


# ── Integrity report ──────────────────────────────────────────────────────────

def final_integrity_report() -> bool:
    """
    After all three phases, run a comprehensive DB check.
    Returns True if all checks pass.
    """
    divider("FINAL INTEGRITY REPORT")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    passed = True

    checks = []

    # ── Grand totals ──────────────────────────────────────────────────────────
    total_services = conn.execute("SELECT COUNT(*) FROM services").fetchone()[0]
    total_rates    = conn.execute("SELECT COUNT(*) FROM rates").fetchone()[0]
    total_ferry    = conn.execute("SELECT COUNT(*) FROM ferry_schedules").fetchone()[0]

    divine_svc   = conn.execute("SELECT COUNT(*) FROM services WHERE company_code='DIVINE'").fetchone()[0]
    goodday_svc  = conn.execute("SELECT COUNT(*) FROM services WHERE company_code='GOOD_DAY'").fetchone()[0]

    # ── Check 1: Minimum totals ───────────────────────────────────────────────
    ok = total_services >= 600 and total_rates >= 1400 and total_ferry >= 30
    checks.append(("Grand totals meet minimums", ok,
                   f"services={total_services}, rates={total_rates}, ferry={total_ferry}"))

    # ── Check 2: Both companies present ──────────────────────────────────────
    ok = divine_svc >= 200 and goodday_svc >= 300
    checks.append(("Both companies have data", ok,
                   f"DIVINE={divine_svc}, GOOD_DAY={goodday_svc}"))

    # ── Check 3: All destinations present ────────────────────────────────────
    dests = {r[0] for r in conn.execute("SELECT DISTINCT destination FROM services").fetchall()}
    expected = {"Bangkok", "Pattaya", "Hua Hin", "Kanchanaburi", "Phuket", "Krabi", "Samui"}
    missing  = expected - dests
    ok = len(missing) == 0
    checks.append(("All 7 destinations present", ok,
                   f"Found: {sorted(dests)}" if ok else f"Missing: {missing}"))

    # ── Check 4: All service types present ───────────────────────────────────
    types = {r[0] for r in conn.execute("SELECT DISTINCT service_type FROM services").fetchall()}
    expected_types = {"Transfer", "Tour", "Enroute/Combi", "Disposal", "Combo", "Ferry"}
    missing_types  = expected_types - types
    ok = len(missing_types) == 0
    checks.append(("All service types present", ok,
                   f"Found: {sorted(types)}" if ok else f"Missing: {missing_types}"))

    # ── Check 5: No orphan services ───────────────────────────────────────────
    orphans = conn.execute("""
        SELECT COUNT(*) FROM services s
        WHERE s.service_type != 'Ferry'
          AND NOT EXISTS (SELECT 1 FROM rates r WHERE r.service_id = s.id)
    """).fetchone()[0]
    ok = orphans == 0
    checks.append(("No orphan services (missing rates)", ok,
                   f"0 orphan services" if ok else f"{orphans} services have no rate rows"))

    # ── Check 6: No invalid prices ────────────────────────────────────────────
    bad_prices = conn.execute("SELECT COUNT(*) FROM rates WHERE price_thb <= 0").fetchone()[0]
    ok = bad_prices == 0
    checks.append(("All prices > 0 THB", ok,
                   "All clean" if ok else f"{bad_prices} rates with price ≤ 0"))

    # ── Check 7: FK integrity ─────────────────────────────────────────────────
    fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
    ok = len(fk_errors) == 0
    checks.append(("Foreign key integrity", ok,
                   "All FK references valid" if ok else f"{len(fk_errors)} FK violations"))

    # ── Check 8: Ferry schedules have piers ──────────────────────────────────
    missing_piers = conn.execute(
        "SELECT COUNT(*) FROM ferry_schedules WHERE depart_pier IS NULL OR depart_pier=''"
    ).fetchone()[0]
    ok = missing_piers == 0
    checks.append(("All ferry schedules have departure pier", ok,
                   "All present" if ok else f"{missing_piers} rows missing departure pier"))

    # ── Print checks ──────────────────────────────────────────────────────────
    for label, ok, detail in checks:
        icon = "  ✓" if ok else "  ✗"
        print(f"{icon}  {label}")
        if not ok:
            print(f"       {detail}")
            passed = False
        else:
            log.debug("     %s", detail)

    # ── Breakdown table ───────────────────────────────────────────────────────
    print("\n  Services by company × destination:")
    rows = conn.execute("""
        SELECT company_code, destination, COUNT(*) as cnt
        FROM services
        GROUP BY company_code, destination
        ORDER BY company_code, destination
    """).fetchall()
    for r in rows:
        print(f"    {r['company_code']:<12}  {r['destination']:<15}  {r['cnt']:>4} services")

    print("\n  Services by type:")
    rows = conn.execute("""
        SELECT service_type, COUNT(*) as cnt
        FROM services GROUP BY service_type ORDER BY service_type
    """).fetchall()
    for r in rows:
        print(f"    {r['service_type']:<20}  {r['cnt']:>4}")

    conn.close()
    return passed


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Quotation DB builder")
    parser.add_argument("--dry-run", action="store_true",
                        help="Only check that input files exist. Do NOT touch the database.")
    args = parser.parse_args()

    t_start = time.time()

    print("\n" + "=" * 60)
    print("  Quotation DB — Full Rebuild")
    print("=" * 60)

    # ── Dry-run mode ──────────────────────────────────────────────────────────
    if args.dry_run:
        print("\n  [DRY RUN] Checking input files only ...\n")
        all_ok = True
        for path in [validate_schema.SCHEMA_SQL,
                     parse_bkk_ptt.XL_PATH,
                     parse_good_day.XL_PATH]:
            exists = path.exists()
            icon = "  ✓" if exists else "  ✗"
            print(f"{icon}  {path.name}  ({path})")
            if not exists:
                all_ok = False
        print()
        if all_ok:
            print("  All input files found. Run without --dry-run to build the DB.")
        else:
            print("  Some files are missing. Fix paths before running the full build.")
        sys.exit(0 if all_ok else 1)

    # ══════════════════════════════════════════════════════════════════════════
    # PHASE 1 — Schema
    # ══════════════════════════════════════════════════════════════════════════
    divider("PHASE 1 — Schema")
    print("  Recreating database from schema.sql ...")

    try:
        validate_schema.main()          # creates quotation.db and validates it
    except SystemExit as exc:
        if exc.code != 0:
            abort("Schema validation failed. See errors above.")

    log.info("Phase 1 complete")

    # ══════════════════════════════════════════════════════════════════════════
    # PHASE 2 — BKK_PTT
    # ══════════════════════════════════════════════════════════════════════════
    divider("PHASE 2 — BKK_PTT.xlsx (DIVINE)")
    print("  Parsing BKK_PTT.xlsx ...")

    try:
        parse_bkk_ptt.main()
    except SystemExit as exc:
        if exc.code != 0:
            abort("BKK_PTT parser failed. See errors above.")

    log.info("Phase 2 complete")

    # ══════════════════════════════════════════════════════════════════════════
    # PHASE 3 — GOOD_DAY
    # ══════════════════════════════════════════════════════════════════════════
    divider("PHASE 3 — GOOD_DAY.xlsx (GOOD_DAY)")
    print("  Parsing GOOD_DAY.xlsx ...")

    try:
        parse_good_day.main()
    except SystemExit as exc:
        if exc.code != 0:
            abort("GOOD_DAY parser failed. See errors above.")

    log.info("Phase 3 complete")

    # ══════════════════════════════════════════════════════════════════════════
    # PHASE 4 — Final integrity report
    # ══════════════════════════════════════════════════════════════════════════
    all_passed = final_integrity_report()

    elapsed = time.time() - t_start

    # ── Final result ──────────────────────────────────────────────────────────
    print()
    print("=" * 60)
    if all_passed:
        print(f"  ✓  BUILD COMPLETE  ({elapsed:.1f}s)")
        print(f"  Database: {DB_PATH}")
        print(f"  Ready for Phase 5 — quotation frontend")
    else:
        print(f"  ✗  BUILD FAILED — see errors above")
        print(f"  Fix the issues and re-run: python build_db.py")
        sys.exit(1)
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
