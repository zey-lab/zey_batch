"""Postgres (Supabase) connection management.

Supabase is the only data store zey_batch talks to -- there is no local
SQLite file anymore. ``PG_SCHEMA`` lets the test suite point every
connection at an isolated schema instead of ``public`` without any
individual test needing to know about it.
"""

from __future__ import annotations

import os
import re

import psycopg
from psycopg.rows import dict_row

_SCHEMA_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def get_connection() -> psycopg.Connection:
    """Open a new connection with dict-row results and the right search_path.

    A fresh connection per call mirrors the previous sqlite3.connect()-per-call
    pattern throughout the codebase -- callers still open/commit/close around
    each unit of work.
    """
    dsn = os.environ["DATABASE_URL"]
    conn = psycopg.connect(dsn, row_factory=dict_row, autocommit=False)
    schema = os.getenv("PG_SCHEMA", "public")
    if not _SCHEMA_RE.match(schema):
        raise ValueError(f"invalid PG_SCHEMA: {schema!r}")
    conn.execute(f'SET search_path TO "{schema}"')
    return conn
