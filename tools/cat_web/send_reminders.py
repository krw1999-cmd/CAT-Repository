from __future__ import annotations

"""
send_reminders.py — Email follow-up reminders for status page items.

Run daily via cron. Sends one email per status item whose follow_up_date == today.

Setup:
  1. Copy .env.example to .env and fill in credentials.
  2. Add to crontab: 0 8 * * * /usr/bin/python3 /path/to/send_reminders.py
"""

import os
import smtplib
import sqlite3
from datetime import date
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

# ---------------------------------------------------------------------------
# Config / credentials
# ---------------------------------------------------------------------------

_SCRIPT_DIR = Path(__file__).parent
_ENV_FILE = _SCRIPT_DIR / ".env"
_DB_PATH = _SCRIPT_DIR / "cat.db"


def _load_env() -> dict:
    """Parse key=value pairs from .env file."""
    env: dict = {}
    if not _ENV_FILE.exists():
        raise FileNotFoundError(f".env file not found at {_ENV_FILE}. "
                                f"Copy .env.example to .env and fill in credentials.")
    for line in _ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            env[key.strip()] = val.strip()
    return env


# ---------------------------------------------------------------------------
# Query due reminders from DB
# ---------------------------------------------------------------------------

def _get_due_items(conn: sqlite3.Connection, today: str) -> list[dict]:
    """Return all status items with follow_up_date = today that are still unresolved."""
    conn.row_factory = sqlite3.Row
    items: list[dict] = []

    # 1. Pending Checks
    rows = conn.execute("""
        SELECT 'Pending Check' AS section,
               c.insured_name, c.job_number, c.id AS claim_id, t.id AS tx_id,
               t.check_number, t.amount, t.type, t.date AS check_date
        FROM transactions t
        JOIN claims c ON c.id = t.claim_id
        WHERE t.follow_up_date = ?
          AND (t.void IS NULL OR t.void = 0)
          AND LOWER(TRIM(t.type)) IN ('carrier', 'draw', 'escrow endorsed')
          AND (
            EXISTS (
                SELECT 1 FROM disbursements d
                WHERE d.transaction_id = t.id
                  AND d.fee_applies = 1
                  AND (d.fee_owed - COALESCE(d.fee_deferred, 0) + COALESCE(d.fee_recouped, 0)) > 0.005
                  AND d.fee_collected < (d.fee_owed - COALESCE(d.fee_deferred, 0) + COALESCE(d.fee_recouped, 0)) - 0.005
            )
            OR EXISTS (
                SELECT 1 FROM disbursements d
                WHERE d.transaction_id = t.id
                  AND d.client_received = 0
                  AND d.amount > 0.005
            )
          )
    """, (today,)).fetchall()
    for r in rows:
        items.append({
            "section": "Pending Check",
            "insured": r["insured_name"],
            "job_number": r["job_number"] or "",
            "detail": (f"Check #{r['check_number'] or '?'}  |  "
                       f"{r['type']}  |  "
                       f"${r['amount']:,.2f}  |  {r['check_date'] or ''}"),
            "claim_id": r["claim_id"],
            "ref_id": r["tx_id"],
        })

    # 2. Pending Fee Invoices
    rows = conn.execute("""
        SELECT c.insured_name, c.job_number, c.id AS claim_id, t.id AS tx_id,
               t.invoice_number, t.amount,
               COALESCE(t.total_collected, 0) AS total_collected,
               t.amount - COALESCE(t.total_collected, 0) AS balance_due
        FROM transactions t
        JOIN claims c ON c.id = t.claim_id
        WHERE t.follow_up_date = ?
          AND LOWER(TRIM(t.type)) = 'fee invoice'
          AND (t.void IS NULL OR t.void = 0)
          AND t.amount - COALESCE(t.total_collected, 0) > 0.005
    """, (today,)).fetchall()
    for r in rows:
        items.append({
            "section": "Pending Fee Invoice",
            "insured": r["insured_name"],
            "job_number": r["job_number"] or "",
            "detail": (f"Invoice #{r['invoice_number'] or '?'}  |  "
                       f"Balance due: ${r['balance_due']:,.2f}  |  "
                       f"Invoice total: ${r['amount']:,.2f}"),
            "claim_id": r["claim_id"],
            "ref_id": r["tx_id"],
        })

    # 3. Open Escrow
    rows = conn.execute("""
        WITH escrowed AS (
            SELECT c.id AS claim_id, c.job_number, c.insured_name,
                   COALESCE(SUM(CASE WHEN LOWER(TRIM(t.type))='escrow'
                                     THEN t.amount ELSE 0 END), 0) AS total_escrowed,
                   COALESCE(SUM(CASE WHEN LOWER(TRIM(t.type)) IN ('draw','escrow endorsed')
                                     THEN t.amount ELSE 0 END), 0) AS total_drawn
            FROM claims c
            LEFT JOIN transactions t ON t.claim_id = c.id
                AND (t.void IS NULL OR t.void = 0)
            WHERE c.follow_up_date = ?
            GROUP BY c.id
            HAVING total_escrowed - total_drawn > 0.005
        )
        SELECT * FROM escrowed
    """, (today,)).fetchall()
    for r in rows:
        net = (r["total_escrowed"] or 0) - (r["total_drawn"] or 0)
        items.append({
            "section": "Open Escrow",
            "insured": r["insured_name"],
            "job_number": r["job_number"] or "",
            "detail": f"Net in escrow: ${net:,.2f}",
            "claim_id": r["claim_id"],
            "ref_id": None,
        })

    # 4. Unrecouped Deferred Fees
    rows = conn.execute("""
        WITH d_agg AS (
            SELECT transaction_id,
                   SUM(fee_deferred) AS fee_deferred,
                   SUM(fee_recouped) AS fee_recouped
            FROM disbursements GROUP BY transaction_id
        ),
        tx_agg AS (
            SELECT t.claim_id,
                   SUM(CASE WHEN d.fee_deferred IS NOT NULL
                            THEN d.fee_deferred
                            ELSE COALESCE(t.deferred, 0) END) AS total_deferred,
                   SUM(CASE WHEN COALESCE(d.fee_recouped, 0) > COALESCE(t.recouped, 0)
                            THEN COALESCE(d.fee_recouped, 0)
                            ELSE COALESCE(t.recouped, 0) END) AS total_recouped
            FROM transactions t
            LEFT JOIN d_agg d ON d.transaction_id = t.id
            WHERE (t.void IS NULL OR t.void = 0)
            GROUP BY t.claim_id
        )
        SELECT c.id AS claim_id, c.job_number, c.insured_name,
               COALESCE(ta.total_deferred, 0) AS total_deferred,
               COALESCE(ta.total_recouped, 0) AS total_recouped,
               COALESCE(ta.total_deferred, 0) - COALESCE(ta.total_recouped, 0) AS unrecouped
        FROM claims c
        LEFT JOIN tx_agg ta ON ta.claim_id = c.id
        WHERE c.follow_up_date = ?
          AND COALESCE(ta.total_deferred, 0) - COALESCE(ta.total_recouped, 0) > 0.005
    """, (today,)).fetchall()
    for r in rows:
        items.append({
            "section": "Unrecouped Deferred Fees",
            "insured": r["insured_name"],
            "job_number": r["job_number"] or "",
            "detail": (f"Deferred: ${r['total_deferred']:,.2f}  |  "
                       f"Recouped: ${r['total_recouped']:,.2f}  |  "
                       f"Outstanding: ${r['unrecouped']:,.2f}"),
            "claim_id": r["claim_id"],
            "ref_id": None,
        })

    # 5. Pending Expenses
    rows = conn.execute("""
        SELECT e.id AS exp_id, e.invoice_date, e.payee_name, e.invoice_amount,
               e.responsible_party, e.reason,
               c.id AS claim_id, c.insured_name, c.job_number,
               e.invoice_amount - COALESCE(p.total_paid, 0) AS outstanding
        FROM expenses e
        JOIN claims c ON c.id = e.claim_id
        LEFT JOIN (
            SELECT expense_id, COALESCE(SUM(amount), 0) AS total_paid
            FROM expense_payments GROUP BY expense_id
        ) p ON p.expense_id = e.id
        WHERE e.follow_up_date = ?
          AND (e.invoice_amount - COALESCE(p.total_paid, 0) > 0.005)
    """, (today,)).fetchall()
    for r in rows:
        items.append({
            "section": "Pending Expense",
            "insured": r["insured_name"],
            "job_number": r["job_number"] or "",
            "detail": (f"Payee: {r['payee_name'] or '?'}  |  "
                       f"${r['invoice_amount']:,.2f}  |  "
                       f"Reason: {r['reason'] or '—'}"),
            "claim_id": r["claim_id"],
            "ref_id": r["exp_id"],
        })

    return items


# ---------------------------------------------------------------------------
# Send email
# ---------------------------------------------------------------------------

def _send_email(smtp_host: str, smtp_port: int, user: str, password: str,
                to_addr: str, subject: str, body: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to_addr
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.ehlo()
        server.starttls()
        server.login(user, password)
        server.sendmail(user, to_addr, msg.as_string())


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    env = _load_env()
    gmail_user = env.get("GMAIL_USER", "")
    gmail_password = env.get("GMAIL_APP_PASSWORD", "")
    reminder_to = env.get("REMINDER_TO", gmail_user)
    smtp_host = env.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(env.get("SMTP_PORT", "587"))

    if not gmail_user or not gmail_password:
        raise ValueError("GMAIL_USER and GMAIL_APP_PASSWORD must be set in .env")

    today_str = date.today().isoformat()  # e.g. "2026-02-23"
    conn = sqlite3.connect(str(_DB_PATH))

    try:
        items = _get_due_items(conn, today_str)
    finally:
        conn.close()

    if not items:
        print(f"[{today_str}] No follow-up reminders due today.")
        return

    print(f"[{today_str}] Sending {len(items)} reminder(s)...")

    sent = 0
    errors = 0
    for item in items:
        job_part = f" ({item['job_number']})" if item['job_number'] else ""
        subject = f"[CAT Follow-up] {item['section']} — {item['insured']}{job_part}"
        body = (
            f"Follow-up reminder for: {today_str}\n\n"
            f"Section:  {item['section']}\n"
            f"Insured:  {item['insured']}{job_part}\n"
            f"Details:  {item['detail']}\n\n"
            f"Log in to review: http://localhost:5001/status\n"
        )
        try:
            _send_email(smtp_host, smtp_port, gmail_user, gmail_password,
                        reminder_to, subject, body)
            print(f"  ✓  {subject}")
            sent += 1
        except Exception as exc:
            print(f"  ✗  Failed — {subject}: {exc}")
            errors += 1

    print(f"Done: {sent} sent, {errors} failed.")


if __name__ == "__main__":
    main()
