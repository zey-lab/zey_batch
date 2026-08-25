"""SQLite schema for the Zey Brow customer data ownership system."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_SQL = """
-- Customers: master customer record synced from Vagaro
CREATE TABLE IF NOT EXISTS customers (
    customer_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    vagaro_user_id  TEXT UNIQUE,
    mobile          TEXT NOT NULL,
    first_name      TEXT,
    last_name       TEXT,
    email           TEXT,
    birthdate       TEXT,
    gender          TEXT,
    address         TEXT,
    city            TEXT,
    state           TEXT,
    zip             TEXT,
    apt_suite       TEXT,
    customer_since  TEXT,
    last_visit      TEXT,
    membership      TEXT,
    referred_by     TEXT,
    online_booking  TEXT,
    tags            TEXT,
    communication_preference TEXT DEFAULT 'sms',  -- sms, email, both, none
    sms_opt_out     INTEGER DEFAULT 0,
    opt_out_date    TEXT,
    email_opt_out   INTEGER DEFAULT 0,
    active          INTEGER DEFAULT 1,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

-- Services: appointment/service history from Vagaro
CREATE TABLE IF NOT EXISTS services (
    service_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id     INTEGER REFERENCES customers(customer_id),
    employee_name   TEXT,
    service_name    TEXT,
    service_date    TEXT,
    duration_min    INTEGER,
    amount_paid     REAL,
    notes           TEXT,
    vagaro_appt_id  TEXT UNIQUE,
    created_at      TEXT DEFAULT (datetime('now'))
);

-- Employees: staff roster from Vagaro
CREATE TABLE IF NOT EXISTS employees (
    employee_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    vagaro_emp_id   TEXT UNIQUE,
    name            TEXT NOT NULL,
    role            TEXT,
    phone           TEXT,
    email           TEXT,
    active          INTEGER DEFAULT 1,
    schedule_json   TEXT,  -- weekly schedule as JSON
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

-- SMS History: every SMS sent through the campaign system
CREATE TABLE IF NOT EXISTS sms_history (
    sms_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id     INTEGER REFERENCES customers(customer_id),
    campaign_type   TEXT,
    message_text    TEXT,
    sent_at         TEXT DEFAULT (datetime('now')),
    status          TEXT,  -- sent, failed, delivered, bounced
    twilio_sid      TEXT,
    error_message   TEXT,
    campaign_row    INTEGER,  -- which campaign row in campaigns table
    created_at      TEXT DEFAULT (datetime('now'))
);

-- Email History: every email sent
CREATE TABLE IF NOT EXISTS email_history (
    email_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id     INTEGER REFERENCES customers(customer_id),
    campaign_type   TEXT,
    subject         TEXT,
    body            TEXT,
    sent_at         TEXT DEFAULT (datetime('now')),
    status          TEXT,  -- sent, failed, delivered, opened
    error_message   TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

-- Campaigns: campaign definitions (replaces campaigns.xlsx)
CREATE TABLE IF NOT EXISTS campaigns (
    campaign_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    text_prompt     TEXT NOT NULL,
    character_limit INTEGER DEFAULT 160,
    campaign_type   TEXT DEFAULT 'Campaign',  -- Campaign, Reminder, Birthday, Anniversary, Announce, Review
    filter_last_visit_days INTEGER,
    filter_last_sms_days   INTEGER,
    rank            INTEGER DEFAULT 999,
    process_date    TEXT,
    process_status  TEXT,
    active          INTEGER DEFAULT 1,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

-- Daily Snapshots: track what changed each sync run
CREATE TABLE IF NOT EXISTS sync_log (
    log_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sync_date       TEXT DEFAULT (datetime('now')),
    source          TEXT,  -- vagaro_customer, vagaro_service, vagaro_employee
    records_fetched INTEGER,
    records_inserted INTEGER,
    records_updated INTEGER,
    records_deactivated INTEGER,
    errors          TEXT,
    duration_sec    REAL
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_customers_mobile ON customers(mobile);
CREATE INDEX IF NOT EXISTS idx_customers_vagaro ON customers(vagaro_user_id);
CREATE INDEX IF NOT EXISTS idx_services_customer ON services(customer_id);
CREATE INDEX IF NOT EXISTS idx_services_date ON services(service_date);
CREATE INDEX IF NOT EXISTS idx_sms_history_customer ON sms_history(customer_id);
CREATE INDEX IF NOT EXISTS idx_sms_history_sent ON sms_history(sent_at);
CREATE INDEX IF NOT EXISTS idx_employees_vagaro ON employees(vagaro_emp_id);
"""


def init_database(db_path: Path) -> None:
    """Create or migrate the SQLite database schema."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()
