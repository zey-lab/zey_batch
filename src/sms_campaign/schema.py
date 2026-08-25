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
    acquisition TEXT,
    bank_name_number TEXT,
    cdn_url TEXT,
    country_id TEXT,
    custom_fields_groups TEXT,
    day_phone TEXT,
    email_failed_reason TEXT,
    email_format TEXT,
    general_tag TEXT,
    is_valid_email INTEGER,
    is_valid_text INTEGER,
    night_phone TEXT,
    no_of_booking INTEGER,
    no_of_class_booked INTEGER,
    no_of_class_check_ins INTEGER,
    no_show_cancel INTEGER,
    photo TEXT,
    service_providers TEXT,
    street_address TEXT,
    street_no TEXT,
    text_failed_reason TEXT,
    total_amount_paid REAL,
    total_points_accumulated REAL,
    ucc_no TEXT,
    ucc_type TEXT,
    enc_user_id TEXT,
    raw_json        TEXT,
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

-- Transactions: sales/payment history from Vagaro Reports > Sales > Transaction List
CREATE TABLE IF NOT EXISTS transactions (
    transaction_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    vagaro_transaction_id   TEXT UNIQUE,
    customer_id             INTEGER REFERENCES customers(customer_id),
    customer_name           TEXT,
    employee_name           TEXT,
    transaction_date        TEXT,
    transaction_type        TEXT,  -- Sale, Refund, etc.
    payment_method          TEXT,
    subtotal                REAL,
    tax                     REAL,
    tip                     REAL,
    discount                REAL,
    total_amount            REAL,
    status                  TEXT,
    notes                   TEXT,
    raw_json                TEXT,
    created_at              TEXT DEFAULT (datetime('now'))
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
CREATE INDEX IF NOT EXISTS idx_transactions_customer ON transactions(customer_id);
CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(transaction_date);
CREATE INDEX IF NOT EXISTS idx_sms_history_customer ON sms_history(customer_id);
CREATE INDEX IF NOT EXISTS idx_sms_history_sent ON sms_history(sent_at);
CREATE INDEX IF NOT EXISTS idx_employees_vagaro ON employees(vagaro_emp_id);
"""


def _parse_columns(table: str) -> list[tuple[str, str]]:
    """Extract (column_name, column_type) pairs for a table from SCHEMA_SQL."""
    marker = f"CREATE TABLE IF NOT EXISTS {table} ("
    start = SCHEMA_SQL.index(marker) + len(marker)
    end = SCHEMA_SQL.index(");", start)
    body = SCHEMA_SQL[start:end]
    columns = []
    for line in body.splitlines():
        line = line.strip().rstrip(",")
        if not line or line.startswith("--"):
            continue
        if line.upper().startswith(("PRIMARY KEY", "FOREIGN KEY", "UNIQUE(", "CHECK(")):
            continue
        parts = line.split()
        name, col_type = parts[0], (parts[1] if len(parts) > 1 else "TEXT")
        columns.append((name, col_type))
    return columns


def migrate_database(db_path: Path) -> None:
    """Idempotently add any columns present in SCHEMA_SQL but missing from an existing DB."""
    conn = sqlite3.connect(str(db_path))
    try:
        for table in ("customers", "services", "employees", "campaigns", "transactions"):
            existing = {
                row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            for name, col_type in _parse_columns(table):
                if name not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {col_type}")
        conn.commit()
    finally:
        conn.close()


def init_database(db_path: Path) -> None:
    """Create or migrate the SQLite database schema."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()
    migrate_database(db_path)
