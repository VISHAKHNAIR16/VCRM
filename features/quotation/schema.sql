-- =============================================================================
-- schema.sql
-- Quotation Tool — SQLite Database Schema
-- Version: 1.0.0
-- =============================================================================
--
-- HOW TO USE:
--   This file is executed automatically by build_db.py
--   You can also run it manually:
--       sqlite3 quotation.db < schema.sql
--
-- TABLES:
--   companies       → Who provides the service (DIVINE / GOOD_DAY_VACATION)
--   services        → One row per service (e.g. "Airport to Bangkok Hotel")
--   rates           → Prices for a service (by vehicle, pax, adult/child)
--   ferry_schedules → Departure times & per-person prices for ferry tickets
--
-- DATA SOURCES:
--   BKK_PTT.xlsx    → source = 'DIVINE'  | destination = Bangkok / Pattaya / etc.
--   GOOD_DAY.xlsx   → source = 'GOOD_DAY' | destination = Phuket / Krabi / Samui
--
-- RATE TYPES:
--   Private         → Whole vehicle booked (price is per vehicle)
--   SIC             → Shared / join trip (price is per person)
--
-- PAX CATEGORIES:
--   PerVehicle      → Price for the whole vehicle regardless of pax count
--   Adult           → Price per adult (SIC tours / ferry)
--   Child           → Price per child (SIC tours / ferry, age 4-11)
--
-- =============================================================================

PRAGMA foreign_keys    = ON;
PRAGMA journal_mode    = WAL;    -- Better concurrent read performance
PRAGMA synchronous     = NORMAL; -- Safe + faster than FULL for our use case

-- =============================================================================
-- TABLE: companies
-- One row per travel company whose rates are stored in this DB.
-- =============================================================================
CREATE TABLE IF NOT EXISTS companies (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT    NOT NULL UNIQUE,  -- e.g. 'DIVINE', 'GOOD_DAY'
    name        TEXT    NOT NULL,         -- Full display name
    currency    TEXT    NOT NULL DEFAULT 'THB',
    includes_vat INTEGER NOT NULL DEFAULT 0  -- 1 = rates already include VAT
);

-- =============================================================================
-- TABLE: services
-- One row per bookable service.
-- =============================================================================
CREATE TABLE IF NOT EXISTS services (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Which company provides this service
    company_code TEXT    NOT NULL REFERENCES companies(code),

    -- Where does this service operate (used for filtering)
    destination  TEXT    NOT NULL,   -- e.g. 'Bangkok', 'Phuket', 'Krabi', 'Samui'

    -- High-level category (used for grouped search results)
    service_type TEXT    NOT NULL,   -- Transfer | Tour | Enroute | Disposal | Ferry | Combo

    -- The name shown to the user in search results
    service_name TEXT    NOT NULL,

    -- Extra metadata (optional, shown in cart detail view)
    duration     TEXT,               -- e.g. '4 Hours', 'Full Day'
    notes        TEXT,               -- Remarks / conditions from the Excel

    -- Audit
    created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- =============================================================================
-- TABLE: rates
-- One row per price variant for a service.
-- A single service can have many rate rows (different vehicles, adult vs child).
-- =============================================================================
CREATE TABLE IF NOT EXISTS rates (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    service_id   INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,

    -- 'Private' = whole vehicle | 'SIC' = shared / per person
    rate_type    TEXT    NOT NULL CHECK(rate_type IN ('Private', 'SIC')),

    -- Vehicle type — NULL for SIC/per-person rates
    -- BKK_PTT uses: CAR, SUV, VAN
    -- GOOD_DAY uses: Sedan/SUV, D4D Van, Camry, Hyundai, VIP Van, Alphard, Car/Fortuner
    vehicle      TEXT,

    -- Pax range label — NULL means "applies to all"
    -- e.g. '1-3 pax', '4-8 pax'
    pax_range    TEXT,

    -- Who this price applies to
    -- PerVehicle: whole car (Private rates)
    -- Adult / Child: per person (SIC or ferry)
    pax_category TEXT    NOT NULL CHECK(pax_category IN ('PerVehicle', 'Adult', 'Child')),

    -- The actual price in Thai Baht (always an integer, no decimals)
    price_thb    INTEGER NOT NULL CHECK(price_thb > 0)
);

-- =============================================================================
-- TABLE: ferry_schedules
-- Only used for GOOD_DAY Ferry Ticket sheet.
-- Stores departure/arrival times and pier names alongside the per-person price.
-- =============================================================================
CREATE TABLE IF NOT EXISTS ferry_schedules (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    service_id      INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,

    -- Direction
    route           TEXT    NOT NULL,  -- e.g. 'Phuket → Phi Phi'

    -- Timetable
    depart_time     TEXT,              -- e.g. '08:30'
    depart_pier     TEXT,              -- e.g. 'Rassada'
    arrive_time     TEXT,              -- e.g. '10:30'
    arrive_pier     TEXT,              -- e.g. 'Tonsai'

    -- Pricing
    price_adult     INTEGER CHECK(price_adult IS NULL OR price_adult > 0),
    price_child     INTEGER CHECK(price_child IS NULL OR price_child > 0)
);

-- =============================================================================
-- INDEXES — speeds up the search queries the frontend will run
-- =============================================================================

-- Full-text-style search on service name (LIKE '%query%')
CREATE INDEX IF NOT EXISTS idx_services_name
    ON services(service_name COLLATE NOCASE);

-- Filter by destination (Bangkok / Phuket / etc.)
CREATE INDEX IF NOT EXISTS idx_services_destination
    ON services(destination);

-- Filter by service type
CREATE INDEX IF NOT EXISTS idx_services_type
    ON services(service_type);

-- Filter by company
CREATE INDEX IF NOT EXISTS idx_services_company
    ON services(company_code);

-- Join: rates → services
CREATE INDEX IF NOT EXISTS idx_rates_service
    ON rates(service_id);

-- Join: ferry_schedules → services
CREATE INDEX IF NOT EXISTS idx_ferry_service
    ON ferry_schedules(service_id);

-- =============================================================================
-- SEED DATA: companies (insert once, ignore if already there)
-- =============================================================================
INSERT OR IGNORE INTO companies (code, name, currency, includes_vat)
VALUES
    ('DIVINE',    'Divine Travel (BKK / PTT)',     'THB', 0),
    ('GOOD_DAY',  'Good Day Vacation (Phuket/Krabi/Samui)', 'THB', 0);

-- =============================================================================
-- END OF SCHEMA
-- =============================================================================
