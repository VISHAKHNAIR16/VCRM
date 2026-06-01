"""
parse_good_day.py
=================
Phase 3 — Parses GOOD_DAY.xlsx (9 data sheets) into quotation.db.

SOURCE FILE : GOOD_DAY.xlsx  (company: GOOD_DAY)
DESTINATION : Phuket / Krabi / Samui
SHEETS PARSED:
  1. Transportation Arrival   → Airport-to-zone transfers (6 vehicle types)
  2. Pick up and Drop off     → Roundtrip tour transfers (zone × CAR/VAN matrix)
                                + Private intercity transfers (Car/SUV/VAN)
  3. Ferry Ticket             → Ferry schedules with departure times & per-person pricing
  4. Tour Attraction in Phuket→ SIC tours (adult/child), laid out in 2 side-by-side columns
  5. Samui Transfers & TOUR   → Samui from/to transfers + Samui SIC tours
  6. Tour Excursion in Krabi  → Krabi SIC tours (with/without national park fee)
  7. Combo Rates              → Phuket combo tours (Car/Fortuner + Van)
  8. Krabi Phuket Combo       → Intercity enroute combo tours
  9. Disposal                 → Phuket disposal (by duration, Car/Van)

USAGE:
    python parse_good_day.py

    Files expected in the SAME folder:
        GOOD_DAY.xlsx
        quotation.db   (created by validate_schema.py, already has DIVINE data)

EXPECTED OUTPUT:
    All counts > 0, no ERROR lines, 0 warnings ideal.
"""

import sqlite3
import logging
import sys
import re
from pathlib import Path

import pandas as pd

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("parse_good_day")

# ── Paths ─────────────────────────────────────────────────────────────────────
HERE    = Path(__file__).parent
XL_PATH = HERE / "data" / "GOOD_DAY.xlsx"
DB_PATH = HERE / "quotation.db"

COMPANY_CODE = "GOOD_DAY"

# ── Skip keywords — rows containing these are footnotes/remarks, not data ─────
SKIP_KEYWORDS = [
    "remark", "note:", "free join", "private transfer:", "above boat",
    "for return trip", "hotel zone", "pier zone", "all phuket",
    "transfer enroute", "service airport", "type of vehicle",
    "destination / location", "roundtrip tour", "private transfer from",
    "service join ferry", "hotel pick-up", "cost of phuket",
    "goodday vacation", "phuket city on disposal", "within phuket city",
    "krabi phuket or", "all intercity", "transfer rate",
    "tour / excursion", "tour in krabi", "tour in samui",
    "private car + driver", "including national park",
    "excluding national park", "exclude national park",
    "price/person", "baht / person", "baht/person",
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def clean(val) -> str:
    s = str(val).strip()
    if s.lower() in ("nan", "none", ""):
        return ""
    return re.sub(r"\s+", " ", s)


def to_int(val) -> int | None:
    try:
        s = str(val).strip().replace(",", "")
        result = int(float(s))
        return result if result > 0 else None
    except (ValueError, TypeError):
        return None


def is_skip_row(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in SKIP_KEYWORDS)


# ── DB helpers ────────────────────────────────────────────────────────────────

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def insert_service(conn, destination: str, service_type: str,
                   service_name: str, duration: str = None, notes: str = None) -> int:
    cur = conn.execute(
        """INSERT INTO services (company_code, destination, service_type, service_name, duration, notes)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (COMPANY_CODE, destination, service_type, service_name, duration, notes),
    )
    return cur.lastrowid


def insert_rate(conn, service_id: int, rate_type: str, vehicle: str,
                pax_category: str, price_thb: int, pax_range: str = None):
    if price_thb is None:
        return
    conn.execute(
        """INSERT INTO rates (service_id, rate_type, vehicle, pax_range, pax_category, price_thb)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (service_id, rate_type, vehicle, pax_range, pax_category, price_thb),
    )


def insert_ferry(conn, service_id: int, route: str, depart_time: str,
                 depart_pier: str, arrive_time: str, arrive_pier: str,
                 price_adult: int, price_child: int):
    conn.execute(
        """INSERT INTO ferry_schedules
           (service_id, route, depart_time, depart_pier, arrive_time, arrive_pier, price_adult, price_child)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (service_id, route, depart_time, depart_pier, arrive_time, arrive_pier, price_adult, price_child),
    )


# ── SHEET 1: Transportation Arrival ──────────────────────────────────────────
# Structure: col0=zone/area, col1-6=vehicle prices
# Row0=title, Row1=header, Row2=vehicle names, Row3=pax ranges, Row4=section label
# Data rows start at row 5. Section labels ("Hotel Zone", "Pier Zone") are skipped.

ARRIVAL_VEHICLES = {
    1: ("Sedan/SUV",  "1-3 pax"),
    2: ("D4D Van",    "4-8 pax"),
    3: ("Camry",      "1-2 pax"),
    4: ("Hyundai",    "1-4 pax"),
    5: ("VIP Van",    "1-5 pax"),
    6: ("Alphard",    "1-4 pax"),
}

def parse_transportation_arrival(conn) -> dict:
    log.info("─── Sheet: Transportation Arrival")
    stats = {"services_added": 0, "rates_added": 0, "warnings": []}

    df = pd.read_excel(XL_PATH, sheet_name="Transportation Arrival", header=None)
    log.debug("  Loaded %d rows × %d cols", len(df), len(df.columns))

    for row_idx, row in df.iloc[4:].iterrows():
        zone = clean(row.iloc[0])
        if not zone or is_skip_row(zone):
            log.debug("  Row %02d SKIP  zone=%r", row_idx, zone)
            continue

        prices = {}
        for col_idx, (vehicle, pax_range) in ARRIVAL_VEHICLES.items():
            p = to_int(row.iloc[col_idx]) if col_idx < len(row) else None
            if p:
                prices[(vehicle, pax_range)] = p

        if not prices:
            log.debug("  Row %02d SKIP  no prices  zone=%r", row_idx, zone)
            continue

        service_name = f"Phuket Airport to {zone}"
        sid = insert_service(conn, "Phuket", "Transfer", service_name)
        stats["services_added"] += 1

        for (vehicle, pax_range), price in prices.items():
            insert_rate(conn, sid, "Private", vehicle, "PerVehicle", price, pax_range)
            stats["rates_added"] += 1

        log.debug("  Row %02d INSERT id=%d  '%s'  prices=%d", row_idx, sid, service_name, len(prices))

    log.info("  Done → %d services, %d rates, %d warnings",
             stats["services_added"], stats["rates_added"], len(stats["warnings"]))
    return stats


# ── SHEET 2: Pick up and Drop off ────────────────────────────────────────────
# Two sections in one sheet:
# SECTION A (rows 0-23):  Roundtrip transfers — destination × hotel-zone matrix
#   Col layout: col0=destination, then pairs (CAR,VAN) for 5 hotel zones
#   Zone columns: Patong(1,2), Karon(3,4), Kata(5,6), Kamala/Laguna(7,8), Maikhao/Rawai/Panwa(9,10)
# SECTION B (rows 25+): Intercity private transfers — col0=name, col8=Car, col9=SUV, col10=VAN

PICKUP_ZONES = [
    ("Patong Beach",          1, 2),
    ("Karon Beach",           3, 4),
    ("Kata Beach",            5, 6),
    ("Kamala/Laguna",         7, 8),
    ("Maikhao/Rawai/Panwa",   9, 10),
]

def parse_pickup_dropoff(conn) -> dict:
    log.info("─── Sheet: Pick up and Drop off")
    stats = {"services_added": 0, "rates_added": 0, "warnings": []}

    df = pd.read_excel(XL_PATH, sheet_name="Pick up and Drop off ", header=None)
    log.debug("  Loaded %d rows × %d cols", len(df), len(df.columns))

    # ── Section A: Roundtrip tour transfers (rows 3-23) ───────────────────────
    log.debug("  Parsing Section A: roundtrip tour transfers")
    for row_idx in range(3, 24):
        if row_idx >= len(df):
            break
        row = df.iloc[row_idx]
        dest = clean(row.iloc[0])
        if not dest or is_skip_row(dest):
            continue

        for zone_label, car_col, van_col in PICKUP_ZONES:
            car_price = to_int(row.iloc[car_col]) if car_col < len(row) else None
            van_price = to_int(row.iloc[van_col]) if van_col < len(row) else None

            if car_price is None and van_price is None:
                continue

            service_name = f"{dest} ({zone_label} pickup)"
            sid = insert_service(conn, "Phuket", "Transfer", service_name,
                                 notes=f"Roundtrip hotel pickup from {zone_label}")
            stats["services_added"] += 1

            if car_price:
                insert_rate(conn, sid, "Private", "CAR", "PerVehicle", car_price)
                stats["rates_added"] += 1
            if van_price:
                insert_rate(conn, sid, "Private", "VAN", "PerVehicle", van_price)
                stats["rates_added"] += 1

            log.debug("  Row %02d INSERT id=%d  '%s'  CAR=%s VAN=%s",
                      row_idx, sid, service_name, car_price, van_price)

    # ── Section B: Intercity private transfers (rows 28+) ─────────────────────
    log.debug("  Parsing Section B: intercity transfers")
    for row_idx in range(28, len(df)):
        row = df.iloc[row_idx]
        name = clean(row.iloc[0])
        if not name or is_skip_row(name):
            continue

        # Car=col8, SUV=col9, VAN=col10
        car = to_int(row.iloc[8])  if len(row) > 8  else None
        suv = to_int(row.iloc[9])  if len(row) > 9  else None
        van = to_int(row.iloc[10]) if len(row) > 10 else None

        if car is None and suv is None and van is None:
            continue

        sid = insert_service(conn, "Phuket", "Transfer", name)
        stats["services_added"] += 1

        for vehicle, price in [("Car/Fortuner", car), ("SUV", suv), ("VAN", van)]:
            if price:
                insert_rate(conn, sid, "Private", vehicle, "PerVehicle", price)
                stats["rates_added"] += 1

        log.debug("  Row %02d INSERT id=%d  '%s'  CAR=%s SUV=%s VAN=%s",
                  row_idx, sid, name, car, suv, van)

    log.info("  Done → %d services, %d rates, %d warnings",
             stats["services_added"], stats["rates_added"], len(stats["warnings"]))
    return stats


# ── SHEET 3: Ferry Ticket ─────────────────────────────────────────────────────
# Multiple route-blocks separated by header rows containing "Destination from X"
# Within each block: row with route name (col0) + times+piers (col1-4) + prices (col5-6)
# Continuation rows: col0 empty, just times+piers+prices for extra departure times

def parse_ferry(conn) -> dict:
    log.info("─── Sheet: Ferry Ticket")
    stats = {"services_added": 0, "rates_added": 0, "warnings": []}

    df = pd.read_excel(XL_PATH, sheet_name="Ferry Ticket", header=None)
    log.debug("  Loaded %d rows × %d cols", len(df), len(df.columns))

    current_route = None
    current_sid   = None

    for row_idx, row in df.iterrows():
        col0 = clean(row.iloc[0])
        col1 = clean(row.iloc[1]) if len(row) > 1 else ""
        col2 = clean(row.iloc[2]) if len(row) > 2 else ""
        col3 = clean(row.iloc[3]) if len(row) > 3 else ""
        col4 = clean(row.iloc[4]) if len(row) > 4 else ""
        col5 = clean(row.iloc[5]) if len(row) > 5 else ""
        col6 = clean(row.iloc[6]) if len(row) > 6 else ""

        # Skip header/remark rows
        if not col0 and not col1:
            continue
        if is_skip_row(col0) or is_skip_row(col1):
            continue
        if col0.lower().startswith("destination from") or col0.lower() == "nan":
            continue
        # Skip the column header row "Time / Pier / Time / Pier / Adult / Child"
        if col1.lower() == "time" or col0.lower() == "time":
            continue

        # ── Try to parse a departure time (HH:MM:SS or HH:MM) from col1 ───────
        depart_time = None
        time_match = re.match(r"(\d{2}:\d{2})(?::\d{2})?", str(row.iloc[1]).strip())
        if time_match:
            depart_time = time_match.group(1)

        # ── Prices (col5=adult, col6=child) ──────────────────────────────────
        price_adult = to_int(col5) if col5 not in ("-", "") else None
        price_child = to_int(col6) if col6 not in ("-", "") else None

        # ── New route row: col0 has the route name ─────────────────────────────
        if col0 and depart_time:
            current_route = col0
            # Create one service per route (multiple schedules share the same service)
            current_sid = insert_service(conn, "Phuket", "Ferry", current_route)
            stats["services_added"] += 1
            log.debug("  Row %02d NEW route id=%d  '%s'", row_idx, current_sid, current_route)

        # ── Continuation row: just another departure time for current route ────
        elif depart_time and current_sid:
            log.debug("  Row %02d  EXTRA schedule for '%s'  depart=%s", row_idx, current_route, depart_time)

        else:
            continue

        # ── Insert ferry schedule row ─────────────────────────────────────────
        if depart_time and current_sid:
            depart_pier  = col2
            arrive_time_m = re.match(r"(\d{2}:\d{2})(?::\d{2})?", str(row.iloc[3]).strip())
            arrive_time  = arrive_time_m.group(1) if arrive_time_m else col3
            arrive_pier  = col4

            insert_ferry(conn, current_sid, current_route,
                         depart_time, depart_pier, arrive_time, arrive_pier,
                         price_adult, price_child)
            stats["rates_added"] += 1

            log.debug("  Row %02d   schedule: %s %s→%s %s | adult=%s child=%s",
                      row_idx, depart_time, depart_pier, arrive_time, arrive_pier,
                      price_adult, price_child)

    log.info("  Done → %d ferry routes, %d schedules, %d warnings",
             stats["services_added"], stats["rates_added"], len(stats["warnings"]))
    return stats


# ── SHEET 4: Tour Attraction in Phuket ───────────────────────────────────────
# Layout: TWO side-by-side tour blocks per row group.
#   Left block:  col0=name, col1=adult, col2=child
#   Right block: col4=name, col5=adult, col6=child
# A "block header" row has tour group name in col0 (or col4) and "Price/Person" in col1 (or col5)
# Data rows: col0 has service name, col1+col2 have prices
# Skip rows: remarks, zone surcharge rows ("Kamala Beach..."), "Including/Excluding" rows

def parse_tour_phuket(conn) -> dict:
    log.info("─── Sheet: Tour Attraction in Phuket")
    stats = {"services_added": 0, "rates_added": 0, "warnings": []}

    df = pd.read_excel(XL_PATH, sheet_name="Tour Attraction in Phuket", header=None)
    log.debug("  Loaded %d rows × %d cols", len(df), len(df.columns))

    def try_insert_tour(row_idx, name_raw, adult_raw, child_raw):
        name = clean(name_raw)
        if not name or is_skip_row(name):
            return
        # Skip note rows that aren't tour names
        if any(kw in name.lower() for kw in [
            "remark", "pick up time", "child age", "free join",
            "extra transfer", "baht / person", "private transfer",
            "including", "excluding", "not recommended",
        ]):
            return

        adult = to_int(adult_raw)
        child = to_int(child_raw)

        if adult is None and child is None:
            return

        sid = insert_service(conn, "Phuket", "Tour", name)
        stats["services_added"] += 1

        if adult:
            insert_rate(conn, sid, "SIC", None, "Adult", adult)
            stats["rates_added"] += 1
        if child:
            insert_rate(conn, sid, "SIC", None, "Child", child)
            stats["rates_added"] += 1

        log.debug("  Row %02d INSERT id=%d  '%s'  adult=%s child=%s",
                  row_idx, sid, name, adult, child)

    for row_idx, row in df.iterrows():
        vals = [clean(row.iloc[i]) if i < len(row) else "" for i in range(7)]

        # Left block
        try_insert_tour(row_idx, vals[0], vals[1], vals[2])
        # Right block
        try_insert_tour(row_idx, vals[4], vals[5], vals[6])

    log.info("  Done → %d services, %d rates, %d warnings",
             stats["services_added"], stats["rates_added"], len(stats["warnings"]))
    return stats


# ── SHEET 5: Samui Transfers & TOUR EXCURSION ─────────────────────────────────
# Two sections:
# A) Transfer tables: section header "Transfer Rate – From X", then From/To/Price rows
# B) Tours: header "TOUR / EXCURSION IN SAMUI", then service rows col0=name, col2=adult, col3=child

def parse_samui(conn) -> dict:
    log.info("─── Sheet: Samui Transfers & TOUR EXCURSI")
    stats = {"services_added": 0, "rates_added": 0, "warnings": []}

    df = pd.read_excel(XL_PATH, sheet_name="Samui Transfers & TOUR  EXCURSI", header=None)
    log.debug("  Loaded %d rows × %d cols", len(df), len(df.columns))

    in_tours = False

    for row_idx, row in df.iterrows():
        col0 = clean(row.iloc[0])
        col1 = clean(row.iloc[1]) if len(row) > 1 else ""
        col2 = clean(row.iloc[2]) if len(row) > 2 else ""
        col3 = clean(row.iloc[3]) if len(row) > 3 else ""

        if not col0 and not col1 and not col2:
            continue

        # ── Detect section switch to tours ────────────────────────────────────
        if "tour / excursion in samui" in col0.lower():
            in_tours = True
            log.debug("  Row %02d → switching to TOURS section", row_idx)
            continue

        # ── Skip header / remark rows ─────────────────────────────────────────
        if is_skip_row(col0) or col0.lower() in ("from", "trip", "remark:"):
            continue
        if col0.lower().startswith("transfer rate"):
            continue
        if col0.lower().startswith("- "):  # bullet remark lines
            continue
        if col0.lower().startswith("●"):   # tour header bullet without price
            pass  # fall through to price check

        # ── TOURS section ─────────────────────────────────────────────────────
        if in_tours:
            # Strip leading bullet "● " if present
            name = re.sub(r"^●\s*", "", col0).strip()
            if not name or is_skip_row(name):
                continue
            # Skip column header row
            if name.lower() in ("tour in samui",):
                continue

            adult = to_int(col2)
            child = to_int(col3) if col3 not in ("-", "") else None

            if adult is None:
                continue

            sid = insert_service(conn, "Samui", "Tour", name)
            stats["services_added"] += 1
            if adult:
                insert_rate(conn, sid, "SIC", None, "Adult", adult)
                stats["rates_added"] += 1
            if child:
                insert_rate(conn, sid, "SIC", None, "Child", child)
                stats["rates_added"] += 1
            log.debug("  Row %02d INSERT Tour id=%d  '%s'  adult=%s child=%s",
                      row_idx, sid, name, adult, child)

        # ── TRANSFERS section: From → To with single price ────────────────────
        else:
            # Skip "Around Samui Island" disposal-style rows too (col1=empty, col2=price)
            if not col1 and col2:
                # This is a single-line service (no "to" destination)
                service_name = col0.replace("\n", " ").strip()
                price = to_int(col2)
                if price and service_name:
                    sid = insert_service(conn, "Samui", "Transfer", service_name)
                    stats["services_added"] += 1
                    insert_rate(conn, sid, "Private", None, "PerVehicle", price)
                    stats["rates_added"] += 1
                    log.debug("  Row %02d INSERT Transfer id=%d  '%s'  price=%s",
                              row_idx, sid, service_name, price)
                continue

            frm = col0
            to  = col1
            price = to_int(col2)

            if not frm or not to or price is None:
                continue

            service_name = f"Samui: {frm} → {to}"
            sid = insert_service(conn, "Samui", "Transfer", service_name)
            stats["services_added"] += 1
            insert_rate(conn, sid, "Private", None, "PerVehicle", price)
            stats["rates_added"] += 1
            log.debug("  Row %02d INSERT Transfer id=%d  '%s'  price=%s",
                      row_idx, sid, service_name, price)

    log.info("  Done → %d services, %d rates, %d warnings",
             stats["services_added"], stats["rates_added"], len(stats["warnings"]))
    return stats


# ── SHEET 6: Tour Excursion in Krabi ─────────────────────────────────────────
# col0=name, col2=adult(excl park fee), col3=child(excl), col4=adult(incl), col5=child(incl)
# We store both price variants as separate services with notes.

def parse_krabi_tours(conn) -> dict:
    log.info("─── Sheet: Tour Excursion in Krabi")
    stats = {"services_added": 0, "rates_added": 0, "warnings": []}

    df = pd.read_excel(XL_PATH, sheet_name="Tour  Excursion in Krabi", header=None)
    log.debug("  Loaded %d rows × %d cols", len(df), len(df.columns))

    for row_idx, row in df.iterrows():
        vals = [clean(row.iloc[i]) if i < len(row) else "" for i in range(6)]
        name = vals[0]

        if not name or is_skip_row(name):
            continue
        if name.lower() in ("tour in krabi", "tour / excursion in krabi (join trip)"):
            continue
        # Skip the column header row
        if "price/person" in name.lower() or "ticket only" in name.lower():
            continue

        # Clean up multi-line cell content
        name = re.sub(r"\s+", " ", name.replace("\n", " ")).strip()

        adult_excl = to_int(vals[2])
        child_excl = to_int(vals[3]) if vals[3] not in ("-", "") else None
        adult_incl = to_int(vals[4])
        child_incl = to_int(vals[5]) if vals[5] not in ("-", "") else None

        # Insert variant 1: excluding national park fee
        if adult_excl:
            sid = insert_service(conn, "Krabi", "Tour",
                                 f"{name} (Excl. Park Fee)", notes="Ticket only, excluding national park fee")
            stats["services_added"] += 1
            insert_rate(conn, sid, "SIC", None, "Adult", adult_excl)
            stats["rates_added"] += 1
            if child_excl:
                insert_rate(conn, sid, "SIC", None, "Child", child_excl)
                stats["rates_added"] += 1
            log.debug("  Row %02d INSERT id=%d  '%s (Excl)'  adult=%s", row_idx, sid, name, adult_excl)

        # Insert variant 2: including national park fee
        if adult_incl:
            sid = insert_service(conn, "Krabi", "Tour",
                                 f"{name} (Incl. Park Fee)", notes="Includes national park entrance fee")
            stats["services_added"] += 1
            insert_rate(conn, sid, "SIC", None, "Adult", adult_incl)
            stats["rates_added"] += 1
            if child_incl:
                insert_rate(conn, sid, "SIC", None, "Child", child_incl)
                stats["rates_added"] += 1
            log.debug("  Row %02d INSERT id=%d  '%s (Incl)'  adult=%s", row_idx, sid, name, adult_incl)

    log.info("  Done → %d services, %d rates, %d warnings",
             stats["services_added"], stats["rates_added"], len(stats["warnings"]))
    return stats


# ── SHEET 7: Combo Rates (Phuket City Combos) ────────────────────────────────
# col0=name, col1=SIC price (sometimes), col2=Car/Fortuner, col3=Van
# SIC column is mostly empty; Car and Van always present for data rows.

def parse_combo_rates(conn) -> dict:
    log.info("─── Sheet: Combo Rates")
    stats = {"services_added": 0, "rates_added": 0, "warnings": []}

    df = pd.read_excel(XL_PATH, sheet_name="Combo Rates", header=None)
    log.debug("  Loaded %d rows × %d cols", len(df), len(df.columns))

    for row_idx, row in df.iterrows():
        vals = [clean(row.iloc[i]) if i < len(row) else "" for i in range(4)]
        name = vals[0]

        if not name or is_skip_row(name):
            continue
        # Skip header rows
        if name.lower() in ("sic", "car / fortuner") or "combo transfer" in name.lower():
            continue
        if "phuket city combo" in name.lower():
            continue

        sic = to_int(vals[1])
        car = to_int(vals[2])
        van = to_int(vals[3])

        if car is None and van is None:
            continue

        sid = insert_service(conn, "Phuket", "Combo", name)
        stats["services_added"] += 1

        if sic:
            insert_rate(conn, sid, "SIC", None, "Adult", sic)
            stats["rates_added"] += 1
        if car:
            insert_rate(conn, sid, "Private", "Car/Fortuner", "PerVehicle", car)
            stats["rates_added"] += 1
        if van:
            insert_rate(conn, sid, "Private", "VAN", "PerVehicle", van)
            stats["rates_added"] += 1

        log.debug("  Row %02d INSERT id=%d  '%s'  SIC=%s CAR=%s VAN=%s",
                  row_idx, sid, name, sic, car, van)

    log.info("  Done → %d services, %d rates, %d warnings",
             stats["services_added"], stats["rates_added"], len(stats["warnings"]))
    return stats


# ── SHEET 8: Krabi Phuket Combo ──────────────────────────────────────────────
# col0=name, col1=SIC (always "NA"), col2=Car/Fortuner, col3=Van

def parse_krabi_phuket_combo(conn) -> dict:
    log.info("─── Sheet: Krabi Phuket Combo")
    stats = {"services_added": 0, "rates_added": 0, "warnings": []}

    df = pd.read_excel(XL_PATH, sheet_name="Krabi Phuket Combo", header=None)
    log.debug("  Loaded %d rows × %d cols", len(df), len(df.columns))

    for row_idx, row in df.iterrows():
        vals = [clean(row.iloc[i]) if i < len(row) else "" for i in range(4)]
        name = vals[0]

        if not name or is_skip_row(name):
            continue
        if name.lower() in ("transfer enroute combo tours",) or "enroute combo" in name.lower():
            continue
        if "note :" in name.lower() or "all intercity" in name.lower():
            continue

        car = to_int(vals[2])
        van = to_int(vals[3])

        if car is None and van is None:
            continue

        sid = insert_service(conn, "Krabi", "Combo", name,
                             notes="Krabi–Phuket intercity enroute combo")
        stats["services_added"] += 1

        if car:
            insert_rate(conn, sid, "Private", "Car/Fortuner", "PerVehicle", car)
            stats["rates_added"] += 1
        if van:
            insert_rate(conn, sid, "Private", "VAN", "PerVehicle", van)
            stats["rates_added"] += 1

        log.debug("  Row %02d INSERT id=%d  '%s'  CAR=%s VAN=%s",
                  row_idx, sid, name, car, van)

    log.info("  Done → %d services, %d rates, %d warnings",
             stats["services_added"], stats["rates_added"], len(stats["warnings"]))
    return stats


# ── SHEET 9: Disposal (Phuket) ────────────────────────────────────────────────
# Two sub-sections:
# A) Within Phuket: col1=duration, col2=Car price, col3=Van price
# B) Phuket↔Krabi intercity disposal: col0=name, col1=duration, col2=Car, col3=Van

def parse_disposal(conn) -> dict:
    log.info("─── Sheet: Disposal (Phuket)")
    stats = {"services_added": 0, "rates_added": 0, "warnings": []}

    df = pd.read_excel(XL_PATH, sheet_name="Disposal", header=None)
    log.debug("  Loaded %d rows × %d cols", len(df), len(df.columns))

    for row_idx, row in df.iterrows():
        vals = [clean(row.iloc[i]) if i < len(row) else "" for i in range(4)]
        col0, col1, col2, col3 = vals

        if not col1 and not col2:
            continue
        if is_skip_row(col0) or is_skip_row(col1):
            continue
        # Skip header rows
        if col1.lower() in ("duration", "car /fortuner", "van"):
            continue

        duration = col1 if col1 else None
        car      = to_int(col2)
        van      = to_int(col3)

        if car is None and van is None:
            continue

        # Section A: within city (col0 is empty or the section header)
        if not col0 or "within phuket" in col0.lower() or "phuket city on disposal" in col0.lower():
            service_name = f"Phuket Disposal {duration}"
        else:
            # Section B: intercity disposal
            service_name = f"{col0} ({duration})"

        sid = insert_service(conn, "Phuket", "Disposal", service_name, duration=duration)
        stats["services_added"] += 1

        if car:
            insert_rate(conn, sid, "Private", "Car/Fortuner", "PerVehicle", car)
            stats["rates_added"] += 1
        if van:
            insert_rate(conn, sid, "Private", "VAN", "PerVehicle", van)
            stats["rates_added"] += 1

        log.debug("  Row %02d INSERT id=%d  '%s'  CAR=%s VAN=%s",
                  row_idx, sid, service_name, car, van)

    log.info("  Done → %d services, %d rates, %d warnings",
             stats["services_added"], stats["rates_added"], len(stats["warnings"]))
    return stats


# ── Validation ────────────────────────────────────────────────────────────────

def validate_inserted_data(conn) -> bool:
    log.info("Running post-insert validation ...")
    passed = True

    # Check 1: Minimum service count
    total = conn.execute(
        "SELECT COUNT(*) FROM services WHERE company_code='GOOD_DAY'"
    ).fetchone()[0]
    ok = total >= 150
    if ok:
        log.info("  ✓ GOOD_DAY service count = %d (expected ≥ 150)", total)
    else:
        log.error("  ✗ Service count = %d — too low", total)
        passed = False

    # Check 2: All expected destinations present
    expected = {"Phuket", "Krabi", "Samui"}
    found = {r[0] for r in conn.execute(
        "SELECT DISTINCT destination FROM services WHERE company_code='GOOD_DAY'"
    ).fetchall()}
    missing = expected - found
    ok = len(missing) == 0
    if ok:
        log.info("  ✓ All destinations present: %s", sorted(found))
    else:
        log.error("  ✗ Missing destinations: %s", missing)
        passed = False

    # Check 3: All expected service types present
    expected_types = {"Transfer", "Tour", "Ferry", "Combo", "Disposal"}
    found_types = {r[0] for r in conn.execute(
        "SELECT DISTINCT service_type FROM services WHERE company_code='GOOD_DAY'"
    ).fetchall()}
    missing_types = expected_types - found_types
    ok = len(missing_types) == 0
    if ok:
        log.info("  ✓ All service types present: %s", sorted(found_types))
    else:
        log.error("  ✗ Missing service types: %s", missing_types)
        passed = False

    # Check 4: No orphan services (every service has at least 1 rate or ferry schedule)
    orphans = conn.execute("""
        SELECT COUNT(*) FROM services s
        WHERE s.company_code = 'GOOD_DAY'
          AND NOT EXISTS (SELECT 1 FROM rates r WHERE r.service_id = s.id)
          AND NOT EXISTS (SELECT 1 FROM ferry_schedules f WHERE f.service_id = s.id)
    """).fetchone()[0]
    ok = orphans == 0
    if ok:
        log.info("  ✓ No orphan services (all have rates or ferry schedules)")
    else:
        log.error("  ✗ %d services have no rates AND no ferry schedules", orphans)
        # Show which ones
        rows = conn.execute("""
            SELECT id, service_type, service_name FROM services s
            WHERE s.company_code = 'GOOD_DAY'
              AND NOT EXISTS (SELECT 1 FROM rates r WHERE r.service_id = s.id)
              AND NOT EXISTS (SELECT 1 FROM ferry_schedules f WHERE f.service_id = s.id)
            LIMIT 5
        """).fetchall()
        for r in rows:
            log.error("    orphan id=%d type=%s name=%s", r[0], r[1], r[2])
        passed = False

    # Check 5: Ferry schedules present
    ferry_count = conn.execute("SELECT COUNT(*) FROM ferry_schedules").fetchone()[0]
    ok = ferry_count >= 20
    if ok:
        log.info("  ✓ Ferry schedules count = %d (expected ≥ 20)", ferry_count)
    else:
        log.error("  ✗ Ferry schedules count = %d — too low", ferry_count)
        passed = False

    # Check 6: Breakdown by destination and type
    log.info("  Breakdown by destination:")
    for r in conn.execute("""
        SELECT destination, COUNT(*) FROM services
        WHERE company_code='GOOD_DAY'
        GROUP BY destination ORDER BY destination
    """).fetchall():
        log.info("    %-15s %d", r[0], r[1])

    log.info("  Breakdown by service type:")
    for r in conn.execute("""
        SELECT service_type, COUNT(*) FROM services
        WHERE company_code='GOOD_DAY'
        GROUP BY service_type ORDER BY service_type
    """).fetchall():
        log.info("    %-15s %d", r[0], r[1])

    # Check 7: Spot-check a known service
    spot = conn.execute("""
        SELECT s.service_name, r.vehicle, r.price_thb
        FROM services s JOIN rates r ON r.service_id = s.id
        WHERE s.company_code='GOOD_DAY' AND s.service_type='Transfer'
          AND s.destination='Phuket'
        ORDER BY s.id LIMIT 3
    """).fetchall()
    if spot:
        log.info("  Sample Phuket transfer rates:")
        for r in spot:
            log.info("    %-50s  %-15s  %d THB", r[0], r[1], r[2])
    else:
        log.error("  ✗ Spot-check failed: no Phuket transfer rates found")
        passed = False

    return passed


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "=" * 60)
    print("  Phase 3 — GOOD_DAY.xlsx Parser")
    print("=" * 60)

    # ── Pre-flight ────────────────────────────────────────────────────────────
    print("\n[STEP 1] Pre-flight checks ...")

    if not XL_PATH.exists():
        log.error("GOOD_DAY.xlsx not found at: %s", XL_PATH)
        print(f"\n  ERROR: GOOD_DAY.xlsx not found in {HERE}")
        sys.exit(1)
    log.info("Found GOOD_DAY.xlsx at: %s", XL_PATH)

    if not DB_PATH.exists():
        log.error("quotation.db not found at: %s", DB_PATH)
        print("\n  ERROR: quotation.db not found. Run validate_schema.py first.")
        sys.exit(1)
    log.info("Found quotation.db at: %s", DB_PATH)

    conn = get_connection()

    existing = conn.execute(
        "SELECT COUNT(*) FROM services WHERE company_code='GOOD_DAY'"
    ).fetchone()[0]
    if existing > 0:
        print(f"\n  WARNING: {existing} existing GOOD_DAY rows found.")
        print("  To avoid duplicates, delete them first:")
        print("    DELETE FROM services WHERE company_code='GOOD_DAY';")
        answer = input("  Continue anyway? (yes/no): ").strip().lower()
        if answer != "yes":
            print("  Aborted.")
            conn.close()
            sys.exit(0)

    # ── Parse all sheets ──────────────────────────────────────────────────────
    print("\n[STEP 2] Opening Excel file ...")
    try:
        xl = pd.ExcelFile(XL_PATH)
        log.info("Sheets found: %s", xl.sheet_names)
    except Exception as exc:
        log.error("Failed to open GOOD_DAY.xlsx: %s", exc)
        sys.exit(1)

    print("\n[STEP 3] Parsing all 9 data sheets ...")

    grand = {"services_added": 0, "rates_added": 0, "warnings": []}

    parsers = [
        parse_transportation_arrival,
        parse_pickup_dropoff,
        parse_ferry,
        parse_tour_phuket,
        parse_samui,
        parse_krabi_tours,
        parse_combo_rates,
        parse_krabi_phuket_combo,
        parse_disposal,
    ]

    for parser_fn in parsers:
        try:
            stats = parser_fn(conn)
            grand["services_added"] += stats["services_added"]
            grand["rates_added"]    += stats["rates_added"]
            grand["warnings"].extend(stats.get("warnings", []))
        except Exception as exc:
            log.exception("Parser %s raised an exception: %s", parser_fn.__name__, exc)
            conn.rollback()
            conn.close()
            print(f"\n  FATAL: {parser_fn.__name__} crashed: {exc}")
            sys.exit(1)

    conn.commit()
    log.info("All sheets committed to database")

    # ── Validation ────────────────────────────────────────────────────────────
    print("\n[STEP 4] Validating inserted data ...")
    validation_passed = validate_inserted_data(conn)
    conn.close()

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    print(f"  Services inserted  : {grand['services_added']}")
    print(f"  Rate rows inserted : {grand['rates_added']}")
    print(f"  Warnings           : {len(grand['warnings'])}")

    if grand["warnings"]:
        print("\n  Warnings:")
        for w in grand["warnings"]:
            print(f"    ⚠  {w}")

    print()
    if validation_passed and len(grand["warnings"]) == 0:
        print("  ✓  Phase 3 complete. No warnings, all checks passed.")
        print("  Next step: build the search API (Phase 5)")
    elif validation_passed:
        print("  ✓  Phase 3 complete with warnings (see above).")
    else:
        print("  ✗  Phase 3 FAILED validation. See errors above.")
        sys.exit(1)
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
