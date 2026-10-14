from __future__ import annotations

import re
import unittest
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


class TestMigrations(unittest.TestCase):
    def test_every_public_table_enables_row_level_security(self) -> None:
        # Supabase exposes public tables through PostgREST; a table without
        # RLS is readable and writable with the anon key (see 006).
        sql = "\n".join(p.read_text().lower() for p in sorted(MIGRATIONS.glob("*.sql")))
        tables = set(re.findall(r"create table (?:if not exists )?public\.(\w+)", sql))
        enabled = set(re.findall(r"alter table public\.(\w+) enable row level security", sql))
        loop = re.search(r"foreach table_name in array array\[(.*?)\]", sql, re.S)
        if loop:
            enabled |= set(re.findall(r"'(\w+)'", loop.group(1)))
        self.assertTrue(tables)
        self.assertEqual(sorted(tables - enabled), [])


if __name__ == "__main__":
    unittest.main()
