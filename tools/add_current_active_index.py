#!/usr/bin/env python3
"""
add_current_active_index.py

Small helper to add the `current_active_index` column to the `game` table in the
project's SQLite DB (`darts.db`). It first checks PRAGMA table_info to avoid
re-adding the column and will print a concise status.

Usage:
  python darts4you/tools/add_current_active_index.py [--db /path/to/darts.db]

If --db is omitted the script will look for `darts.db` in the project root
(directory two levels up from this script, which matches the repo layout).
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from typing import Optional


def has_column(conn: sqlite3.Connection, table: str, col: str) -> bool:
    """
    Return True if the given column exists on the given table according to
    PRAGMA table_info('<table>').
    """
    cur = conn.execute(f"PRAGMA table_info('{table}')")
    rows = cur.fetchall()
    cols = [r[1] for r in rows]
    return col in cols


def add_column(conn: sqlite3.Connection, table: str, column_sql: str) -> None:
    """
    Run a simple ALTER TABLE ... ADD COLUMN statement.

    The `column_sql` should be the column definition portion, e.g.:
      "current_active_index INTEGER DEFAULT 0"
    """
    sql = f"ALTER TABLE {table} ADD COLUMN {column_sql}"
    conn.execute(sql)
    conn.commit()


def default_db_path() -> str:
    """
    Determine a sensible default path for darts.db relative to this script.

    The repository layout places this script at:
      darts4you/tools/add_current_active_index.py

    We look for darts4you/darts.db (one level up from the project root when
    executed inside the repo) so go up two directories from this script.
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    # Move up two levels to the project root (script_dir -> darts4you/tools -> project root)
    candidate = os.path.normpath(os.path.join(script_dir, "..", "darts.db"))
    return candidate


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Add current_active_index column to darts.db if missing")
    p.add_argument("--db", help="Path to darts.db (SQLite). If omitted a default in the project is used.")
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    db_path = args.db or default_db_path()

    if not os.path.exists(db_path):
        print(f"ERROR: database file not found at: {db_path}", file=sys.stderr)
        return 2

    try:
        conn = sqlite3.connect(db_path)
    except Exception as e:
        print(f"ERROR: failed to open database '{db_path}': {e}", file=sys.stderr)
        return 3

    try:
        if has_column(conn, "game", "current_active_index"):
            print("No action needed: column 'current_active_index' already exists on 'game' table.")
            return 0

        print("Adding column 'current_active_index' to 'game' table...")

        try:
            add_column(conn, "game", "current_active_index INTEGER DEFAULT 0")
            # Verify success
            if has_column(conn, "game", "current_active_index"):
                print(
                    "Success: added 'current_active_index' to 'game' table. Existing rows will have value 0 by default."
                )
                return 0
            else:
                print("ERROR: ALTER TABLE executed but column not visible afterwards. (unexpected)", file=sys.stderr)
                return 4
        except sqlite3.OperationalError as e:
            print("ERROR: failed to run ALTER TABLE:", e, file=sys.stderr)
            print(
                "Common causes: database file is locked, the file is read-only, or it is not a valid SQLite database.",
                file=sys.stderr,
            )
            return 5
        except Exception as e:
            print("ERROR: unexpected failure while attempting to add column:", e, file=sys.stderr)
            return 6
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
