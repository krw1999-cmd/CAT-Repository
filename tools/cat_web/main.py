from __future__ import annotations

"""CAT Web — Flask application entry point and route definitions."""

from flask import (
    Flask,
    g,
    redirect,
    render_template,
    request,
    url_for,
    flash,
    abort,
    jsonify,
)
from flask_login import login_required, login_user, logout_user, current_user

import auth
import config
import db as db_module

app = Flask(__name__)
app.secret_key = config.SECRET_KEY
app.config["DEBUG"] = config.DEBUG

auth.init_app(app)
app.teardown_appcontext(db_module.close_db)

TRANSACTION_TYPES = db_module.TRANSACTION_TYPES
COVERAGE_TYPES = db_module.COVERAGE_TYPES
ASSIGNEE_ROLES = db_module.ASSIGNEE_ROLES


@app.template_filter("money")
def money_filter(value):
    try:
        return "${:,.2f}".format(float(value or 0))
    except (TypeError, ValueError):
        return "$0.00"

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("claims_list"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user_row = db_module.get_user_by_username(username)
        if user_row and auth.check_password(user_row["password_hash"], password):
            user = auth.User(user_row["id"], user_row["username"])
            login_user(user, remember=True)
            return redirect(request.args.get("next") or url_for("claims_list"))
        flash("Invalid username or password.", "error")
    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Claims dashboard
# ---------------------------------------------------------------------------

@app.route("/")
@login_required
def claims_list():
    claims = db_module.get_all_claims()
    return render_template("claims.html", claims=claims)


@app.route("/claims/new", methods=["GET", "POST"])
@login_required
def new_claim():
    if request.method == "POST":
        data = {
            "job_number": request.form.get("job_number", "").strip(),
            "claim_number": request.form.get("claim_number", "").strip(),
            "insured_name": request.form.get("insured_name", "").strip(),
            "carrier": request.form.get("carrier", "").strip(),
            "contract_pct": db_module._f(request.form.get("contract_pct")),
        }
        claim_id = db_module.create_claim(data)
        return redirect(url_for("claim_detail", claim_id=claim_id))
    return render_template("new_claim.html")


# ---------------------------------------------------------------------------
# Claim detail
# ---------------------------------------------------------------------------

@app.route("/claims/<int:claim_id>")
@login_required
def claim_detail(claim_id: int):
    claim = db_module.get_claim(claim_id)
    if not claim:
        abort(404)
    limits = db_module.get_limits(claim_id)
    transactions = db_module.get_transactions(claim_id)
    expenses = db_module.get_expenses(claim_id)
    assignees = db_module.get_assignees(claim_id)
    summary = db_module.get_transaction_summary(claim_id)
    open_escrows = db_module.get_open_escrows(claim_id)
    return render_template(
        "claim.html",
        claim=claim,
        limits=limits,
        transactions=transactions,
        expenses=expenses,
        assignees=assignees,
        summary=summary,
        open_escrows=open_escrows,
        tx_types=TRANSACTION_TYPES,
        coverage_types=COVERAGE_TYPES,
    )


# -- Claim header edit -------------------------------------------------------

@app.route("/claims/<int:claim_id>/header")
@login_required
def claim_header_view(claim_id: int):
    claim = db_module.get_claim(claim_id)
    if not claim:
        abort(404)
    return render_template("partials/_claim_header.html", claim=claim)


@app.route("/claims/<int:claim_id>/header/edit")
@login_required
def claim_header_edit(claim_id: int):
    claim = db_module.get_claim(claim_id)
    if not claim:
        abort(404)
    return render_template("partials/_claim_header_edit.html", claim=claim)


@app.route("/claims/<int:claim_id>/header", methods=["POST"])
@login_required
def claim_header_save(claim_id: int):
    claim = db_module.get_claim(claim_id)
    if not claim:
        abort(404)
    data = {
        "job_number": request.form.get("job_number", "").strip(),
        "claim_number": request.form.get("claim_number", "").strip(),
        "insured_name": request.form.get("insured_name", "").strip(),
        "carrier": request.form.get("carrier", "").strip(),
        "contract_pct": db_module._f(request.form.get("contract_pct")),
    }
    db_module.update_claim(claim_id, data)
    claim = db_module.get_claim(claim_id)
    return render_template("partials/_claim_header.html", claim=claim)


# ---------------------------------------------------------------------------
# Policy Limits — HTMX fragments
# ---------------------------------------------------------------------------

@app.route("/claims/<int:claim_id>/limits/new-row")
@login_required
def limit_new_row(claim_id: int):
    claim = db_module.get_claim(claim_id)
    return render_template("partials/_limit_edit.html",
                           claim=claim,
                           limit=None,
                           coverage_types=COVERAGE_TYPES)


@app.route("/claims/<int:claim_id>/limits", methods=["POST"])
@login_required
def limit_create(claim_id: int):
    data = _limit_data_from_form()
    limit_id = db_module.create_limit(claim_id, data)
    limit = db_module.get_limit(limit_id)
    claim = db_module.get_claim(claim_id)
    row_html = render_template("partials/_limit_row.html", claim=claim, limit=limit)
    placeholder = '<tr id="limit-new-row-placeholder"></tr>'
    return row_html + placeholder


@app.route("/claims/<int:claim_id>/limits/<int:limit_id>/edit")
@login_required
def limit_edit_row(claim_id: int, limit_id: int):
    claim = db_module.get_claim(claim_id)
    limit = db_module.get_limit(limit_id)
    if not limit:
        abort(404)
    return render_template("partials/_limit_edit.html",
                           claim=claim,
                           limit=limit,
                           coverage_types=COVERAGE_TYPES)


@app.route("/claims/<int:claim_id>/limits/<int:limit_id>", methods=["PUT"])
@login_required
def limit_update(claim_id: int, limit_id: int):
    data = _limit_data_from_form()
    db_module.update_limit(limit_id, data)
    limit = db_module.get_limit(limit_id)
    claim = db_module.get_claim(claim_id)
    return render_template("partials/_limit_row.html", claim=claim, limit=limit)


@app.route("/claims/<int:claim_id>/limits/<int:limit_id>", methods=["DELETE"])
@login_required
def limit_delete(claim_id: int, limit_id: int):
    db_module.delete_limit(limit_id)
    return ""


def _limit_data_from_form() -> dict:
    return {
        "coverage_type": request.form.get("coverage_type", "").strip(),
        "base_limit": db_module._f(request.form.get("base_limit")),
        "extended_limit": db_module._f(request.form.get("extended_limit")),
        "paid": db_module._f(request.form.get("paid")),
        "remaining": db_module._f(request.form.get("remaining")),
    }


# ---------------------------------------------------------------------------
# Transactions — HTMX fragments
# ---------------------------------------------------------------------------

@app.route("/claims/<int:claim_id>/transactions/new-row")
@login_required
def tx_new_row(claim_id: int):
    claim = db_module.get_claim(claim_id)
    open_escrows = db_module.get_open_escrows(claim_id)
    return render_template("partials/_tx_edit.html",
                           claim=claim,
                           tx=None,
                           tx_types=TRANSACTION_TYPES,
                           open_escrows=open_escrows)


@app.route("/claims/<int:claim_id>/transactions", methods=["POST"])
@login_required
def tx_create(claim_id: int):
    data = _tx_data_from_form()
    tx_id = db_module.create_transaction(claim_id, data)
    tx = db_module.get_transactions(claim_id)  # use joined version for display
    tx_row = next((t for t in tx if t["id"] == tx_id), None)
    claim = db_module.get_claim(claim_id)
    summary = db_module.get_transaction_summary(claim_id)
    if tx_row is None:
        tx_row = db_module.get_transaction(tx_id)
    row_html = render_template("partials/_tx_row.html", claim=claim, tx=tx_row,
                               tx_types=TRANSACTION_TYPES)
    placeholder = '<tr id="tx-new-row-placeholder"></tr>'
    summary_html = render_template("partials/_summary.html", summary=summary)
    return row_html + placeholder + summary_html


@app.route("/claims/<int:claim_id>/transactions/<int:tx_id>/edit")
@login_required
def tx_edit_row(claim_id: int, tx_id: int):
    claim = db_module.get_claim(claim_id)
    tx = db_module.get_transaction(tx_id)
    if not tx:
        abort(404)
    open_escrows = db_module.get_open_escrows(claim_id)
    return render_template("partials/_tx_edit.html",
                           claim=claim,
                           tx=tx,
                           tx_types=TRANSACTION_TYPES,
                           open_escrows=open_escrows)


@app.route("/claims/<int:claim_id>/transactions/<int:tx_id>", methods=["PUT"])
@login_required
def tx_update(claim_id: int, tx_id: int):
    data = _tx_data_from_form()
    db_module.update_transaction(tx_id, data)
    txs = db_module.get_transactions(claim_id)
    tx = next((t for t in txs if t["id"] == tx_id), None)
    if tx is None:
        tx = db_module.get_transaction(tx_id)
    claim = db_module.get_claim(claim_id)
    summary = db_module.get_transaction_summary(claim_id)
    row_html = render_template("partials/_tx_row.html", claim=claim, tx=tx,
                               tx_types=TRANSACTION_TYPES)
    summary_html = render_template("partials/_summary.html", summary=summary)
    return row_html + summary_html


@app.route("/claims/<int:claim_id>/transactions/<int:tx_id>", methods=["DELETE"])
@login_required
def tx_delete(claim_id: int, tx_id: int):
    claim_id_check = db_module.get_transaction(tx_id)
    if not claim_id_check:
        abort(404)
    db_module.delete_transaction(tx_id)
    summary = db_module.get_transaction_summary(claim_id)
    return render_template("partials/_summary.html", summary=summary)


def _tx_data_from_form() -> dict:
    return {
        "sequence_number":     request.form.get("sequence_number") or None,
        "date":                request.form.get("date", "").strip(),
        "amount":              db_module._f(request.form.get("amount")),
        "type":                request.form.get("type", "").strip(),
        "fee_owed":            db_module._f(request.form.get("fee_owed")),
        "balance":             db_module._f(request.form.get("balance")),
        "deferred":            db_module._f(request.form.get("deferred")),
        "recouped":            db_module._f(request.form.get("recouped")),
        "fee_collected":       db_module._f(request.form.get("fee_collected")),
        "reimbursed":          db_module._f(request.form.get("reimbursed")),
        "total_collected":     db_module._f(request.form.get("total_collected")),
        "unpaid_payee_expense":db_module._f(request.form.get("unpaid_payee_expense")),
        "outstanding_expense": db_module._f(request.form.get("outstanding_expense")),
        "ott":                 db_module._fopt(request.form.get("ott")),
        "notes":               request.form.get("notes", "").strip(),
        "check_id":            request.form.get("check_id") or None,
        "linked_escrow_id":    request.form.get("linked_escrow_id") or None,
    }


# ---------------------------------------------------------------------------
# Expenses — HTMX fragments
# ---------------------------------------------------------------------------

@app.route("/claims/<int:claim_id>/expenses/new-row")
@login_required
def exp_new_row(claim_id: int):
    claim = db_module.get_claim(claim_id)
    return render_template("partials/_exp_edit.html", claim=claim, exp=None)


@app.route("/claims/<int:claim_id>/expenses", methods=["POST"])
@login_required
def exp_create(claim_id: int):
    data = _exp_data_from_form()
    exp_id = db_module.create_expense(claim_id, data)
    exp = db_module.get_expense(exp_id)
    claim = db_module.get_claim(claim_id)
    row_html = render_template("partials/_exp_row.html", claim=claim, exp=exp)
    placeholder = '<tr id="exp-new-row-placeholder"></tr>'
    return row_html + placeholder


@app.route("/claims/<int:claim_id>/expenses/<int:exp_id>/edit")
@login_required
def exp_edit_row(claim_id: int, exp_id: int):
    claim = db_module.get_claim(claim_id)
    exp = db_module.get_expense(exp_id)
    if not exp:
        abort(404)
    return render_template("partials/_exp_edit.html", claim=claim, exp=exp)


@app.route("/claims/<int:claim_id>/expenses/<int:exp_id>", methods=["PUT"])
@login_required
def exp_update(claim_id: int, exp_id: int):
    data = _exp_data_from_form()
    db_module.update_expense(exp_id, data)
    exp = db_module.get_expense(exp_id)
    claim = db_module.get_claim(claim_id)
    return render_template("partials/_exp_row.html", claim=claim, exp=exp)


@app.route("/claims/<int:claim_id>/expenses/<int:exp_id>", methods=["DELETE"])
@login_required
def exp_delete(claim_id: int, exp_id: int):
    db_module.delete_expense(exp_id)
    return ""


def _exp_data_from_form() -> dict:
    return {
        "invoice_date": request.form.get("invoice_date", "").strip(),
        "payee_name": request.form.get("payee_name", "").strip(),
        "invoice_amount": db_module._f(request.form.get("invoice_amount")),
        "responsible_party": request.form.get("responsible_party", "").strip(),
        "unpaid_to_payee": db_module._f(request.form.get("unpaid_to_payee")),
        "client_outstanding": db_module._f(request.form.get("client_outstanding")),
        "wp_outstanding": db_module._f(request.form.get("wp_outstanding")),
    }


# ---------------------------------------------------------------------------
# Check queue (Phase 3 bridge)
# ---------------------------------------------------------------------------

@app.route("/checks")
@login_required
def checks_queue():
    checks = db_module.get_unmatched_checks()
    all_claims = db_module.get_all_claims_simple()
    return render_template("checks.html", checks=checks, all_claims=all_claims)


@app.route("/checks/<int:check_id>/match", methods=["POST"])
@login_required
def check_match(check_id: int):
    claim_id = request.form.get("claim_id")
    if not claim_id:
        flash("Select a claim first.", "error")
        return redirect(url_for("checks_queue"))
    claim_id = int(claim_id)
    db_module.match_check_to_claim(check_id, claim_id)

    from db import get_db
    check_row = get_db().execute("SELECT * FROM checks WHERE id = ?", (check_id,)).fetchone()
    if check_row:
        check_row = dict(check_row)
        claim = db_module.get_claim(claim_id)
        pct = (claim["contract_pct"] or 0) / 100.0
        try:
            amount = float(str(check_row.get("amount", "0")).lstrip("$").replace(",", ""))
        except (TypeError, ValueError):
            amount = 0.0
        fee_owed = round(pct * amount, 2)
        tx_data = {
            "date": check_row.get("check_date", ""),
            "amount": amount,
            "type": _coverage_to_type(check_row.get("coverage", "")),
            "fee_owed": fee_owed,
            "balance": 0,
            "deferred": 0,
            "recouped": 0,
            "fee_collected": 0,
            "reimbursed": 0,
            "total_collected": 0,
            "unpaid_payee_expense": 0,
            "outstanding_expense": 0,
            "ott": None,
            "notes": f"Check #{check_row.get('check_number', '')} — {check_row.get('coverage', '')}",
            "check_id": check_id,
        }
        db_module.create_transaction(claim_id, tx_data)

    flash("Check matched and transaction created.", "success")
    return redirect(url_for("checks_queue"))


def _coverage_to_type(coverage: str) -> str:
    c = (coverage or "").lower()
    if "draw" in c:
        return "Draw"
    if "escrow" in c:
        return "Escrow"
    return "Carrier"


# ---------------------------------------------------------------------------
# Assignees (claim-level split defaults) — HTMX fragments
# ---------------------------------------------------------------------------

@app.route("/claims/<int:claim_id>/assignees/new-row")
@login_required
def assignee_new_row(claim_id: int):
    claim = db_module.get_claim(claim_id)
    return render_template("partials/_assignee_edit.html",
                           claim=claim, assignee=None,
                           roles=ASSIGNEE_ROLES)

@app.route("/claims/<int:claim_id>/assignees", methods=["POST"])
@login_required
def assignee_create(claim_id: int):
    data = _assignee_data_from_form()
    aid = db_module.create_assignee(claim_id, data)
    assignee = db_module.get_assignee(aid)
    claim = db_module.get_claim(claim_id)
    row_html = render_template("partials/_assignee_row.html",
                               claim=claim, assignee=assignee, roles=ASSIGNEE_ROLES)
    placeholder = '<tr id="assignee-new-row-placeholder"></tr>'
    total_html = _assignee_total_oob(claim_id)
    return row_html + placeholder + total_html

@app.route("/claims/<int:claim_id>/assignees/<int:aid>/edit")
@login_required
def assignee_edit_row(claim_id: int, aid: int):
    claim = db_module.get_claim(claim_id)
    assignee = db_module.get_assignee(aid)
    if not assignee:
        abort(404)
    return render_template("partials/_assignee_edit.html",
                           claim=claim, assignee=assignee, roles=ASSIGNEE_ROLES)

@app.route("/claims/<int:claim_id>/assignees/<int:aid>", methods=["PUT"])
@login_required
def assignee_update(claim_id: int, aid: int):
    data = _assignee_data_from_form()
    db_module.update_assignee(aid, data)
    assignee = db_module.get_assignee(aid)
    claim = db_module.get_claim(claim_id)
    row_html = render_template("partials/_assignee_row.html",
                               claim=claim, assignee=assignee, roles=ASSIGNEE_ROLES)
    return row_html + _assignee_total_oob(claim_id)

@app.route("/claims/<int:claim_id>/assignees/<int:aid>", methods=["DELETE"])
@login_required
def assignee_delete(claim_id: int, aid: int):
    db_module.delete_assignee(aid)
    return _assignee_total_oob(claim_id)

@app.route("/claims/<int:claim_id>/assignees/<int:aid>")
@login_required
def assignee_view_row(claim_id: int, aid: int):
    claim = db_module.get_claim(claim_id)
    assignee = db_module.get_assignee(aid)
    if not assignee:
        abort(404)
    return render_template("partials/_assignee_row.html",
                           claim=claim, assignee=assignee, roles=ASSIGNEE_ROLES)

def _assignee_data_from_form() -> dict:
    return {
        "role": request.form.get("role", "other"),
        "name": request.form.get("name", "").strip(),
        "split_pct": db_module._f(request.form.get("split_pct")),
        "sort_order": request.form.get("sort_order", 0),
    }

def _assignee_total_oob(claim_id: int) -> str:
    total = db_module.get_assignee_total_pct(claim_id)
    return f'<span id="assignee-total" hx-swap-oob="true">{total:,.2f}%</span>'


# ---------------------------------------------------------------------------
# Transaction detail page (full page)
# ---------------------------------------------------------------------------

@app.route("/claims/<int:claim_id>/transactions/<int:tx_id>")
@login_required
def tx_detail(claim_id: int, tx_id: int):
    claim = db_module.get_claim(claim_id)
    tx = db_module.get_transaction(tx_id)
    if not claim or not tx:
        abort(404)
    splits = db_module.get_splits_with_amounts(tx_id)
    coverages = db_module.get_coverages(tx_id)
    disbursements = db_module.get_disbursements(tx_id)
    disburse_totals = db_module.get_disbursement_totals(tx_id)
    vendors = db_module.get_all_vendors()

    # Linked escrow info (for Draw transactions)
    linked_escrow = None
    escrow_disbursements = []
    if tx.get("linked_escrow_id"):
        linked_escrow = db_module.get_transaction(tx["linked_escrow_id"])
        escrow_disbursements = db_module.get_disbursements(tx["linked_escrow_id"])

    # Build set of recipient names already in this tx's disbursements for checklist
    covered_recipients = {d.get("recipient_name", "").lower() for d in disbursements}

    return render_template(
        "transaction.html",
        claim=claim,
        tx=tx,
        splits=splits,
        coverages=coverages,
        disbursements=disbursements,
        disburse_totals=disburse_totals,
        vendors=vendors,
        linked_escrow=linked_escrow,
        escrow_disbursements=escrow_disbursements,
        covered_recipients=covered_recipients,
        roles=ASSIGNEE_ROLES,
        coverage_types=COVERAGE_TYPES,
        tx_types=TRANSACTION_TYPES,
    )


# Transaction info (header fields) — view + HTMX inline edit

@app.route("/claims/<int:claim_id>/transactions/<int:tx_id>/info")
@login_required
def tx_info_view(claim_id: int, tx_id: int):
    claim = db_module.get_claim(claim_id)
    tx = db_module.get_transaction(tx_id)
    if not claim or not tx:
        abort(404)
    open_escrows = db_module.get_open_escrows(claim_id)
    return render_template("partials/_tx_info.html", claim=claim, tx=tx,
                           tx_types=TRANSACTION_TYPES,
                           open_escrows=open_escrows)


@app.route("/claims/<int:claim_id>/transactions/<int:tx_id>/info/edit")
@login_required
def tx_info_edit(claim_id: int, tx_id: int):
    claim = db_module.get_claim(claim_id)
    tx = db_module.get_transaction(tx_id)
    if not claim or not tx:
        abort(404)
    open_escrows = db_module.get_open_escrows(claim_id)
    return render_template("partials/_tx_info_edit.html",
                           claim=claim, tx=tx, tx_types=TRANSACTION_TYPES,
                           open_escrows=open_escrows)

@app.route("/claims/<int:claim_id>/transactions/<int:tx_id>/info", methods=["POST"])
@login_required
def tx_info_save(claim_id: int, tx_id: int):
    tx = db_module.get_transaction(tx_id)
    if not tx:
        abort(404)
    data = dict(tx)
    data.update({
        "check_number":    request.form.get("check_number", "").strip(),
        "received_date":   request.form.get("received_date", "").strip(),
        "payer":           request.form.get("payer", "").strip(),
        "payees_text":     request.form.get("payees_text", "").strip(),
        "endorsed":        bool(request.form.get("endorsed")),
        "void":            bool(request.form.get("void")),
        "type":            request.form.get("type", tx.get("type", "")),
        "date":            request.form.get("date", tx.get("date", "")),
        "notes":           request.form.get("notes", tx.get("notes", "")),
        "linked_escrow_id": request.form.get("linked_escrow_id") or None,
    })
    db_module.update_transaction(tx_id, data)
    tx = db_module.get_transaction(tx_id)
    claim = db_module.get_claim(claim_id)
    open_escrows = db_module.get_open_escrows(claim_id)
    return render_template("partials/_tx_info.html", claim=claim, tx=tx,
                           tx_types=TRANSACTION_TYPES,
                           open_escrows=open_escrows)


# ---------------------------------------------------------------------------
# Transaction Splits — HTMX fragments (on detail page)
# ---------------------------------------------------------------------------

@app.route("/claims/<int:claim_id>/transactions/<int:tx_id>/splits/new-row")
@login_required
def split_new_row(claim_id: int, tx_id: int):
    claim = db_module.get_claim(claim_id)
    tx = db_module.get_transaction(tx_id)
    return render_template("partials/_split_edit.html",
                           claim=claim, tx=tx, split=None, roles=ASSIGNEE_ROLES)

@app.route("/claims/<int:claim_id>/transactions/<int:tx_id>/splits", methods=["POST"])
@login_required
def split_create(claim_id: int, tx_id: int):
    data = _split_data_from_form()
    sid = db_module.create_split(tx_id, data)
    split = db_module.calc_split_amounts(db_module.get_split(sid),
                                         db_module.get_disbursement_totals(tx_id))
    claim = db_module.get_claim(claim_id)
    tx = db_module.get_transaction(tx_id)
    row_html = render_template("partials/_split_row.html",
                               claim=claim, tx=tx, split=split, roles=ASSIGNEE_ROLES)
    placeholder = '<tr id="split-new-row-placeholder"></tr>'
    total_html = _split_total_oob(tx_id)
    return row_html + placeholder + total_html

@app.route("/claims/<int:claim_id>/transactions/<int:tx_id>/splits/<int:sid>/edit")
@login_required
def split_edit_row(claim_id: int, tx_id: int, sid: int):
    claim = db_module.get_claim(claim_id)
    tx = db_module.get_transaction(tx_id)
    split = db_module.get_split(sid)
    if not split:
        abort(404)
    return render_template("partials/_split_edit.html",
                           claim=claim, tx=tx, split=split, roles=ASSIGNEE_ROLES)

@app.route("/claims/<int:claim_id>/transactions/<int:tx_id>/splits/<int:sid>", methods=["PUT"])
@login_required
def split_update(claim_id: int, tx_id: int, sid: int):
    data = _split_data_from_form()
    db_module.update_split(sid, data)
    split = db_module.calc_split_amounts(db_module.get_split(sid),
                                         db_module.get_disbursement_totals(tx_id))
    claim = db_module.get_claim(claim_id)
    tx = db_module.get_transaction(tx_id)
    row_html = render_template("partials/_split_row.html",
                               claim=claim, tx=tx, split=split, roles=ASSIGNEE_ROLES)
    return row_html + _split_total_oob(tx_id)

@app.route("/claims/<int:claim_id>/transactions/<int:tx_id>/splits/<int:sid>", methods=["DELETE"])
@login_required
def split_delete(claim_id: int, tx_id: int, sid: int):
    db_module.delete_split(sid)
    return _split_total_oob(tx_id)

@app.route("/claims/<int:claim_id>/transactions/<int:tx_id>/splits/seed", methods=["POST"])
@login_required
def split_seed(claim_id: int, tx_id: int):
    """Re-clone claim assignees into this transaction's splits (replaces existing)."""
    db_module.get_db().execute(
        "DELETE FROM transaction_splits WHERE transaction_id = ?", (tx_id,))
    db_module.get_db().commit()
    db_module.clone_splits_from_assignees(claim_id, tx_id)
    tx = db_module.get_transaction(tx_id)
    claim = db_module.get_claim(claim_id)
    splits = db_module.get_splits_with_amounts(tx_id)
    return render_template("partials/_splits_section.html",
                           claim=claim, tx=tx, splits=splits, roles=ASSIGNEE_ROLES)

@app.route("/claims/<int:claim_id>/transactions/<int:tx_id>/splits/<int:sid>")
@login_required
def split_view_row(claim_id: int, tx_id: int, sid: int):
    claim = db_module.get_claim(claim_id)
    tx = db_module.get_transaction(tx_id)
    split = db_module.get_split(sid)
    if not split:
        abort(404)
    split = db_module.calc_split_amounts(split, tx)
    return render_template("partials/_split_row.html",
                           claim=claim, tx=tx, split=split, roles=ASSIGNEE_ROLES)

def _split_data_from_form() -> dict:
    return {
        "role": request.form.get("role", "other"),
        "name": request.form.get("name", "").strip(),
        "split_pct": db_module._f(request.form.get("split_pct")),
    }

def _split_total_oob(tx_id: int) -> str:
    splits = db_module.get_splits_with_amounts(tx_id)
    total_pct = sum(s.get("split_pct") or 0 for s in splits)
    total_fee = sum(s.get("fee_amount") or 0 for s in splits)
    css = "color:var(--red)" if abs(total_pct - 100) > 0.01 and splits else ""
    return (
        f'<span id="split-total-pct" hx-swap-oob="true" style="{css}">{total_pct:,.2f}%</span>'
        f'<span id="split-total-fee" hx-swap-oob="true">${total_fee:,.2f}</span>'
    )


# ---------------------------------------------------------------------------
# Transaction Coverage — HTMX fragments (on detail page)
# ---------------------------------------------------------------------------

@app.route("/claims/<int:claim_id>/transactions/<int:tx_id>/coverage/new-row")
@login_required
def coverage_new_row(claim_id: int, tx_id: int):
    claim = db_module.get_claim(claim_id)
    tx = db_module.get_transaction(tx_id)
    return render_template("partials/_coverage_edit.html",
                           claim=claim, tx=tx, cov=None,
                           coverage_types=COVERAGE_TYPES)

@app.route("/claims/<int:claim_id>/transactions/<int:tx_id>/coverage", methods=["POST"])
@login_required
def coverage_create(claim_id: int, tx_id: int):
    data = _coverage_data_from_form()
    cid = db_module.create_coverage(tx_id, data)
    cov = db_module.get_coverage(cid)
    claim = db_module.get_claim(claim_id)
    tx = db_module.get_transaction(tx_id)
    row_html = render_template("partials/_coverage_row.html",
                               claim=claim, tx=tx, cov=cov,
                               coverage_types=COVERAGE_TYPES)
    placeholder = '<tr id="coverage-new-row-placeholder"></tr>'
    return row_html + placeholder + _coverage_total_oob(tx_id)

@app.route("/claims/<int:claim_id>/transactions/<int:tx_id>/coverage/<int:cid>/edit")
@login_required
def coverage_edit_row(claim_id: int, tx_id: int, cid: int):
    claim = db_module.get_claim(claim_id)
    tx = db_module.get_transaction(tx_id)
    cov = db_module.get_coverage(cid)
    if not cov:
        abort(404)
    return render_template("partials/_coverage_edit.html",
                           claim=claim, tx=tx, cov=cov,
                           coverage_types=COVERAGE_TYPES)

@app.route("/claims/<int:claim_id>/transactions/<int:tx_id>/coverage/<int:cid>", methods=["PUT"])
@login_required
def coverage_update(claim_id: int, tx_id: int, cid: int):
    data = _coverage_data_from_form()
    db_module.update_coverage(cid, data)
    cov = db_module.get_coverage(cid)
    claim = db_module.get_claim(claim_id)
    tx = db_module.get_transaction(tx_id)
    row_html = render_template("partials/_coverage_row.html",
                               claim=claim, tx=tx, cov=cov,
                               coverage_types=COVERAGE_TYPES)
    return row_html + _coverage_total_oob(tx_id)

@app.route("/claims/<int:claim_id>/transactions/<int:tx_id>/coverage/<int:cid>", methods=["DELETE"])
@login_required
def coverage_delete(claim_id: int, tx_id: int, cid: int):
    db_module.delete_coverage(cid)
    return _coverage_total_oob(tx_id)

@app.route("/claims/<int:claim_id>/transactions/<int:tx_id>/coverage/<int:cid>")
@login_required
def coverage_view_row(claim_id: int, tx_id: int, cid: int):
    claim = db_module.get_claim(claim_id)
    tx = db_module.get_transaction(tx_id)
    cov = db_module.get_coverage(cid)
    if not cov:
        abort(404)
    return render_template("partials/_coverage_row.html",
                           claim=claim, tx=tx, cov=cov,
                           coverage_types=COVERAGE_TYPES)

def _coverage_data_from_form() -> dict:
    return {
        "coverage_type": request.form.get("coverage_type", "").strip(),
        "amount": db_module._f(request.form.get("amount")),
    }

def _coverage_total_oob(tx_id: int) -> str:
    covs = db_module.get_coverages(tx_id)
    total = sum(c.get("amount") or 0 for c in covs)
    return f'<span id="coverage-total" hx-swap-oob="true">${total:,.2f}</span>'  # already has commas


# ---------------------------------------------------------------------------
# Disbursements — HTMX fragments (on transaction detail page)
# ---------------------------------------------------------------------------

@app.route("/claims/<int:claim_id>/transactions/<int:tx_id>/disbursements/new-row")
@login_required
def disbursement_new_row(claim_id: int, tx_id: int):
    claim = db_module.get_claim(claim_id)
    tx = db_module.get_transaction(tx_id)
    vendors = db_module.get_all_vendors()
    recipient_type = request.args.get("type", "insured")
    return render_template("partials/_disbursement_edit.html",
                           claim=claim, tx=tx, d=None,
                           recipient_type=recipient_type,
                           vendors=vendors)


@app.route("/claims/<int:claim_id>/transactions/<int:tx_id>/disbursements", methods=["POST"])
@login_required
def disbursement_create(claim_id: int, tx_id: int):
    data = _disbursement_data_from_form()
    did = db_module.create_disbursement(tx_id, data)
    d = db_module.get_disbursement(did)
    claim = db_module.get_claim(claim_id)
    tx = db_module.get_transaction(tx_id)
    vendors = db_module.get_all_vendors()
    row_html = render_template("partials/_disbursement_row.html",
                               claim=claim, tx=tx, d=d, vendors=vendors)
    placeholder = '<tr id="disburse-new-row-placeholder"></tr>'
    totals_html = _disburse_totals_oob(tx_id, claim)
    return row_html + placeholder + totals_html


@app.route("/claims/<int:claim_id>/transactions/<int:tx_id>/disbursements/<int:did>/edit")
@login_required
def disbursement_edit_row(claim_id: int, tx_id: int, did: int):
    claim = db_module.get_claim(claim_id)
    tx = db_module.get_transaction(tx_id)
    d = db_module.get_disbursement(did)
    if not d:
        abort(404)
    vendors = db_module.get_all_vendors()
    return render_template("partials/_disbursement_edit.html",
                           claim=claim, tx=tx, d=d,
                           recipient_type=d.get("recipient_type", "insured"),
                           vendors=vendors)


@app.route("/claims/<int:claim_id>/transactions/<int:tx_id>/disbursements/<int:did>",
           methods=["PUT"])
@login_required
def disbursement_update(claim_id: int, tx_id: int, did: int):
    data = _disbursement_data_from_form()
    db_module.update_disbursement(did, data)
    d = db_module.get_disbursement(did)
    claim = db_module.get_claim(claim_id)
    tx = db_module.get_transaction(tx_id)
    vendors = db_module.get_all_vendors()
    row_html = render_template("partials/_disbursement_row.html",
                               claim=claim, tx=tx, d=d, vendors=vendors)
    return row_html + _disburse_totals_oob(tx_id, claim)


@app.route("/claims/<int:claim_id>/transactions/<int:tx_id>/disbursements/<int:did>",
           methods=["DELETE"])
@login_required
def disbursement_delete(claim_id: int, tx_id: int, did: int):
    db_module.delete_disbursement(did)
    claim = db_module.get_claim(claim_id)
    return _disburse_totals_oob(tx_id, claim)


@app.route("/claims/<int:claim_id>/transactions/<int:tx_id>/disbursements/<int:did>")
@login_required
def disbursement_view_row(claim_id: int, tx_id: int, did: int):
    claim = db_module.get_claim(claim_id)
    tx = db_module.get_transaction(tx_id)
    d = db_module.get_disbursement(did)
    if not d:
        abort(404)
    vendors = db_module.get_all_vendors()
    return render_template("partials/_disbursement_row.html",
                           claim=claim, tx=tx, d=d, vendors=vendors)


def _disbursement_data_from_form() -> dict:
    fee_applies_raw = request.form.get("fee_applies")
    # checkbox: present = checked
    fee_applies = fee_applies_raw is not None and fee_applies_raw != "0"
    return {
        "sort_order":     request.form.get("sort_order", 0),
        "recipient_type": request.form.get("recipient_type", "insured"),
        "vendor_id":      request.form.get("vendor_id") or None,
        "recipient_name": request.form.get("recipient_name", "").strip(),
        "amount":         db_module._f(request.form.get("amount")),
        "fee_applies":    fee_applies,
        "fee_pct":        db_module._fopt(request.form.get("fee_pct")),
        "fee_owed":       db_module._f(request.form.get("fee_owed")),
        "fee_collected":  db_module._f(request.form.get("fee_collected")),
        "fee_deferred":   db_module._f(request.form.get("fee_deferred")),
        "fee_recouped":   db_module._f(request.form.get("fee_recouped")),
        "use_check_splits": True,
        "notes":          request.form.get("notes", "").strip(),
    }


def _disburse_totals_oob(tx_id: int, claim: dict) -> str:
    t = db_module.get_disbursement_totals(tx_id)
    return (
        f'<span id="disburse-total-disbursed" hx-swap-oob="true">${t["total_disbursed"]:,.2f}</span>'
        f'<span id="disburse-total-fee-owed" hx-swap-oob="true">${t["fee_owed"]:,.2f}</span>'
        f'<span id="disburse-total-fee-coll" hx-swap-oob="true">${t["fee_collected"]:,.2f}</span>'
        f'<span id="disburse-total-deferred" hx-swap-oob="true">${t["fee_deferred"]:,.2f}</span>'
        f'<span id="disburse-sidebar-net" hx-swap-oob="true">${t["net_to_insured"]:,.2f}</span>'
        f'<span id="disburse-sidebar-vendors" hx-swap-oob="true">${t["to_vendors"]:,.2f}</span>'
        f'<span id="disburse-sidebar-fee-owed" hx-swap-oob="true">${t["fee_owed"]:,.2f}</span>'
        f'<span id="disburse-sidebar-fee-coll" hx-swap-oob="true">${t["fee_collected"]:,.2f}</span>'
        f'<span id="disburse-sidebar-deferred" hx-swap-oob="true">${t["fee_deferred"]:,.2f}</span>'
        f'<span id="disburse-sidebar-recouped" hx-swap-oob="true">${t["fee_recouped"]:,.2f}</span>'
        + _split_total_oob(tx_id)
    )


# ---------------------------------------------------------------------------
# Vendors — global management
# ---------------------------------------------------------------------------

@app.route("/vendors")
@login_required
def vendors_list():
    vendors = db_module.get_all_vendors()
    return render_template("vendors.html", vendors=vendors)


@app.route("/vendors/options")
@login_required
def vendors_options():
    vendors = db_module.get_all_vendors()
    return jsonify([{"value": str(v["id"]), "text": v["name"]} for v in vendors])


@app.route("/vendors", methods=["POST"])
@login_required
def vendor_create():
    data = {
        "name":  request.form.get("name", "").strip(),
        "phone": request.form.get("phone", "").strip(),
        "email": request.form.get("email", "").strip(),
        "notes": request.form.get("notes", "").strip(),
    }
    if not data["name"]:
        return "Name required", 400
    try:
        vid = db_module.create_vendor(data)
    except Exception:
        return "Vendor name already exists", 409
    vendor = db_module.get_vendor(vid)
    # If called from Tom Select inline creation, return JSON
    if request.headers.get("Accept", "").startswith("application/json"):
        return jsonify({"value": str(vendor["id"]), "text": vendor["name"]})
    vendors = db_module.get_all_vendors()
    return render_template("vendors.html", vendors=vendors)


@app.route("/vendors/<int:vendor_id>", methods=["PUT"])
@login_required
def vendor_update(vendor_id: int):
    data = {
        "name":  request.form.get("name", "").strip(),
        "phone": request.form.get("phone", "").strip(),
        "email": request.form.get("email", "").strip(),
        "notes": request.form.get("notes", "").strip(),
    }
    db_module.update_vendor(vendor_id, data)
    vendors = db_module.get_all_vendors()
    return render_template("vendors.html", vendors=vendors)


@app.route("/vendors/<int:vendor_id>", methods=["DELETE"])
@login_required
def vendor_delete(vendor_id: int):
    db_module.delete_vendor(vendor_id)
    vendors = db_module.get_all_vendors()
    return render_template("vendors.html", vendors=vendors)


# ---------------------------------------------------------------------------
# Fee Recipients — global management
# ---------------------------------------------------------------------------

@app.route("/fee-recipients")
@login_required
def fee_recipients_list():
    recipients = db_module.get_all_fee_recipients()
    return render_template("fee_recipients.html", recipients=recipients,
                           roles=ASSIGNEE_ROLES)


@app.route("/fee-recipients/options")
@login_required
def fee_recipients_options():
    recipients = db_module.get_all_fee_recipients()
    return jsonify([{"value": str(r["id"]), "text": r["name"]} for r in recipients])


@app.route("/fee-recipients", methods=["POST"])
@login_required
def fee_recipient_create():
    data = {
        "name":         request.form.get("name", "").strip(),
        "default_role": request.form.get("default_role", "").strip(),
    }
    if not data["name"]:
        return "Name required", 400
    try:
        rid = db_module.create_fee_recipient(data)
    except Exception:
        return "Name already exists", 409
    recipients = db_module.get_all_fee_recipients()
    return render_template("fee_recipients.html", recipients=recipients,
                           roles=ASSIGNEE_ROLES)


@app.route("/fee-recipients/<int:rid>", methods=["PUT"])
@login_required
def fee_recipient_update(rid: int):
    data = {
        "name":         request.form.get("name", "").strip(),
        "default_role": request.form.get("default_role", "").strip(),
    }
    db_module.update_fee_recipient(rid, data)
    recipients = db_module.get_all_fee_recipients()
    return render_template("fee_recipients.html", recipients=recipients,
                           roles=ASSIGNEE_ROLES)


@app.route("/fee-recipients/<int:rid>", methods=["DELETE"])
@login_required
def fee_recipient_delete(rid: int):
    db_module.delete_fee_recipient(rid)
    recipients = db_module.get_all_fee_recipients()
    return render_template("fee_recipients.html", recipients=recipients,
                           roles=ASSIGNEE_ROLES)


# ---------------------------------------------------------------------------
# App startup
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    db_module.init_db()
    print(f"Database: {config.DB_PATH}")
    print("Run seed.py first if this is a fresh install.")
    app.run(debug=config.DEBUG, host="0.0.0.0", port=5001)
