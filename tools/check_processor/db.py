"""SQLite persistence for check_processor.

- init_db: opens/creates checks.db in the watch folder, ensures schema exists
- insert_check: INSERT OR IGNORE so reprocessing a file is always safe
- get_all_checks: all rows ordered by check_date (for reporting)
"""

import sqlite3
from pathlib import Path


_CREATE = """
CREATE TABLE IF NOT EXISTS checks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    -- From filename (parsed, never re-extracted)
    file_name       TEXT NOT NULL UNIQUE,
    check_date      TEXT,
    check_number    TEXT,
    coverage        TEXT,
    amount          TEXT,
    -- From Claude Vision
    payees          TEXT,
    client          TEXT,
    mortgage_co     TEXT,
    vendor          TEXT,
    insured_name    TEXT,
    claim_number    TEXT,
    policy_number   TEXT,
    loss_date       TEXT,
    loss_address    TEXT,
    bank            TEXT,
    memo            TEXT,
    -- Housekeeping
    processed_at    TEXT NOT NULL
);
"""


def init_db(watch_folder: str) -> sqlite3.Connection:
    """Open (or create) checks.db in watch_folder and ensure the schema exists."""
    db_path = Path(watch_folder) / "checks.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_CREATE)
    conn.commit()
    return conn


def insert_check(conn: sqlite3.Connection, record: dict) -> None:
    """Insert a check record. Silently skips if file_name already exists."""
    conn.execute(
        """
        INSERT OR IGNORE INTO checks (
            file_name, check_date, check_number, coverage, amount,
            payees, client, mortgage_co, vendor,
            insured_name, claim_number, policy_number,
            loss_date, loss_address, bank, memo, processed_at
        ) VALUES (
            :file_name, :check_date, :check_number, :coverage, :amount,
            :payees, :client, :mortgage_co, :vendor,
            :insured_name, :claim_number, :policy_number,
            :loss_date, :loss_address, :bank, :memo, :processed_at
        )
        """,
        record,
    )
    conn.commit()


def get_all_checks(conn: sqlite3.Connection) -> list:
    """Return all rows ordered by check_date."""
    cursor = conn.execute("SELECT * FROM checks ORDER BY check_date")
    return [dict(row) for row in cursor.fetchall()]
