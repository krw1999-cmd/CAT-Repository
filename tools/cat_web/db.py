from __future__ import annotations

"""Database layer for CAT Web."""

import sqlite3
from datetime import datetime, timezone
from flask import g

import config

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claims (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    job_number    TEXT,
    claim_number  TEXT,
    insured_name  TEXT,
    carrier       TEXT,
    contract_pct  REAL DEFAULT 0,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS policy_limits (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id       INTEGER NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    coverage_type  TEXT NOT NULL,
    base_limit     REAL DEFAULT 0,
    extended_limit REAL DEFAULT 0,
    paid           REAL DEFAULT 0,
    remaining      REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS transactions (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id              INTEGER NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    sequence_number       INTEGER,
    date                  TEXT,
    amount                REAL DEFAULT 0,
    type                  TEXT,
    fee_pct               REAL,          -- NULL = inherit claim's contract_pct
    fee_owed              REAL DEFAULT 0,
    balance               REAL DEFAULT 0,
    deferred              REAL DEFAULT 0,
    recouped              REAL DEFAULT 0,
    fee_collected         REAL DEFAULT 0,
    reimbursed            REAL DEFAULT 0,
    total_collected       REAL DEFAULT 0,
    unpaid_payee_expense  REAL DEFAULT 0,
    outstanding_expense   REAL DEFAULT 0,
    ott                   REAL,
    notes                 TEXT,
    check_id              INTEGER REFERENCES checks(id),
    -- check detail fields
    check_number          TEXT,
    received_date         TEXT,
    payer                 TEXT,
    payees_text           TEXT,
    endorsed              INTEGER DEFAULT 0,
    void                  INTEGER DEFAULT 0,
    linked_escrow_id      INTEGER REFERENCES transactions(id),
    inv_for               TEXT DEFAULT 'insured',
    inv_vendor_id         INTEGER REFERENCES vendors(id),
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS expenses (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id           INTEGER NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    invoice_date       TEXT,
    payee_name         TEXT,
    invoice_amount     REAL DEFAULT 0,
    responsible_party  TEXT,
    unpaid_to_payee    REAL DEFAULT 0,
    client_outstanding REAL DEFAULT 0,
    wp_outstanding     REAL DEFAULT 0,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS checks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id      INTEGER REFERENCES claims(id),
    file_name     TEXT NOT NULL UNIQUE,
    check_date    TEXT,
    check_number  TEXT,
    coverage      TEXT,
    amount        TEXT,
    payees        TEXT,
    client        TEXT,
    mortgage_co   TEXT,
    vendor        TEXT,
    insured_name  TEXT,
    claim_number  TEXT,
    policy_number TEXT,
    loss_date     TEXT,
    loss_address  TEXT,
    bank          TEXT,
    memo          TEXT,
    processed_at  TEXT NOT NULL
);

-- Claim-level default split percentages (set once, applied to each new transaction)
CREATE TABLE IF NOT EXISTS assignees (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id     INTEGER NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    role         TEXT NOT NULL,   -- firm, sales, adjuster, referrer, other
    name         TEXT NOT NULL,
    split_pct    REAL DEFAULT 0,  -- e.g. 40.0 = 40%
    sort_order   INTEGER DEFAULT 0,
    recipient_id INTEGER REFERENCES fee_recipients(id)
);

-- Per-transaction payroll splits (cloned from assignees on creation, fully editable)
CREATE TABLE IF NOT EXISTS transaction_splits (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    role           TEXT NOT NULL,
    name           TEXT NOT NULL,
    split_pct      REAL DEFAULT 0,
    recipient_id   INTEGER REFERENCES fee_recipients(id)
);

-- Per-transaction coverage allocation (how much of this check goes to each coverage)
CREATE TABLE IF NOT EXISTS transaction_coverage (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    coverage_type  TEXT NOT NULL,
    amount         REAL DEFAULT 0
);

-- Global vendor list
CREATE TABLE IF NOT EXISTS vendors (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    phone      TEXT,
    email      TEXT,
    notes      TEXT,
    created_at TEXT NOT NULL
);

-- Global Waypoint payroll people (fee recipients)
CREATE TABLE IF NOT EXISTS fee_recipients (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL UNIQUE,
    default_role TEXT,
    created_at   TEXT NOT NULL
);

-- Disbursements: where the money goes (per-transaction)
CREATE TABLE IF NOT EXISTS disbursements (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id   INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    sort_order       INTEGER DEFAULT 0,
    recipient_type   TEXT NOT NULL,   -- 'insured' | 'vendor'
    vendor_id        INTEGER REFERENCES vendors(id),
    recipient_name   TEXT,
    amount           REAL NOT NULL DEFAULT 0,
    fee_applies      INTEGER NOT NULL DEFAULT 1,
    fee_pct          REAL,            -- NULL = inherit from claim contract_pct
    fee_owed         REAL DEFAULT 0,
    fee_collected    REAL DEFAULT 0,
    fee_deferred     REAL DEFAULT 0,
    fee_recouped     REAL DEFAULT 0,
    use_check_splits INTEGER NOT NULL DEFAULT 1,
    notes            TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

-- Per-disbursement payroll splits (only used when use_check_splits=0)
CREATE TABLE IF NOT EXISTS disbursement_splits (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    disbursement_id INTEGER NOT NULL REFERENCES disbursements(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,
    name            TEXT,
    split_pct       REAL DEFAULT 0,
    recipient_id    INTEGER REFERENCES fee_recipients(id)
);

-- Recouped fee custom split (only used when transaction_recouped_splits has rows)
CREATE TABLE IF NOT EXISTS transaction_recouped_splits (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    role           TEXT NOT NULL,
    name           TEXT NOT NULL,
    split_pct      REAL DEFAULT 0,
    recipient_id   INTEGER REFERENCES fee_recipients(id)
);

-- Invoice payment log (per Fee Invoice transaction)
CREATE TABLE IF NOT EXISTS invoice_payments (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    date           TEXT,
    amount         REAL NOT NULL DEFAULT 0,
    method         TEXT,        -- 'qb' | 'check' | 'recoup'
    reference      TEXT,
    linked_tx_id   INTEGER REFERENCES transactions(id),
    notes          TEXT,
    created_at     TEXT NOT NULL
);

-- Many-to-many: Fee Invoice → deferred-fee transactions
CREATE TABLE IF NOT EXISTS invoice_deferred_links (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_tx_id  INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    deferred_tx_id INTEGER NOT NULL REFERENCES transactions(id),
    created_at     TEXT NOT NULL
);

-- Payments from WP or client to the external payee (per expense)
CREATE TABLE IF NOT EXISTS expense_payments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    expense_id  INTEGER NOT NULL REFERENCES expenses(id) ON DELETE CASCADE,
    date        TEXT,
    paid_by     TEXT NOT NULL,   -- 'wp' | 'client'
    amount      REAL NOT NULL DEFAULT 0,
    method      TEXT,            -- 'check' | 'ach' | 'cc' | 'other'
    reference   TEXT,
    notes       TEXT,
    created_at  TEXT NOT NULL
);

-- Client paying WP back after WP fronted a client expense
CREATE TABLE IF NOT EXISTS expense_reimbursements (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    expense_id     INTEGER NOT NULL REFERENCES expenses(id) ON DELETE CASCADE,
    date           TEXT,
    amount         REAL NOT NULL DEFAULT 0,
    method         TEXT,
    reference      TEXT,
    linked_tx_id   INTEGER REFERENCES transactions(id),
    notes          TEXT,
    created_at     TEXT NOT NULL
);

-- WP payroll splits per expense (clone of claim assignees, fully editable)
CREATE TABLE IF NOT EXISTS expense_splits (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    expense_id  INTEGER NOT NULL REFERENCES expenses(id) ON DELETE CASCADE,
    role        TEXT NOT NULL,
    name        TEXT NOT NULL,
    split_pct   REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS coverage_type_catalog (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);
"""

# Columns added after initial deploy — safe to run on any existing DB
_MIGRATIONS = [
    ("transactions", "fee_pct",          "REAL"),
    ("transactions", "check_number",     "TEXT"),
    ("transactions", "received_date",    "TEXT"),
    ("transactions", "payer",            "TEXT"),
    ("transactions", "payees_text",      "TEXT"),
    ("transactions", "endorsed",         "INTEGER DEFAULT 0"),
    ("transactions", "void",             "INTEGER DEFAULT 0"),
    ("transactions", "linked_escrow_id", "INTEGER"),
    ("assignees",    "recipient_id",     "INTEGER"),
    ("transaction_splits", "recipient_id", "INTEGER"),
    ("disbursements", "client_received",  "INTEGER NOT NULL DEFAULT 0"),
    ("transactions",  "invoice_number",    "TEXT"),
    ("transactions",  "inv_client_paid",   "INTEGER DEFAULT 0"),
    ("transactions",  "inv_fee_received",  "INTEGER DEFAULT 0"),
    ("transactions",  "inv_for",           "TEXT DEFAULT 'insured'"),
    ("transactions",  "inv_vendor_id",      "INTEGER"),
    ("claims",        "proc_method",                  "TEXT"),
    ("transactions",  "proc_method",                  "TEXT"),
    ("transactions",  "proc_fee_invoice_id",           "INTEGER"),
    ("claims",        "proc_method_carrier",           "TEXT"),
    ("claims",        "proc_method_draw",              "TEXT"),
    ("claims",        "proc_method_escrow",            "TEXT"),
    ("claims",        "proc_method_escrow_endorsed",   "TEXT"),
    ("disbursement_splits", "recipient_id",               "INTEGER"),
    ("expenses",      "reason",                        "TEXT"),
    ("expenses",      "invoice_number",                "TEXT"),
    ("expenses",      "client_amount",                 "REAL DEFAULT 0"),
    ("expenses",      "wp_amount",                     "REAL DEFAULT 0"),
    ("expenses",      "vendor_id",                     "INTEGER"),
    ("expense_splits","recipient_id",                  "INTEGER"),
]

ASSIGNEE_ROLES = ["firm", "sales", "adjuster", "referrer", "other"]

TRANSACTION_TYPES = [
    "Escrow", "Escrow Endorsed", "Carrier", "Draw", "Fee Invoice",
]

COVERAGE_TYPES = ["COV A", "COV B", "COV C", "COV D", "OTHER", "OTHER2"]

# Types that represent released / actual money
RELEASED_TYPES = {"carrier", "draw", "escrow endorsed"}
# Type that represents planned / hypothetical money
ESCROW_TYPE = "escrow"

# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(config.DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(e=None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    conn = sqlite3.connect(config.DB_PATH)
    conn.executescript(_SCHEMA)
    _run_migrations(conn)
    # Seed coverage type catalog with defaults if empty
    count = conn.execute("SELECT COUNT(*) FROM coverage_type_catalog").fetchone()[0]
    if count == 0:
        conn.executemany(
            "INSERT OR IGNORE INTO coverage_type_catalog (name) VALUES (?)",
            [(t,) for t in COVERAGE_TYPES],
        )
    conn.commit()
    conn.close()


def get_coverage_types() -> list[str]:
    """Return all coverage types from the catalog, sorted alphabetically."""
    rows = get_db().execute(
        "SELECT name FROM coverage_type_catalog ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows]


def ensure_coverage_type(name: str) -> None:
    """Add a coverage type to the catalog if it doesn't already exist."""
    name = (name or "").strip()
    if not name:
        return
    get_db().execute(
        "INSERT OR IGNORE INTO coverage_type_catalog (name) VALUES (?)", (name,)
    )
    get_db().commit()


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Add new columns to existing tables if they don't exist yet."""
    for table, col, typ in _MIGRATIONS:
        try:
            existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if col not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        except Exception:
            pass  # table may not exist yet; schema creation handles it


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _row(conn, query, params=()):
    return conn.execute(query, params).fetchone()

def _rows(conn, query, params=()):
    return [dict(r) for r in conn.execute(query, params).fetchall()]

def _f(v) -> float:
    try:
        return float(v) if v not in (None, "", "None") else 0.0
    except (TypeError, ValueError):
        return 0.0

def _fopt(v) -> float | None:
    try:
        return float(v) if v not in (None, "", "None") else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def get_user_by_id(user_id: int) -> dict | None:
    row = _row(get_db(), "SELECT * FROM users WHERE id = ?", (user_id,))
    return dict(row) if row else None

def get_user_by_username(username: str) -> dict | None:
    row = _row(get_db(), "SELECT * FROM users WHERE username = ?", (username,))
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Claims
# ---------------------------------------------------------------------------

def get_all_claims() -> list[dict]:
    sql = """
        SELECT c.*,
               COALESCE(fc.fee_collected_sum, 0) AS total_collected_sum,
               COALESCE(uinv.unpaid_invoice_count, 0) AS unpaid_invoice_count
        FROM claims c
        LEFT JOIN (
            SELECT t.claim_id,
                   SUM(
                       CASE
                       WHEN LOWER(TRIM(t.type)) IN ('carrier', 'draw', 'escrow endorsed') THEN
                           CASE WHEN d.has_d = 1
                                THEN COALESCE(d.disburse_fee_collected, 0)
                                ELSE COALESCE(t.fee_collected, 0)
                           END
                       WHEN LOWER(TRIM(t.type)) = 'fee invoice' THEN
                           COALESCE(t.total_collected, 0)
                       ELSE 0 END
                   ) AS fee_collected_sum
            FROM transactions t
            LEFT JOIN (
                SELECT transaction_id,
                       1 AS has_d,
                       SUM(fee_collected) AS disburse_fee_collected
                FROM disbursements
                GROUP BY transaction_id
            ) d ON d.transaction_id = t.id
            WHERE (t.void IS NULL OR t.void = 0)
            GROUP BY t.claim_id
        ) fc ON fc.claim_id = c.id
        LEFT JOIN (
            SELECT t2.claim_id, COUNT(*) AS unpaid_invoice_count
            FROM transactions t2
            WHERE LOWER(t2.type) = 'fee invoice'
              AND (t2.void IS NULL OR t2.void = 0)
              AND NOT (t2.inv_client_paid = 1 AND t2.inv_fee_received = 1)
            GROUP BY t2.claim_id
        ) uinv ON uinv.claim_id = c.id
        ORDER BY c.created_at DESC
    """
    return _rows(get_db(), sql)

def get_claim(claim_id: int) -> dict | None:
    row = _row(get_db(), "SELECT * FROM claims WHERE id = ?", (claim_id,))
    return dict(row) if row else None

def create_claim(data: dict) -> int:
    now = _now()
    cur = get_db().execute(
        """INSERT INTO claims (job_number, claim_number, insured_name, carrier,
                               contract_pct,
                               proc_method_carrier, proc_method_draw, proc_method_escrow,
                               created_at, updated_at)
           VALUES (:job_number, :claim_number, :insured_name, :carrier,
                   :contract_pct,
                   :proc_method_carrier, :proc_method_draw, :proc_method_escrow,
                   :now, :now)""",
        {
            **data,
            "proc_method_carrier": data.get("proc_method_carrier") or None,
            "proc_method_draw":    data.get("proc_method_draw") or None,
            "proc_method_escrow":  data.get("proc_method_escrow") or None,
            "now": now,
        },
    )
    get_db().commit()
    return cur.lastrowid

def update_claim(claim_id: int, data: dict) -> None:
    get_db().execute(
        """UPDATE claims SET job_number=:job_number, claim_number=:claim_number,
                             insured_name=:insured_name, carrier=:carrier,
                             contract_pct=:contract_pct,
                             proc_method_carrier=:proc_method_carrier,
                             proc_method_draw=:proc_method_draw,
                             proc_method_escrow=:proc_method_escrow,
                             updated_at=:now
           WHERE id = :id""",
        {
            **data,
            "proc_method_carrier": data.get("proc_method_carrier") or None,
            "proc_method_draw":    data.get("proc_method_draw") or None,
            "proc_method_escrow":  data.get("proc_method_escrow") or None,
            "now": _now(),
            "id": claim_id,
        },
    )
    get_db().commit()


# ---------------------------------------------------------------------------
# Policy Limits
# ---------------------------------------------------------------------------

def get_limits(claim_id: int) -> list[dict]:
    sql = """
        SELECT pl.id, pl.claim_id, pl.coverage_type, pl.base_limit, pl.extended_limit,
               COALESCE(_cov._computed_paid, 0) AS paid,
               (COALESCE(pl.base_limit, 0) + COALESCE(pl.extended_limit, 0)
                - COALESCE(_cov._computed_paid, 0)) AS remaining
        FROM policy_limits pl
        LEFT JOIN (
            SELECT tc.coverage_type, SUM(tc.amount) AS _computed_paid
            FROM transaction_coverage tc
            JOIN transactions t ON t.id = tc.transaction_id
            WHERE t.claim_id = ? AND (t.void IS NULL OR t.void = 0)
            GROUP BY tc.coverage_type
        ) _cov ON _cov.coverage_type = pl.coverage_type
        WHERE pl.claim_id = ?
        ORDER BY pl.id
    """
    return _rows(get_db(), sql, (claim_id, claim_id))

def get_limit(limit_id: int) -> dict | None:
    sql = """
        SELECT pl.id, pl.claim_id, pl.coverage_type, pl.base_limit, pl.extended_limit,
               COALESCE(_cov._computed_paid, 0) AS paid,
               (COALESCE(pl.base_limit, 0) + COALESCE(pl.extended_limit, 0)
                - COALESCE(_cov._computed_paid, 0)) AS remaining
        FROM policy_limits pl
        LEFT JOIN (
            SELECT tc.coverage_type, SUM(tc.amount) AS _computed_paid
            FROM transaction_coverage tc
            JOIN transactions t ON t.id = tc.transaction_id
            WHERE t.claim_id = (SELECT claim_id FROM policy_limits WHERE id = ?)
              AND (t.void IS NULL OR t.void = 0)
            GROUP BY tc.coverage_type
        ) _cov ON _cov.coverage_type = pl.coverage_type
        WHERE pl.id = ?
    """
    row = _row(get_db(), sql, (limit_id, limit_id))
    return dict(row) if row else None

def create_limit(claim_id: int, data: dict) -> int:
    cur = get_db().execute(
        """INSERT INTO policy_limits (claim_id, coverage_type, base_limit, extended_limit)
           VALUES (:claim_id, :coverage_type, :base_limit, :extended_limit)""",
        {"claim_id": claim_id, **data},
    )
    get_db().commit()
    return cur.lastrowid

def update_limit(limit_id: int, data: dict) -> None:
    get_db().execute(
        """UPDATE policy_limits SET coverage_type=:coverage_type,
                                    base_limit=:base_limit,
                                    extended_limit=:extended_limit
           WHERE id = :id""",
        {**data, "id": limit_id},
    )
    get_db().commit()

def delete_limit(limit_id: int) -> None:
    get_db().execute("DELETE FROM policy_limits WHERE id = ?", (limit_id,))
    get_db().commit()

def sync_limit_paid(claim_id: int) -> None:
    """Recompute paid/remaining on all policy_limits for this claim from transaction_coverage."""
    db = get_db()
    # Sum coverage allocations per type across all non-void transactions
    totals = _rows(db, """
        SELECT tc.coverage_type, COALESCE(SUM(tc.amount), 0) AS total_paid
        FROM transaction_coverage tc
        JOIN transactions t ON t.id = tc.transaction_id
        WHERE t.claim_id = ? AND (t.void IS NULL OR t.void = 0)
        GROUP BY tc.coverage_type
    """, (claim_id,))
    paid_map = {r["coverage_type"]: r["total_paid"] for r in totals}

    limits = _rows(db, "SELECT * FROM policy_limits WHERE claim_id = ?", (claim_id,))
    for lim in limits:
        paid = paid_map.get(lim["coverage_type"], 0.0)
        remaining = (lim["base_limit"] or 0.0) + (lim["extended_limit"] or 0.0) - paid
        db.execute(
            "UPDATE policy_limits SET paid=?, remaining=? WHERE id=?",
            (paid, remaining, lim["id"]),
        )
    db.commit()


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------

def get_transactions(claim_id: int) -> list[dict]:
    """Return transactions with disbursement-computed summary columns and invoice payment totals."""
    sql = """
        SELECT t.*,
               COALESCE(d.net_to_insured, 0)      AS net_to_insured,
               COALESCE(d.to_vendors, 0)           AS to_vendors,
               COALESCE(d.disburse_fee_owed, 0)    AS disburse_fee_owed,
               COALESCE(d.disburse_fee_collected, 0) AS disburse_fee_collected,
               COALESCE(d.disburse_fee_deferred, 0)  AS disburse_fee_deferred,
               COALESCE(d.disburse_fee_recouped, 0)  AS disburse_fee_recouped,
               CASE WHEN d.transaction_id IS NOT NULL THEN 1 ELSE 0 END AS has_disbursements,
               COALESCE(ip.amount_paid, 0)           AS amount_paid,
               CASE WHEN dr.total_count > 0 AND dr.received_count = dr.total_count THEN 1 ELSE 0 END AS all_disburse_received
        FROM transactions t
        LEFT JOIN (
            SELECT transaction_id,
                   SUM(CASE WHEN recipient_type='insured'
                       THEN amount - fee_collected ELSE 0 END)  AS net_to_insured,
                   SUM(CASE WHEN recipient_type='vendor'
                       THEN amount ELSE 0 END)                   AS to_vendors,
                   SUM(fee_owed)                                 AS disburse_fee_owed,
                   SUM(fee_collected)                            AS disburse_fee_collected,
                   SUM(fee_deferred)                             AS disburse_fee_deferred,
                   SUM(fee_recouped)                             AS disburse_fee_recouped
            FROM disbursements
            GROUP BY transaction_id
        ) d ON d.transaction_id = t.id
        LEFT JOIN (
            SELECT transaction_id, COALESCE(SUM(amount), 0) AS amount_paid
            FROM invoice_payments
            GROUP BY transaction_id
        ) ip ON ip.transaction_id = t.id
        LEFT JOIN (
            SELECT transaction_id,
                   COUNT(*) AS total_count,
                   SUM(CASE WHEN client_received THEN 1 ELSE 0 END) AS received_count
            FROM disbursements
            GROUP BY transaction_id
        ) dr ON dr.transaction_id = t.id
        WHERE t.claim_id = ?
        ORDER BY t.sequence_number, t.id
    """
    return _rows(get_db(), sql, (claim_id,))

def get_transaction(tx_id: int) -> dict | None:
    row = _row(get_db(), """
        SELECT t.*, v.name AS inv_vendor_name,
               fee_inv.invoice_number AS proc_fee_inv_number
        FROM transactions t
        LEFT JOIN vendors v ON v.id = t.inv_vendor_id
        LEFT JOIN transactions fee_inv ON fee_inv.id = t.proc_fee_invoice_id
        WHERE t.id = ?
    """, (tx_id,))
    return dict(row) if row else None

def _next_sequence(claim_id: int) -> int:
    row = _row(get_db(),
               "SELECT COALESCE(MAX(sequence_number), 0) + 1 AS n FROM transactions WHERE claim_id = ?",
               (claim_id,))
    return row["n"]

def effective_fee_pct(tx: dict, claim: dict) -> float:
    """Return the fee % to use for this transaction (tx override or claim default)."""
    if tx.get("fee_pct") is not None:
        return float(tx["fee_pct"])
    return float(claim.get("contract_pct") or 0)

def create_transaction(claim_id: int, data: dict) -> int:
    now = _now()
    seq = data.get("sequence_number") or _next_sequence(claim_id)
    cur = get_db().execute(
        """INSERT INTO transactions
               (claim_id, sequence_number, date, amount, type,
                fee_pct, fee_owed, balance, deferred, recouped,
                fee_collected, reimbursed, total_collected,
                unpaid_payee_expense, outstanding_expense,
                ott, notes, check_id,
                check_number, received_date, payer, payees_text, endorsed, void,
                linked_escrow_id, inv_for, inv_vendor_id,
                proc_method, proc_fee_invoice_id,
                created_at, updated_at)
           VALUES
               (:claim_id, :seq, :date, :amount, :type,
                :fee_pct, :fee_owed, :balance, :deferred, :recouped,
                :fee_collected, :reimbursed, :total_collected,
                :unpaid_payee_expense, :outstanding_expense,
                :ott, :notes, :check_id,
                :check_number, :received_date, :payer, :payees_text, :endorsed, :void,
                :linked_escrow_id, :inv_for, :inv_vendor_id,
                :proc_method, :proc_fee_invoice_id,
                :now, :now)""",
        {
            "claim_id": claim_id,
            "seq": seq,
            "date": data.get("date", ""),
            "amount": _f(data.get("amount")),
            "type": data.get("type", ""),
            "fee_pct": _fopt(data.get("fee_pct")),
            "fee_owed": _f(data.get("fee_owed")),
            "balance": _f(data.get("balance")),
            "deferred": _f(data.get("deferred")),
            "recouped": _f(data.get("recouped")),
            "fee_collected": _f(data.get("fee_collected")),
            "reimbursed": _f(data.get("reimbursed")),
            "total_collected": _f(data.get("total_collected")),
            "unpaid_payee_expense": _f(data.get("unpaid_payee_expense")),
            "outstanding_expense": _f(data.get("outstanding_expense")),
            "ott": _fopt(data.get("ott")),
            "notes": data.get("notes", ""),
            "check_id": data.get("check_id") or None,
            "check_number": data.get("check_number", "") or "",
            "received_date": data.get("received_date", "") or "",
            "payer": data.get("payer", "") or "",
            "payees_text": data.get("payees_text", "") or "",
            "endorsed": 1 if data.get("endorsed") else 0,
            "void": 1 if data.get("void") else 0,
            "linked_escrow_id": data.get("linked_escrow_id") or None,
            "inv_for": data.get("inv_for") or "insured",
            "inv_vendor_id": data.get("inv_vendor_id") or None,
            "proc_method": data.get("proc_method") or None,
            "proc_fee_invoice_id": data.get("proc_fee_invoice_id") or None,
            "now": now,
        },
    )
    tx_id = cur.lastrowid
    get_db().commit()

    # Auto-clone claim assignees as starting splits for this transaction
    clone_splits_from_assignees(claim_id, tx_id)
    return tx_id

def update_transaction(tx_id: int, data: dict) -> None:
    get_db().execute(
        """UPDATE transactions SET
               sequence_number=:seq, date=:date, amount=:amount, type=:type,
               fee_pct=:fee_pct, fee_owed=:fee_owed, balance=:balance,
               deferred=:deferred, recouped=:recouped,
               fee_collected=:fee_collected, reimbursed=:reimbursed,
               total_collected=:total_collected,
               unpaid_payee_expense=:unpaid_payee_expense,
               outstanding_expense=:outstanding_expense,
               ott=:ott, notes=:notes,
               check_number=:check_number, received_date=:received_date,
               payer=:payer, payees_text=:payees_text,
               endorsed=:endorsed, void=:void,
               linked_escrow_id=:linked_escrow_id,
               invoice_number=:invoice_number,
               inv_client_paid=:inv_client_paid,
               inv_fee_received=:inv_fee_received,
               inv_for=:inv_for,
               inv_vendor_id=:inv_vendor_id,
               proc_method=:proc_method,
               proc_fee_invoice_id=:proc_fee_invoice_id,
               updated_at=:now
           WHERE id = :id""",
        {
            "seq": data.get("sequence_number"),
            "date": data.get("date", ""),
            "amount": _f(data.get("amount")),
            "type": data.get("type", ""),
            "fee_pct": _fopt(data.get("fee_pct")),
            "fee_owed": _f(data.get("fee_owed")),
            "balance": _f(data.get("balance")),
            "deferred": _f(data.get("deferred")),
            "recouped": _f(data.get("recouped")),
            "fee_collected": _f(data.get("fee_collected")),
            "reimbursed": _f(data.get("reimbursed")),
            "total_collected": _f(data.get("total_collected")),
            "unpaid_payee_expense": _f(data.get("unpaid_payee_expense")),
            "outstanding_expense": _f(data.get("outstanding_expense")),
            "ott": _fopt(data.get("ott")),
            "notes": data.get("notes", ""),
            "check_number": data.get("check_number", "") or "",
            "received_date": data.get("received_date", "") or "",
            "payer": data.get("payer", "") or "",
            "payees_text": data.get("payees_text", "") or "",
            "endorsed": 1 if data.get("endorsed") else 0,
            "void": 1 if data.get("void") else 0,
            "linked_escrow_id": data.get("linked_escrow_id") or None,
            "invoice_number": data.get("invoice_number", "") or "",
            "inv_client_paid":  1 if data.get("inv_client_paid") else 0,
            "inv_fee_received": 1 if data.get("inv_fee_received") else 0,
            "inv_for": data.get("inv_for") or "insured",
            "inv_vendor_id": data.get("inv_vendor_id") or None,
            "proc_method": data.get("proc_method") or None,
            "proc_fee_invoice_id": data.get("proc_fee_invoice_id") or None,
            "now": _now(),
            "id": tx_id,
        },
    )
    get_db().commit()

def delete_transaction(tx_id: int) -> None:
    get_db().execute("DELETE FROM transactions WHERE id = ?", (tx_id,))
    get_db().commit()

def get_open_escrows(claim_id: int) -> list[dict]:
    """Return unresolved Escrow transactions for a claim (for Draw linking)."""
    return _rows(get_db(),
                 """SELECT id, sequence_number, date, amount
                    FROM transactions
                    WHERE claim_id = ? AND LOWER(type) = 'escrow'
                    ORDER BY sequence_number, id""",
                 (claim_id,))

def get_transaction_summary(claim_id: int) -> dict:
    rows = get_transactions(claim_id)
    s = {
        "total_amount": 0.0,
        "released": 0.0,    # Carrier + Draw + Escrow Endorsed
        "in_escrow": 0.0,   # Escrow (unresolved)
        "escrow": 0.0,      # legacy compat
        "draw": 0.0,
        "carrier": 0.0,
        "other": 0.0,
        "total_fee_owed": 0.0,
        "total_fee_collected": 0.0,
        "total_deferred": 0.0,
        "total_recouped": 0.0,
        "total_reimbursed": 0.0,
        "total_collected": 0.0,
        "total_outstanding_expense": 0.0,
        "net_to_insured": 0.0,
        "to_vendors": 0.0,
    }
    for t in rows:
        if t.get("void"):
            continue
        amt = t.get("amount") or 0.0
        s["total_amount"] += amt
        t_type = (t.get("type") or "").lower().strip()
        if t_type in RELEASED_TYPES:
            s["released"] += amt
        elif t_type == ESCROW_TYPE:
            s["in_escrow"] += amt
            s["escrow"] += amt

        if "draw" in t_type:
            s["draw"] += amt
        elif "carrier" in t_type:
            s["carrier"] += amt
        elif t_type not in RELEASED_TYPES and t_type != ESCROW_TYPE:
            s["other"] += amt

        # Use disbursement-computed values if present, fall back to tx columns
        if t.get("has_disbursements"):
            # fee_owed: money arrives via Escrow or Carrier
            if t_type in (ESCROW_TYPE, "carrier", "escrow endorsed"):
                s["total_fee_owed"] += t.get("disburse_fee_owed") or 0.0
            # fee_collected: money goes out via Draw, Carrier, or Escrow Endorsed
            if t_type in RELEASED_TYPES:
                s["total_fee_collected"] += t.get("disburse_fee_collected") or 0.0
            s["total_deferred"]      += t.get("disburse_fee_deferred") or 0.0
            # Use max of disbursement-level and transaction-level recouped: the latter is
            # auto-set by invoice payment side effects and may be non-zero even when
            # disburse_fee_recouped is still 0.
            s["total_recouped"]      += max(t.get("disburse_fee_recouped") or 0.0, t.get("recouped") or 0.0)
            if t_type in RELEASED_TYPES:
                s["net_to_insured"]  += t.get("net_to_insured") or 0.0
                s["to_vendors"]      += t.get("to_vendors") or 0.0
        else:
            # fee_owed: money arrives via Escrow or Carrier
            if t_type in (ESCROW_TYPE, "carrier", "escrow endorsed"):
                s["total_fee_owed"] += t.get("fee_owed") or 0.0
            # fee_collected: money goes out via Draw, Carrier, or Escrow Endorsed
            if t_type in RELEASED_TYPES:
                s["total_fee_collected"] += t.get("fee_collected") or 0.0
            s["total_deferred"]      += t.get("deferred") or 0.0
            s["total_recouped"]      += t.get("recouped") or 0.0

        # Fee Invoice payments (via invoice_payments → total_collected) count as collected fee
        if t_type == "fee invoice":
            s["total_fee_collected"] += t.get("total_collected") or 0.0
            inv_for = (t.get("inv_for") or "insured").lower()
            if inv_for == "insured":
                s["net_to_insured"] -= t.get("total_collected") or 0.0

        s["total_reimbursed"]        += t.get("reimbursed") or 0.0
        s["total_collected"]         += t.get("total_collected") or 0.0
        s["total_outstanding_expense"] += t.get("outstanding_expense") or 0.0
    s["draw_vs_escrow"] = s["draw"] - s["in_escrow"]
    s["deferred_vs_recouped"] = s["total_deferred"] - s["total_recouped"]
    return s


# ---------------------------------------------------------------------------
# Assignees (claim-level split defaults)
# ---------------------------------------------------------------------------

def get_assignees(claim_id: int) -> list[dict]:
    return _rows(get_db(),
                 "SELECT * FROM assignees WHERE claim_id = ? ORDER BY sort_order, id",
                 (claim_id,))

def get_assignee(assignee_id: int) -> dict | None:
    row = _row(get_db(), "SELECT * FROM assignees WHERE id = ?", (assignee_id,))
    return dict(row) if row else None

def create_assignee(claim_id: int, data: dict) -> int:
    cur = get_db().execute(
        """INSERT INTO assignees (claim_id, role, name, split_pct, sort_order, recipient_id)
           VALUES (:claim_id, :role, :name, :split_pct, :sort_order, :recipient_id)""",
        {
            "claim_id": claim_id,
            "role": data.get("role", "other"),
            "name": data.get("name", ""),
            "split_pct": _f(data.get("split_pct")),
            "sort_order": int(data.get("sort_order") or 0),
            "recipient_id": data.get("recipient_id") or None,
        },
    )
    get_db().commit()
    return cur.lastrowid

def update_assignee(assignee_id: int, data: dict) -> None:
    get_db().execute(
        """UPDATE assignees SET role=:role, name=:name,
                                split_pct=:split_pct, sort_order=:sort_order,
                                recipient_id=:recipient_id
           WHERE id = :id""",
        {
            "role": data.get("role", "other"),
            "name": data.get("name", ""),
            "split_pct": _f(data.get("split_pct")),
            "sort_order": int(data.get("sort_order") or 0),
            "recipient_id": data.get("recipient_id") or None,
            "id": assignee_id,
        },
    )
    get_db().commit()

def delete_assignee(assignee_id: int) -> None:
    get_db().execute("DELETE FROM assignees WHERE id = ?", (assignee_id,))
    get_db().commit()

def get_assignee_total_pct(claim_id: int) -> float:
    row = _row(get_db(),
               "SELECT COALESCE(SUM(split_pct), 0) AS total FROM assignees WHERE claim_id = ?",
               (claim_id,))
    return float(row["total"])


# ---------------------------------------------------------------------------
# Transaction Splits (per-transaction payroll, cloned from assignees)
# ---------------------------------------------------------------------------

def get_splits(tx_id: int) -> list[dict]:
    return _rows(get_db(),
                 "SELECT * FROM transaction_splits WHERE transaction_id = ? ORDER BY id",
                 (tx_id,))

def get_split(split_id: int) -> dict | None:
    row = _row(get_db(), "SELECT * FROM transaction_splits WHERE id = ?", (split_id,))
    return dict(row) if row else None

def create_split(tx_id: int, data: dict) -> int:
    cur = get_db().execute(
        """INSERT INTO transaction_splits (transaction_id, role, name, split_pct, recipient_id)
           VALUES (:tx_id, :role, :name, :split_pct, :recipient_id)""",
        {
            "tx_id": tx_id,
            "role": data.get("role", "other"),
            "name": data.get("name", ""),
            "split_pct": _f(data.get("split_pct")),
            "recipient_id": data.get("recipient_id") or None,
        },
    )
    get_db().commit()
    return cur.lastrowid

def update_split(split_id: int, data: dict) -> None:
    get_db().execute(
        """UPDATE transaction_splits SET role=:role, name=:name, split_pct=:split_pct,
                                         recipient_id=:recipient_id
           WHERE id = :id""",
        {
            "role": data.get("role", "other"),
            "name": data.get("name", ""),
            "split_pct": _f(data.get("split_pct")),
            "recipient_id": data.get("recipient_id") or None,
            "id": split_id,
        },
    )
    get_db().commit()

def delete_split(split_id: int) -> None:
    get_db().execute("DELETE FROM transaction_splits WHERE id = ?", (split_id,))
    get_db().commit()

def clone_splits_from_assignees(claim_id: int, tx_id: int) -> None:
    """Copy the claim's current assignees into transaction_splits."""
    assignees = get_assignees(claim_id)
    for a in assignees:
        get_db().execute(
            """INSERT INTO transaction_splits (transaction_id, role, name, split_pct)
               VALUES (?, ?, ?, ?)""",
            (tx_id, a["role"], a["name"], a["split_pct"]),
        )
    get_db().commit()

def calc_split_amounts(split: dict, disburse_totals: dict, include_recouped: bool = True) -> dict:
    """Return a split dict enriched with calculated dollar amounts from disbursement totals.

    Pass include_recouped=False when custom recouped splits are active, so the recouped
    column shows — in the main splits table (the recouped portion is tracked separately).
    """
    pct = (split.get("split_pct") or 0) / 100.0
    fee_owed = disburse_totals.get("fee_owed") or 0
    deferred  = disburse_totals.get("fee_deferred") or 0
    recouped  = (disburse_totals.get("fee_recouped") or 0) if include_recouped else 0
    return {
        **split,
        "fee_amount":      round(fee_owed  * pct, 2),
        "deferred_amount": round(deferred  * pct, 2),
        "recouped_amount": round(recouped  * pct, 2),
    }

def get_splits_with_amounts(tx_id: int, include_recouped: bool = True) -> list[dict]:
    totals = get_disbursement_totals(tx_id)
    return [calc_split_amounts(s, totals, include_recouped=include_recouped) for s in get_splits(tx_id)]


# ---------------------------------------------------------------------------
# Transaction Coverage Allocation
# ---------------------------------------------------------------------------

def get_coverages(tx_id: int) -> list[dict]:
    return _rows(get_db(),
                 "SELECT * FROM transaction_coverage WHERE transaction_id = ? ORDER BY id",
                 (tx_id,))

def get_coverage(cov_id: int) -> dict | None:
    row = _row(get_db(), "SELECT * FROM transaction_coverage WHERE id = ?", (cov_id,))
    return dict(row) if row else None

def create_coverage(tx_id: int, data: dict) -> int:
    cur = get_db().execute(
        """INSERT INTO transaction_coverage (transaction_id, coverage_type, amount)
           VALUES (?, ?, ?)""",
        (tx_id, data.get("coverage_type", ""), _f(data.get("amount"))),
    )
    get_db().commit()
    return cur.lastrowid

def update_coverage(cov_id: int, data: dict) -> None:
    get_db().execute(
        "UPDATE transaction_coverage SET coverage_type=?, amount=? WHERE id=?",
        (data.get("coverage_type", ""), _f(data.get("amount")), cov_id),
    )
    get_db().commit()

def delete_coverage(cov_id: int) -> None:
    get_db().execute("DELETE FROM transaction_coverage WHERE id = ?", (cov_id,))
    get_db().commit()


# ---------------------------------------------------------------------------
# Vendors
# ---------------------------------------------------------------------------

def get_all_vendors() -> list[dict]:
    return _rows(get_db(), "SELECT * FROM vendors ORDER BY name")

def get_vendor(vendor_id: int) -> dict | None:
    row = _row(get_db(), "SELECT * FROM vendors WHERE id = ?", (vendor_id,))
    return dict(row) if row else None

def create_vendor(data: dict) -> int:
    now = _now()
    cur = get_db().execute(
        """INSERT INTO vendors (name, phone, email, notes, created_at)
           VALUES (:name, :phone, :email, :notes, :now)""",
        {
            "name":  data.get("name", "").strip(),
            "phone": data.get("phone", "").strip(),
            "email": data.get("email", "").strip(),
            "notes": data.get("notes", "").strip(),
            "now": now,
        },
    )
    get_db().commit()
    return cur.lastrowid

def update_vendor(vendor_id: int, data: dict) -> None:
    get_db().execute(
        """UPDATE vendors SET name=:name, phone=:phone, email=:email, notes=:notes
           WHERE id = :id""",
        {
            "name":  data.get("name", "").strip(),
            "phone": data.get("phone", "").strip(),
            "email": data.get("email", "").strip(),
            "notes": data.get("notes", "").strip(),
            "id": vendor_id,
        },
    )
    get_db().commit()

def delete_vendor(vendor_id: int) -> None:
    get_db().execute("DELETE FROM vendors WHERE id = ?", (vendor_id,))
    get_db().commit()


# ---------------------------------------------------------------------------
# Fee Recipients
# ---------------------------------------------------------------------------

def get_all_fee_recipients() -> list[dict]:
    return _rows(get_db(), "SELECT * FROM fee_recipients ORDER BY name")

def get_fee_recipient(recipient_id: int) -> dict | None:
    row = _row(get_db(), "SELECT * FROM fee_recipients WHERE id = ?", (recipient_id,))
    return dict(row) if row else None

def create_fee_recipient(data: dict) -> int:
    now = _now()
    cur = get_db().execute(
        """INSERT INTO fee_recipients (name, default_role, created_at)
           VALUES (:name, :default_role, :now)""",
        {
            "name":         data.get("name", "").strip(),
            "default_role": data.get("default_role", "").strip(),
            "now": now,
        },
    )
    get_db().commit()
    return cur.lastrowid

def update_fee_recipient(recipient_id: int, data: dict) -> None:
    get_db().execute(
        "UPDATE fee_recipients SET name=:name, default_role=:default_role WHERE id=:id",
        {
            "name":         data.get("name", "").strip(),
            "default_role": data.get("default_role", "").strip(),
            "id": recipient_id,
        },
    )
    get_db().commit()

def delete_fee_recipient(recipient_id: int) -> None:
    get_db().execute("DELETE FROM fee_recipients WHERE id = ?", (recipient_id,))
    get_db().commit()


# ---------------------------------------------------------------------------
# Disbursements
# ---------------------------------------------------------------------------

def get_disbursements(tx_id: int) -> list[dict]:
    return _rows(get_db(),
                 """SELECT d.*, v.name AS vendor_name
                    FROM disbursements d
                    LEFT JOIN vendors v ON v.id = d.vendor_id
                    WHERE d.transaction_id = ?
                    ORDER BY d.sort_order, d.id""",
                 (tx_id,))

def get_disbursement(did: int) -> dict | None:
    row = _row(get_db(),
               """SELECT d.*, v.name AS vendor_name
                  FROM disbursements d
                  LEFT JOIN vendors v ON v.id = d.vendor_id
                  WHERE d.id = ?""",
               (did,))
    return dict(row) if row else None

def create_disbursement(tx_id: int, data: dict) -> int:
    now = _now()
    cur = get_db().execute(
        """INSERT INTO disbursements
               (transaction_id, sort_order, recipient_type, vendor_id,
                recipient_name, amount, fee_applies, fee_pct,
                fee_owed, fee_collected, fee_deferred, fee_recouped,
                use_check_splits, notes, created_at, updated_at)
           VALUES
               (:tx_id, :sort_order, :recipient_type, :vendor_id,
                :recipient_name, :amount, :fee_applies, :fee_pct,
                :fee_owed, :fee_collected, :fee_deferred, :fee_recouped,
                :use_check_splits, :notes, :now, :now)""",
        {
            "tx_id":          tx_id,
            "sort_order":     int(data.get("sort_order") or 0),
            "recipient_type": data.get("recipient_type", "insured"),
            "vendor_id":      data.get("vendor_id") or None,
            "recipient_name": data.get("recipient_name", "").strip(),
            "amount":         _f(data.get("amount")),
            "fee_applies":    1 if data.get("fee_applies", True) else 0,
            "fee_pct":        _fopt(data.get("fee_pct")),
            "fee_owed":       _f(data.get("fee_owed")),
            "fee_collected":  _f(data.get("fee_collected")),
            "fee_deferred":   _f(data.get("fee_deferred")),
            "fee_recouped":   _f(data.get("fee_recouped")),
            "use_check_splits": 1 if data.get("use_check_splits", True) else 0,
            "notes":          data.get("notes", "").strip(),
            "now": now,
        },
    )
    get_db().commit()
    return cur.lastrowid

def update_disbursement(did: int, data: dict) -> None:
    get_db().execute(
        """UPDATE disbursements SET
               sort_order=:sort_order, recipient_type=:recipient_type,
               vendor_id=:vendor_id, recipient_name=:recipient_name,
               amount=:amount, fee_applies=:fee_applies, fee_pct=:fee_pct,
               fee_owed=:fee_owed, fee_collected=:fee_collected,
               fee_deferred=:fee_deferred, fee_recouped=:fee_recouped,
               use_check_splits=:use_check_splits, notes=:notes,
               client_received=:client_received, updated_at=:now
           WHERE id = :id""",
        {
            "sort_order":       int(data.get("sort_order") or 0),
            "recipient_type":   data.get("recipient_type", "insured"),
            "vendor_id":        data.get("vendor_id") or None,
            "recipient_name":   data.get("recipient_name", "").strip(),
            "amount":           _f(data.get("amount")),
            "fee_applies":      1 if data.get("fee_applies", True) else 0,
            "fee_pct":          _fopt(data.get("fee_pct")),
            "fee_owed":         _f(data.get("fee_owed")),
            "fee_collected":    _f(data.get("fee_collected")),
            "fee_deferred":     _f(data.get("fee_deferred")),
            "fee_recouped":     _f(data.get("fee_recouped")),
            "use_check_splits": 1 if data.get("use_check_splits", True) else 0,
            "notes":            data.get("notes", "").strip(),
            "client_received":  1 if data.get("client_received") else 0,
            "now": _now(),
            "id": did,
        },
    )
    get_db().commit()

def delete_disbursement(did: int) -> None:
    get_db().execute("DELETE FROM disbursements WHERE id = ?", (did,))
    get_db().commit()

def get_disbursement_totals(tx_id: int) -> dict:
    """Return aggregate columns for a transaction's disbursements."""
    row = _row(get_db(), """
        SELECT
            COALESCE(SUM(CASE WHEN recipient_type='insured'
                THEN amount - fee_collected ELSE 0 END), 0) AS net_to_insured,
            COALESCE(SUM(CASE WHEN recipient_type='vendor'
                THEN amount ELSE 0 END), 0)                  AS to_vendors,
            COALESCE(SUM(fee_owed), 0)                       AS fee_owed,
            COALESCE(SUM(fee_collected), 0)                  AS fee_collected,
            COALESCE(SUM(fee_deferred), 0)                   AS fee_deferred,
            COALESCE(SUM(fee_recouped), 0)                   AS fee_recouped,
            COALESCE(SUM(amount), 0)                         AS total_disbursed
        FROM disbursements WHERE transaction_id = ?
    """, (tx_id,))
    return dict(row) if row else {
        "net_to_insured": 0, "to_vendors": 0,
        "fee_owed": 0, "fee_collected": 0,
        "fee_deferred": 0, "fee_recouped": 0,
        "total_disbursed": 0,
    }


# ---------------------------------------------------------------------------
# Disbursement Splits (per-disbursement payroll, only when use_check_splits=0)
# ---------------------------------------------------------------------------

def get_disbursement_splits(did: int) -> list[dict]:
    return _rows(get_db(),
                 "SELECT * FROM disbursement_splits WHERE disbursement_id = ? ORDER BY id",
                 (did,))

def get_disbursement_split(sid: int) -> dict | None:
    row = _row(get_db(), "SELECT * FROM disbursement_splits WHERE id = ?", (sid,))
    return dict(row) if row else None

def create_disbursement_split(did: int, data: dict) -> int:
    cur = get_db().execute(
        """INSERT INTO disbursement_splits (disbursement_id, role, name, split_pct, recipient_id)
           VALUES (?, ?, ?, ?, ?)""",
        (did, data.get("role", "other"), data.get("name", ""),
         _f(data.get("split_pct")), data.get("recipient_id") or None),
    )
    get_db().commit()
    return cur.lastrowid

def update_disbursement_split(sid: int, data: dict) -> None:
    get_db().execute(
        "UPDATE disbursement_splits SET role=?, name=?, split_pct=?, recipient_id=? WHERE id=?",
        (data.get("role", "other"), data.get("name", ""),
         _f(data.get("split_pct")), data.get("recipient_id") or None, sid),
    )
    get_db().commit()

def delete_disbursement_split(sid: int) -> None:
    get_db().execute("DELETE FROM disbursement_splits WHERE id = ?", (sid,))
    get_db().commit()

def seed_disbursement_splits_from_tx(did: int, tx_id: int) -> None:
    """Copy transaction_splits into disbursement_splits and set use_check_splits=0."""
    db = get_db()
    db.execute("DELETE FROM disbursement_splits WHERE disbursement_id = ?", (did,))
    splits = get_splits(tx_id)
    for s in splits:
        db.execute(
            "INSERT INTO disbursement_splits (disbursement_id, role, name, split_pct, recipient_id) VALUES (?, ?, ?, ?, ?)",
            (did, s["role"], s["name"], s["split_pct"], s.get("recipient_id")),
        )
    db.execute("UPDATE disbursements SET use_check_splits=0 WHERE id=?", (did,))
    db.commit()

def reset_disbursement_splits(did: int) -> None:
    """Delete custom disbursement splits and revert to use_check_splits=1."""
    db = get_db()
    db.execute("DELETE FROM disbursement_splits WHERE disbursement_id = ?", (did,))
    db.execute("UPDATE disbursements SET use_check_splits=1 WHERE id=?", (did,))
    db.commit()


# ---------------------------------------------------------------------------
# Recouped Fee Custom Splits (per-transaction, separate from main splits)
# ---------------------------------------------------------------------------

def get_recouped_splits(tx_id: int) -> list[dict]:
    return _rows(get_db(),
                 "SELECT * FROM transaction_recouped_splits WHERE transaction_id = ? ORDER BY id",
                 (tx_id,))

def get_recouped_split(sid: int) -> dict | None:
    row = _row(get_db(), "SELECT * FROM transaction_recouped_splits WHERE id = ?", (sid,))
    return dict(row) if row else None

def has_custom_recouped_splits(tx_id: int) -> bool:
    row = _row(get_db(),
               "SELECT COUNT(*) AS n FROM transaction_recouped_splits WHERE transaction_id = ?",
               (tx_id,))
    return bool(row and row["n"] > 0)

def create_recouped_split(tx_id: int, data: dict) -> int:
    cur = get_db().execute(
        """INSERT INTO transaction_recouped_splits (transaction_id, role, name, split_pct, recipient_id)
           VALUES (?, ?, ?, ?, ?)""",
        (tx_id, data.get("role", "other"), data.get("name", ""),
         _f(data.get("split_pct")), data.get("recipient_id") or None),
    )
    get_db().commit()
    return cur.lastrowid

def update_recouped_split(sid: int, data: dict) -> None:
    get_db().execute(
        "UPDATE transaction_recouped_splits SET role=?, name=?, split_pct=?, recipient_id=? WHERE id=?",
        (data.get("role", "other"), data.get("name", ""),
         _f(data.get("split_pct")), data.get("recipient_id") or None, sid),
    )
    get_db().commit()

def delete_recouped_split(sid: int) -> None:
    get_db().execute("DELETE FROM transaction_recouped_splits WHERE id = ?", (sid,))
    get_db().commit()

def seed_recouped_splits_from_tx(tx_id: int) -> None:
    """Copy transaction_splits into transaction_recouped_splits."""
    db = get_db()
    db.execute("DELETE FROM transaction_recouped_splits WHERE transaction_id = ?", (tx_id,))
    splits = get_splits(tx_id)
    for s in splits:
        db.execute(
            "INSERT INTO transaction_recouped_splits (transaction_id, role, name, split_pct, recipient_id) VALUES (?, ?, ?, ?, ?)",
            (tx_id, s["role"], s["name"], s["split_pct"], s.get("recipient_id")),
        )
    db.commit()

def reset_recouped_splits(tx_id: int) -> None:
    """Delete all custom recouped splits for a transaction."""
    get_db().execute("DELETE FROM transaction_recouped_splits WHERE transaction_id = ?", (tx_id,))
    get_db().commit()


def get_escrow_checklist(claim_id: int) -> list[dict]:
    """Cumulative planned-vs-paid checklist across all escrow/draw checks on a claim.

    Planned = sum of disbursement amounts from all Escrow-type transactions.
    Paid    = sum of disbursement amounts from all Draw + Escrow Endorsed transactions.
    """
    sql = """
        WITH planned AS (
            SELECT LOWER(TRIM(d.recipient_name)) AS key,
                   d.recipient_name,
                   d.recipient_type,
                   SUM(d.amount) AS planned,
                   SUM(d.fee_owed) AS fee_owed
            FROM disbursements d
            JOIN transactions t ON t.id = d.transaction_id
            WHERE t.claim_id = ?
              AND LOWER(TRIM(t.type)) IN ('escrow', 'escrow endorsed')
              AND (t.void IS NULL OR t.void = 0)
            GROUP BY LOWER(TRIM(d.recipient_name))
        ),
        paid AS (
            SELECT LOWER(TRIM(d.recipient_name)) AS key,
                   SUM(d.amount) AS paid
            FROM disbursements d
            JOIN transactions t ON t.id = d.transaction_id
            WHERE t.claim_id = ?
              AND LOWER(TRIM(t.type)) IN ('draw', 'escrow endorsed')
              AND (t.void IS NULL OR t.void = 0)
            GROUP BY LOWER(TRIM(d.recipient_name))
        )
        SELECT p.recipient_name,
               p.recipient_type,
               p.planned,
               p.fee_owed,
               COALESCE(pd.paid, 0)              AS paid,
               p.planned - COALESCE(pd.paid, 0)  AS remaining
        FROM planned p
        LEFT JOIN paid pd ON pd.key = p.key
        ORDER BY p.recipient_name
    """
    return _rows(get_db(), sql, (claim_id, claim_id))


# ---------------------------------------------------------------------------
# Expenses
# ---------------------------------------------------------------------------

def get_expenses(claim_id: int) -> list[dict]:
    sql = """
        SELECT e.*,
               COALESCE(p.wp_paid,     0)  AS wp_paid,
               COALESCE(p.client_paid, 0)  AS client_paid,
               COALESCE(p.total_paid,  0)  AS total_paid,
               COALESCE(r.reimbursed,  0)  AS reimbursed_amt,
               COALESCE(e.invoice_amount, 0) - COALESCE(p.total_paid, 0) AS outstanding_to_payee,
               COALESCE(e.client_amount, 0) - COALESCE(p.client_paid, 0) AS client_outstanding_amt,
               COALESCE(e.wp_amount,    0) - COALESCE(p.wp_paid,    0)   AS wp_outstanding_amt,
               -- WP fronted = how much WP paid beyond their own share
               MAX(0, COALESCE(p.wp_paid, 0) - COALESCE(e.wp_amount, 0)) AS wp_fronted_amt,
               -- What client still owes WP after reimbursements
               MAX(0, COALESCE(p.wp_paid, 0) - COALESCE(e.wp_amount, 0))
                 - COALESCE(r.reimbursed, 0)                              AS reimburse_owed
        FROM expenses e
        LEFT JOIN (
            SELECT expense_id,
                   COALESCE(SUM(CASE WHEN paid_by='wp'     THEN amount ELSE 0 END), 0) AS wp_paid,
                   COALESCE(SUM(CASE WHEN paid_by='client' THEN amount ELSE 0 END), 0) AS client_paid,
                   COALESCE(SUM(amount), 0)                                             AS total_paid
            FROM expense_payments
            GROUP BY expense_id
        ) p ON p.expense_id = e.id
        LEFT JOIN (
            SELECT expense_id, COALESCE(SUM(amount), 0) AS reimbursed
            FROM expense_reimbursements
            GROUP BY expense_id
        ) r ON r.expense_id = e.id
        WHERE e.claim_id = ?
        ORDER BY e.invoice_date, e.id
    """
    return _rows(get_db(), sql, (claim_id,))

def get_expense(exp_id: int) -> dict | None:
    row = _row(get_db(), """
        SELECT e.*,
               v.name AS vendor_name,
               COALESCE(p.wp_paid,     0)  AS wp_paid,
               COALESCE(p.client_paid, 0)  AS client_paid,
               COALESCE(p.total_paid,  0)  AS total_paid,
               COALESCE(e.invoice_amount, 0) - COALESCE(p.total_paid, 0)  AS outstanding_to_payee,
               COALESCE(e.client_amount,  0) - COALESCE(p.client_paid, 0) AS client_outstanding_amt,
               COALESCE(e.wp_amount,      0) - COALESCE(p.wp_paid,    0)  AS wp_outstanding_amt,
               MAX(0, COALESCE(p.wp_paid, 0) - COALESCE(e.wp_amount, 0))  AS wp_fronted_amt,
               MAX(0, COALESCE(p.wp_paid, 0) - COALESCE(e.wp_amount, 0))
                 - COALESCE(r.reimbursed, 0)                               AS reimburse_owed
        FROM expenses e
        LEFT JOIN vendors v ON v.id = e.vendor_id
        LEFT JOIN (
            SELECT expense_id,
                   COALESCE(SUM(CASE WHEN paid_by='wp'     THEN amount ELSE 0 END), 0) AS wp_paid,
                   COALESCE(SUM(CASE WHEN paid_by='client' THEN amount ELSE 0 END), 0) AS client_paid,
                   COALESCE(SUM(amount), 0)                                             AS total_paid
            FROM expense_payments GROUP BY expense_id
        ) p ON p.expense_id = e.id
        LEFT JOIN (
            SELECT expense_id, COALESCE(SUM(amount), 0) AS reimbursed
            FROM expense_reimbursements GROUP BY expense_id
        ) r ON r.expense_id = e.id
        WHERE e.id = ?
    """, (exp_id,))
    return dict(row) if row else None

def create_expense(claim_id: int, data: dict) -> int:
    now = _now()
    cur = get_db().execute(
        """INSERT INTO expenses (claim_id, invoice_date, payee_name, vendor_id,
                                  invoice_amount,
                                  responsible_party, unpaid_to_payee,
                                  client_outstanding, wp_outstanding,
                                  reason, invoice_number, client_amount, wp_amount,
                                  created_at, updated_at)
           VALUES (:claim_id, :invoice_date, :payee_name, :vendor_id,
                   :invoice_amount,
                   :responsible_party, :unpaid_to_payee,
                   :client_outstanding, :wp_outstanding,
                   :reason, :invoice_number, :client_amount, :wp_amount,
                   :now, :now)""",
        {
            "claim_id": claim_id,
            "invoice_date": data.get("invoice_date", ""),
            "payee_name": data.get("payee_name", ""),
            "vendor_id": data.get("vendor_id") or None,
            "invoice_amount": _f(data.get("invoice_amount")),
            "responsible_party": data.get("responsible_party", ""),
            "unpaid_to_payee": _f(data.get("unpaid_to_payee")),
            "client_outstanding": _f(data.get("client_outstanding")),
            "wp_outstanding": _f(data.get("wp_outstanding")),
            "reason": data.get("reason", ""),
            "invoice_number": data.get("invoice_number", ""),
            "client_amount": _f(data.get("client_amount")),
            "wp_amount": _f(data.get("wp_amount")),
            "now": now,
        },
    )
    exp_id = cur.lastrowid
    get_db().commit()
    clone_splits_from_assignees_for_expense(claim_id, exp_id)
    return exp_id

def update_expense(exp_id: int, data: dict) -> None:
    get_db().execute(
        """UPDATE expenses SET invoice_date=:invoice_date, payee_name=:payee_name,
                               vendor_id=:vendor_id,
                               invoice_amount=:invoice_amount,
                               responsible_party=:responsible_party,
                               unpaid_to_payee=:unpaid_to_payee,
                               client_outstanding=:client_outstanding,
                               wp_outstanding=:wp_outstanding,
                               reason=:reason, invoice_number=:invoice_number,
                               client_amount=:client_amount, wp_amount=:wp_amount,
                               updated_at=:now
           WHERE id = :id""",
        {
            "invoice_date": data.get("invoice_date", ""),
            "payee_name": data.get("payee_name", ""),
            "vendor_id": data.get("vendor_id") or None,
            "invoice_amount": _f(data.get("invoice_amount")),
            "responsible_party": data.get("responsible_party", ""),
            "unpaid_to_payee": _f(data.get("unpaid_to_payee")),
            "client_outstanding": _f(data.get("client_outstanding")),
            "wp_outstanding": _f(data.get("wp_outstanding")),
            "reason": data.get("reason", ""),
            "invoice_number": data.get("invoice_number", ""),
            "client_amount": _f(data.get("client_amount")),
            "wp_amount": _f(data.get("wp_amount")),
            "now": _now(),
            "id": exp_id,
        },
    )
    get_db().commit()

def delete_expense(exp_id: int) -> None:
    get_db().execute("DELETE FROM expenses WHERE id = ?", (exp_id,))
    get_db().commit()


# ---------------------------------------------------------------------------
# Expense Payments
# ---------------------------------------------------------------------------

def get_expense_payments(expense_id: int) -> list[dict]:
    return _rows(get_db(),
                 "SELECT * FROM expense_payments WHERE expense_id = ? ORDER BY date, id",
                 (expense_id,))

def create_expense_payment(expense_id: int, data: dict) -> int:
    now = _now()
    cur = get_db().execute(
        """INSERT INTO expense_payments
               (expense_id, date, paid_by, amount, method, reference, notes, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            expense_id,
            data.get("date", "") or "",
            data.get("paid_by", "wp"),
            _f(data.get("amount")),
            data.get("method", "") or "",
            data.get("reference", "") or "",
            data.get("notes", "") or "",
            now,
        ),
    )
    get_db().commit()
    return cur.lastrowid

def update_expense_payment(pid: int, data: dict) -> None:
    get_db().execute(
        """UPDATE expense_payments
           SET date=?, paid_by=?, amount=?, method=?, reference=?, notes=?
           WHERE id=?""",
        (
            data.get("date", "") or "",
            data.get("paid_by", "wp"),
            _f(data.get("amount")),
            data.get("method", "") or "",
            data.get("reference", "") or "",
            data.get("notes", "") or "",
            pid,
        ),
    )
    get_db().commit()

def delete_expense_payment(pid: int) -> None:
    get_db().execute("DELETE FROM expense_payments WHERE id = ?", (pid,))
    get_db().commit()


# ---------------------------------------------------------------------------
# Expense Reimbursements
# ---------------------------------------------------------------------------

def get_expense_reimbursements(expense_id: int) -> list[dict]:
    return _rows(get_db(), """
        SELECT r.*, t.sequence_number AS linked_tx_seq
        FROM expense_reimbursements r
        LEFT JOIN transactions t ON t.id = r.linked_tx_id
        WHERE r.expense_id = ?
        ORDER BY r.date, r.id
    """, (expense_id,))

def create_expense_reimbursement(expense_id: int, data: dict) -> int:
    now = _now()
    cur = get_db().execute(
        """INSERT INTO expense_reimbursements
               (expense_id, date, amount, method, reference, linked_tx_id, notes, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            expense_id,
            data.get("date", "") or "",
            _f(data.get("amount")),
            data.get("method", "") or "",
            data.get("reference", "") or "",
            data.get("linked_tx_id") or None,
            data.get("notes", "") or "",
            now,
        ),
    )
    get_db().commit()
    return cur.lastrowid

def delete_expense_reimbursement(rid: int) -> None:
    get_db().execute("DELETE FROM expense_reimbursements WHERE id = ?", (rid,))
    get_db().commit()


# ---------------------------------------------------------------------------
# Expense Splits
# ---------------------------------------------------------------------------

def get_expense_splits(expense_id: int) -> list[dict]:
    return _rows(get_db(),
                 "SELECT * FROM expense_splits WHERE expense_id = ? ORDER BY id",
                 (expense_id,))

def create_expense_split(expense_id: int, data: dict) -> int:
    cur = get_db().execute(
        """INSERT INTO expense_splits (expense_id, role, name, split_pct, recipient_id)
           VALUES (?, ?, ?, ?, ?)""",
        (expense_id, data.get("role", "other"), data.get("name", ""),
         _f(data.get("split_pct")), data.get("recipient_id") or None),
    )
    get_db().commit()
    return cur.lastrowid

def update_expense_split(sid: int, data: dict) -> None:
    get_db().execute(
        "UPDATE expense_splits SET role=?, name=?, split_pct=?, recipient_id=? WHERE id=?",
        (data.get("role", "other"), data.get("name", ""),
         _f(data.get("split_pct")), data.get("recipient_id") or None, sid),
    )
    get_db().commit()

def delete_expense_split(sid: int) -> None:
    get_db().execute("DELETE FROM expense_splits WHERE id = ?", (sid,))
    get_db().commit()

def clone_splits_from_assignees_for_expense(claim_id: int, expense_id: int) -> None:
    """Copy the claim's current assignees into expense_splits."""
    assignees = get_assignees(claim_id)
    for a in assignees:
        get_db().execute(
            "INSERT INTO expense_splits (expense_id, role, name, split_pct) VALUES (?, ?, ?, ?)",
            (expense_id, a["role"], a["name"], a["split_pct"]),
        )
    get_db().commit()

def get_expense_totals(expense_id: int) -> dict:
    """Return aggregate payment and reimbursement totals for an expense."""
    exp = get_expense(expense_id)
    if not exp:
        return {}
    wp_amount = _f(exp.get("wp_amount"))
    client_amount = _f(exp.get("client_amount"))

    pay_row = _row(get_db(), """
        SELECT
            COALESCE(SUM(CASE WHEN paid_by='wp' THEN amount ELSE 0 END), 0)     AS wp_paid,
            COALESCE(SUM(CASE WHEN paid_by='client' THEN amount ELSE 0 END), 0) AS client_paid,
            COALESCE(SUM(amount), 0)                                              AS total_paid
        FROM expense_payments WHERE expense_id = ?
    """, (expense_id,))

    reimb_row = _row(get_db(),
                     "SELECT COALESCE(SUM(amount), 0) AS reimbursed FROM expense_reimbursements WHERE expense_id = ?",
                     (expense_id,))

    wp_paid     = float(pay_row["wp_paid"])     if pay_row else 0.0
    client_paid = float(pay_row["client_paid"]) if pay_row else 0.0
    total_paid  = float(pay_row["total_paid"])  if pay_row else 0.0
    reimbursed  = float(reimb_row["reimbursed"]) if reimb_row else 0.0

    # WP fronted money for client = WP paid more than their own wp_amount share
    wp_fronted_for_client = max(0.0, wp_paid - wp_amount)
    reimburse_owed = max(0.0, wp_fronted_for_client - reimbursed)

    return {
        "wp_paid":            wp_paid,
        "client_paid":        client_paid,
        "total_paid":         total_paid,
        "wp_unpaid":          max(0.0, wp_amount - wp_paid),
        "client_unpaid":      max(0.0, client_amount - client_paid),
        "reimbursed":         reimbursed,
        "wp_fronted_for_client": wp_fronted_for_client,
        "reimburse_owed":     reimburse_owed,
    }


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def get_unmatched_checks() -> list[dict]:
    return _rows(get_db(),
                 "SELECT * FROM checks WHERE claim_id IS NULL ORDER BY check_date DESC, id DESC")

def get_checks_for_claim(claim_id: int) -> list[dict]:
    return _rows(get_db(),
                 "SELECT * FROM checks WHERE claim_id = ? ORDER BY check_date",
                 (claim_id,))

def match_check_to_claim(check_id: int, claim_id: int) -> None:
    get_db().execute("UPDATE checks SET claim_id = ? WHERE id = ?", (claim_id, check_id))
    get_db().commit()

def get_all_claims_simple() -> list[dict]:
    return _rows(get_db(),
                 "SELECT id, job_number, insured_name, claim_number FROM claims ORDER BY insured_name")


# ---------------------------------------------------------------------------
# Invoice Payments
# ---------------------------------------------------------------------------

def get_invoice_payments(tx_id: int) -> list[dict]:
    return _rows(get_db(), """
        SELECT p.*,
               lt.sequence_number AS linked_tx_seq
        FROM invoice_payments p
        LEFT JOIN transactions lt ON lt.id = p.linked_tx_id
        WHERE p.transaction_id = ?
        ORDER BY p.date, p.id
    """, (tx_id,))

def get_invoice_payment(pid: int) -> dict | None:
    row = _row(get_db(), "SELECT * FROM invoice_payments WHERE id = ?", (pid,))
    return dict(row) if row else None

def create_invoice_payment(tx_id: int, data: dict) -> int:
    now = _now()
    cur = get_db().execute(
        """INSERT INTO invoice_payments
               (transaction_id, date, amount, method, reference, linked_tx_id, notes, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            tx_id,
            data.get("date", "") or "",
            _f(data.get("amount")),
            data.get("method", "") or "",
            data.get("reference", "") or "",
            data.get("linked_tx_id") or None,
            data.get("notes", "") or "",
            now,
        ),
    )
    get_db().commit()
    _apply_payment_side_effects(tx_id)
    return cur.lastrowid

def delete_invoice_payment(pid: int) -> None:
    row = _row(get_db(), "SELECT transaction_id FROM invoice_payments WHERE id = ?", (pid,))
    if not row:
        return
    tx_id = row["transaction_id"]
    get_db().execute("DELETE FROM invoice_payments WHERE id = ?", (pid,))
    get_db().commit()
    _apply_payment_side_effects(tx_id)

def _apply_payment_side_effects(tx_id: int) -> None:
    """Sync total_collected and auto-recoup linked deferred transactions."""
    db = get_db()
    # Sync total_collected on the invoice transaction
    db.execute(
        "UPDATE transactions SET total_collected = "
        "(SELECT COALESCE(SUM(amount),0) FROM invoice_payments WHERE transaction_id=?) "
        "WHERE id=?",
        (tx_id, tx_id),
    )
    db.commit()

    tx = get_transaction(tx_id)
    if not tx:
        return

    paid_row = _row(db, "SELECT COALESCE(SUM(amount),0) AS paid FROM invoice_payments WHERE transaction_id=?", (tx_id,))
    total_paid = float(paid_row["paid"]) if paid_row else 0.0
    invoice_amount = float(tx.get("amount") or 0)
    is_fully_paid = invoice_amount > 0 and total_paid >= invoice_amount - 0.005

    links = _rows(db, "SELECT deferred_tx_id FROM invoice_deferred_links WHERE invoice_tx_id=?", (tx_id,))
    for link in links:
        dtx_id = link["deferred_tx_id"]
        dtx = _row(db, "SELECT deferred, recouped FROM transactions WHERE id=?", (dtx_id,))
        if not dtx:
            continue
        d = float(dtx["deferred"] or 0)
        r = float(dtx["recouped"] or 0)
        # If deferred is only tracked in disbursements (transactions.deferred == 0), use that sum
        if d == 0:
            disb_row = _row(db, "SELECT COALESCE(SUM(fee_deferred),0) AS dd FROM disbursements WHERE transaction_id=?", (dtx_id,))
            d = float(disb_row["dd"]) if disb_row else 0.0
        if is_fully_paid:
            if d > 0:
                db.execute("UPDATE transactions SET recouped=? WHERE id=?", (d, dtx_id))
        else:
            # Rollback only if we auto-set it (recouped == deferred and deferred > 0)
            if d > 0 and abs(r - d) < 0.005:
                db.execute("UPDATE transactions SET recouped=0 WHERE id=?", (dtx_id,))
    db.commit()


# ---------------------------------------------------------------------------
# Invoice Deferred Links
# ---------------------------------------------------------------------------

def get_invoice_deferred_links(invoice_tx_id: int) -> list[dict]:
    return _rows(get_db(), """
        SELECT idl.*,
               t.sequence_number, t.type, t.date, t.amount,
               COALESCE(d.disburse_fee_deferred, t.deferred) AS deferred
        FROM invoice_deferred_links idl
        JOIN transactions t ON t.id = idl.deferred_tx_id
        LEFT JOIN (
            SELECT transaction_id, SUM(fee_deferred) AS disburse_fee_deferred
            FROM disbursements
            GROUP BY transaction_id
        ) d ON d.transaction_id = t.id
        WHERE idl.invoice_tx_id = ?
        ORDER BY t.sequence_number, t.id
    """, (invoice_tx_id,))

def create_invoice_deferred_link(invoice_tx_id: int, deferred_tx_id: int) -> int:
    now = _now()
    cur = get_db().execute(
        "INSERT INTO invoice_deferred_links (invoice_tx_id, deferred_tx_id, created_at) VALUES (?, ?, ?)",
        (invoice_tx_id, deferred_tx_id, now),
    )
    get_db().commit()
    return cur.lastrowid

def delete_invoice_deferred_link(link_id: int) -> None:
    get_db().execute("DELETE FROM invoice_deferred_links WHERE id = ?", (link_id,))
    get_db().commit()


# ---------------------------------------------------------------------------
# Helper for dropdowns
# ---------------------------------------------------------------------------

def get_fee_invoices(claim_id: int) -> list[dict]:
    """Return non-void Fee Invoice transactions for a claim (for proc_fee_invoice_id dropdown)."""
    return _rows(get_db(), """
        SELECT id, sequence_number, invoice_number, amount
        FROM transactions
        WHERE claim_id = ? AND LOWER(type) = 'fee invoice'
          AND (void IS NULL OR void = 0)
        ORDER BY sequence_number, id
    """, (claim_id,))


def get_claim_transactions_for_select(claim_id: int, exclude_tx_id: int | None = None) -> list[dict]:
    """Return non-void transactions for dropdown usage, excluding the invoice itself.

    Uses disbursement-computed deferred when disbursements exist (same logic as get_transactions),
    so the deferred fee filter in the template works regardless of how deferred was recorded.
    """
    sql = """
        SELECT t.id, t.sequence_number, t.type, t.date, t.amount,
               COALESCE(d.disburse_fee_deferred, t.deferred) AS deferred
        FROM transactions t
        LEFT JOIN (
            SELECT transaction_id,
                   SUM(fee_deferred) AS disburse_fee_deferred
            FROM disbursements
            GROUP BY transaction_id
        ) d ON d.transaction_id = t.id
        WHERE t.claim_id = ?
          AND (t.void IS NULL OR t.void = 0)
    """
    params: list = [claim_id]
    if exclude_tx_id is not None:
        sql += " AND t.id != ?"
        params.append(exclude_tx_id)
    sql += " ORDER BY t.sequence_number, t.id"
    return _rows(get_db(), sql, params)
