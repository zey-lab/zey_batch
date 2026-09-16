"""Test isolation for the Postgres-backed data store.

Every test used to get a throwaway SQLite file for free via tempfile. Now
that ZeyDataStore always talks to the same Supabase Postgres instance, tests
instead run inside one dedicated schema (created once per session, dropped
at the end) and every table in it is truncated before each test function so
tests stay as isolated from each other -- and from the real `public`
production data -- as the old per-file SQLite databases were.
"""

from __future__ import annotations

import os
import uuid

import psycopg
import pytest

_TABLES = (
    "customers", "services", "employees", "transactions",
    "sms_history", "email_history", "campaigns", "sync_log", "webhook_events",
)


def _admin_connection() -> psycopg.Connection:
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)


@pytest.fixture(scope="session", autouse=True)
def _pg_test_schema():
    schema = f"pytest_{uuid.uuid4().hex[:12]}"
    conn = _admin_connection()
    try:
        conn.execute(f'CREATE SCHEMA "{schema}"')
        for table in _TABLES:
            conn.execute(f'CREATE TABLE "{schema}".{table} (LIKE public.{table} INCLUDING ALL)')
        os.environ["PG_SCHEMA"] = schema
        yield schema
    finally:
        conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
        conn.close()
        os.environ.pop("PG_SCHEMA", None)


@pytest.fixture(autouse=True)
def _pg_truncate_between_tests(_pg_test_schema):
    schema = _pg_test_schema
    conn = _admin_connection()
    try:
        tables = ", ".join(f'"{schema}".{table}' for table in _TABLES)
        conn.execute(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")
        yield
    finally:
        conn.close()
