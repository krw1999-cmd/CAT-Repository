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
    split_pct       REAL DEFAULT 0
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
]

ASSIGNEE_ROLES = ["firm", "sales", "adjuster", "referrer", "other"]

TRANSACTION_TYPES = [
    "Escrow", "Escrow Endorsed", "Carrier", "Draw",
    "Fee Invoice", "Waypoint Expense", "Client Expense", "VOID", "Other",
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
    conn.commit()
    conn.close()


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
               COALESCE(SUM(t.total_collected), 0) AS total_collected_sum
        FROM claims c
        LEFT JOIN transactions t ON t.claim_id = c.id
        GROUP BY c.id
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
                               contract_pct, created_at, updated_at)
           VALUES (:job_number, :claim_number, :insured_name, :carrier,
                   :contract_pct, :now, :now)""",
        {**data, "now": now},
    )
    get_db().commit()
    return cur.lastrowid

def update_claim(claim_id: int, data: dict) -> None:
    get_db().execute(
        """UPDATE claims SET job_number=:job_number, claim_number=:claim_number,
                             insured_name=:insured_name, carrier=:carrier,
                             contract_pct=:contract_pct, updated_at=:now
           WHERE id = :id""",
        {**data, "now": _now(), "id": claim_id},
    )
    get_db().commit()


# ---------------------------------------------------------------------------
# Policy Limits
# ---------------------------------------------------------------------------

def get_limits(claim_id: int) -> list[dict]:
    return _rows(get_db(),
                 "SELECT * FROM policy_limits WHERE claim_id = ? ORDER BY id",
                 (claim_id,))

def get_limit(limit_id: int) -> dict | None:
    row = _row(get_db(), "SELECT * FROM policy_limits WHERE id = ?", (limit_id,))
    return dict(row) if row else None

def create_limit(claim_id: int, data: dict) -> int:
    cur = get_db().execute(
        """INSERT INTO policy_limits (claim_id, coverage_type, base_limit,
                                      extended_limit, paid, remaining)
           VALUES (:claim_id, :coverage_type, :base_limit,
                   :extended_limit, :paid, :remaining)""",
        {"claim_id": claim_id, **data},
    )
    get_db().commit()
    return cur.lastrowid

def update_limit(limit_id: int, data: dict) -> None:
    get_db().execute(
        """UPDATE policy_limits SET coverage_type=:coverage_type,
                                    base_limit=:base_limit,
                                    extended_limit=:extended_limit,
                                    paid=:paid, remaining=:remaining
           WHERE id = :id""",
        {**data, "id": limit_id},
    )
    get_db().commit()

def delete_limit(limit_id: int) -> None:
    get_db().execute("DELETE FROM policy_limits WHERE id = ?", (limit_id,))
    get_db().commit()


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------

def get_transactions(claim_id: int) -> list[dict]:
    """Return transactions with disbursement-computed summary columns."""
    sql = """
        SELECT t.*,
               COALESCE(d.net_to_insured, 0)      AS net_to_insured,
               COALESCE(d.to_vendors, 0)           AS to_vendors,
               COALESCE(d.disburse_fee_owed, 0)    AS disburse_fee_owed,
               COALESCE(d.disburse_fee_collected, 0) AS disburse_fee_collected,
               COALESCE(d.disburse_fee_deferred, 0)  AS disburse_fee_deferred,
               COALESCE(d.disburse_fee_recouped, 0)  AS disburse_fee_recouped,
               CASE WHEN d.transaction_id IS NOT NULL THEN 1 ELSE 0 END AS has_disbursements
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
        WHERE t.claim_id = ?
        ORDER BY t.sequence_number, t.id
    """
    return _rows(get_db(), sql, (claim_id,))

def get_transaction(tx_id: int) -> dict | None:
    row = _row(get_db(), "SELECT * FROM transactions WHERE id = ?", (tx_id,))
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
                linked_escrow_id,
                created_at, updated_at)
           VALUES
               (:claim_id, :seq, :date, :amount, :type,
                :fee_pct, :fee_owed, :balance, :deferred, :recouped,
                :fee_collected, :reimbursed, :total_collected,
                :unpaid_payee_expense, :outstanding_expense,
                :ott, :notes, :check_id,
                :check_number, :received_date, :payer, :payees_text, :endorsed, :void,
                :linked_escrow_id,
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
            s["total_fee_owed"]      += t.get("disburse_fee_owed") or 0.0
            s["total_fee_collected"] += t.get("disburse_fee_collected") or 0.0
            s["total_deferred"]      += t.get("disburse_fee_deferred") or 0.0
            s["total_recouped"]      += t.get("disburse_fee_recouped") or 0.0
            s["net_to_insured"]      += t.get("net_to_insured") or 0.0
            s["to_vendors"]          += t.get("to_vendors") or 0.0
        else:
            s["total_fee_owed"]      += t.get("fee_owed") or 0.0
            s["total_fee_collected"] += t.get("fee_collected") or 0.0
            s["total_deferred"]      += t.get("deferred") or 0.0
            s["total_recouped"]      += t.get("recouped") or 0.0

        s["total_reimbursed"]        += t.get("reimbursed") or 0.0
        s["total_collected"]         += t.get("total_collected") or 0.0
        s["total_outstanding_expense"] += t.get("outstanding_expense") or 0.0
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
        """INSERT INTO assignees (claim_id, role, name, split_pct, sort_order)
           VALUES (:claim_id, :role, :name, :split_pct, :sort_order)""",
        {
            "claim_id": claim_id,
            "role": data.get("role", "other"),
            "name": data.get("name", ""),
            "split_pct": _f(data.get("split_pct")),
            "sort_order": int(data.get("sort_order") or 0),
        },
    )
    get_db().commit()
    return cur.lastrowid

def update_assignee(assignee_id: int, data: dict) -> None:
    get_db().execute(
        """UPDATE assignees SET role=:role, name=:name,
                                split_pct=:split_pct, sort_order=:sort_order
           WHERE id = :id""",
        {
            "role": data.get("role", "other"),
            "name": data.get("name", ""),
            "split_pct": _f(data.get("split_pct")),
            "sort_order": int(data.get("sort_order") or 0),
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
        """INSERT INTO transaction_splits (transaction_id, role, name, split_pct)
           VALUES (:tx_id, :role, :name, :split_pct)""",
        {
            "tx_id": tx_id,
            "role": data.get("role", "other"),
            "name": data.get("name", ""),
            "split_pct": _f(data.get("split_pct")),
        },
    )
    get_db().commit()
    return cur.lastrowid

def update_split(split_id: int, data: dict) -> None:
    get_db().execute(
        """UPDATE transaction_splits SET role=:role, name=:name, split_pct=:split_pct
           WHERE id = :id""",
        {
            "role": data.get("role", "other"),
            "name": data.get("name", ""),
            "split_pct": _f(data.get("split_pct")),
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

def calc_split_amounts(split: dict, disburse_totals: dict) -> dict:
    """Return a split dict enriched with calculated dollar amounts from disbursement totals."""
    pct = (split.get("split_pct") or 0) / 100.0
    fee_owed = disburse_totals.get("fee_owed") or 0
    deferred  = disburse_totals.get("fee_deferred") or 0
    recouped  = disburse_totals.get("fee_recouped") or 0
    return {
        **split,
        "fee_amount":      round(fee_owed  * pct, 2),
        "deferred_amount": round(deferred  * pct, 2),
        "recouped_amount": round(recouped  * pct, 2),
    }

def get_splits_with_amounts(tx_id: int) -> list[dict]:
    totals = get_disbursement_totals(tx_id)
    return [calc_split_amounts(s, totals) for s in get_splits(tx_id)]


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
               updated_at=:now
           WHERE id = :id""",
        {
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
# Expenses
# ---------------------------------------------------------------------------

def get_expenses(claim_id: int) -> list[dict]:
    return _rows(get_db(),
                 "SELECT * FROM expenses WHERE claim_id = ? ORDER BY invoice_date, id",
                 (claim_id,))

def get_expense(exp_id: int) -> dict | None:
    row = _row(get_db(), "SELECT * FROM expenses WHERE id = ?", (exp_id,))
    return dict(row) if row else None

def create_expense(claim_id: int, data: dict) -> int:
    now = _now()
    cur = get_db().execute(
        """INSERT INTO expenses (claim_id, invoice_date, payee_name, invoice_amount,
                                  responsible_party, unpaid_to_payee,
                                  client_outstanding, wp_outstanding,
                                  created_at, updated_at)
           VALUES (:claim_id, :invoice_date, :payee_name, :invoice_amount,
                   :responsible_party, :unpaid_to_payee,
                   :client_outstanding, :wp_outstanding, :now, :now)""",
        {
            "claim_id": claim_id,
            "invoice_date": data.get("invoice_date", ""),
            "payee_name": data.get("payee_name", ""),
            "invoice_amount": _f(data.get("invoice_amount")),
            "responsible_party": data.get("responsible_party", ""),
            "unpaid_to_payee": _f(data.get("unpaid_to_payee")),
            "client_outstanding": _f(data.get("client_outstanding")),
            "wp_outstanding": _f(data.get("wp_outstanding")),
            "now": now,
        },
    )
    get_db().commit()
    return cur.lastrowid

def update_expense(exp_id: int, data: dict) -> None:
    get_db().execute(
        """UPDATE expenses SET invoice_date=:invoice_date, payee_name=:payee_name,
                               invoice_amount=:invoice_amount,
                               responsible_party=:responsible_party,
                               unpaid_to_payee=:unpaid_to_payee,
                               client_outstanding=:client_outstanding,
                               wp_outstanding=:wp_outstanding,
                               updated_at=:now
           WHERE id = :id""",
        {
            "invoice_date": data.get("invoice_date", ""),
            "payee_name": data.get("payee_name", ""),
            "invoice_amount": _f(data.get("invoice_amount")),
            "responsible_party": data.get("responsible_party", ""),
            "unpaid_to_payee": _f(data.get("unpaid_to_payee")),
            "client_outstanding": _f(data.get("client_outstanding")),
            "wp_outstanding": _f(data.get("wp_outstanding")),
            "now": _now(),
            "id": exp_id,
        },
    )
    get_db().commit()

def delete_expense(exp_id: int) -> None:
    get_db().execute("DELETE FROM expenses WHERE id = ?", (exp_id,))
    get_db().commit()


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
