from __future__ import annotations
"""CAT Excel file parser.

Entry point:
    parse_workbook(wb) -> dict
"""

import re
from datetime import datetime, date as date_type


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SHEET_TYPE_MAP = {
    "escrow endorsed": "Escrow Endorsed",
    "endorsed":        "Escrow Endorsed",
    "escrow":          "Escrow",
    "carrier":         "Carrier",
    "draw":            "Draw",
    "fee invoice":     "Fee Invoice",
    "fee":             "Fee Invoice",
}

ROLE_MAP = {
    "firm":      "firm",
    "waypoint":  "firm",
    "wp":        "firm",
    "sales":     "sales",
    "adjuster":  "adjuster",
    "adj":       "adjuster",
    "referrer":  "referrer",
    "ref":       "referrer",
}


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _excel_date(v) -> str | None:
    """Convert Excel date value to ISO date string YYYY-MM-DD."""
    if v is None:
        return None
    if isinstance(v, (datetime, date_type)):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, (int, float)) and v > 0:
        from datetime import timedelta
        try:
            excel_epoch = datetime(1899, 12, 30)
            return (excel_epoch + timedelta(days=int(v))).strftime("%Y-%m-%d")
        except Exception:
            return None
    if isinstance(v, str):
        v = v.strip()
        if not v:
            return None
        for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%m/%d/%y", "%B %d, %Y"):
            try:
                return datetime.strptime(v, fmt).strftime("%Y-%m-%d")
            except ValueError:
                pass
        return v
    return None


def _cell_value(ws, row: int, col: int):
    """Get cell value, resolving merged cells."""
    cell = ws.cell(row=row, column=col)
    if cell.value is not None:
        return cell.value
    for mr in ws.merged_cells.ranges:
        if mr.min_row <= row <= mr.max_row and mr.min_col <= col <= mr.max_col:
            return ws.cell(row=mr.min_row, column=mr.min_col).value
    return None


def _str(v) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _str_id(v) -> str:
    """Like _str but strips trailing '.0' that Excel adds to whole-number values."""
    s = _str(v)
    if s.endswith(".0") and s[:-2].lstrip("-").isdigit():
        return s[:-2]
    return s


def _clean_amount(v) -> float:
    """Parse a dollar amount from various formats."""
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("$", "").replace(",", "")
    s = s.replace("(", "-").replace(")", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _clean_pct(v) -> float:
    """Parse a percentage. Handles '40%' → 40.0, 0.40 → 40.0."""
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        f = float(v)
        # If stored as decimal (0.4 instead of 40), convert
        if 0 < abs(f) <= 1.0:
            return round(f * 100, 4)
        return f
    s = str(v).strip().rstrip("%")
    try:
        f = float(s)
        if 0 < abs(f) <= 1.0:
            return round(f * 100, 4)
        return f
    except ValueError:
        return 0.0


def _clean_check_number(v) -> str:
    """Convert a check number value to a clean string (no .0 suffix)."""
    if v is None:
        return ""
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    return str(v).strip()


def _find_cell(ws, sentinel: str):
    """Find the first cell containing sentinel (case-insensitive). Returns cell or None."""
    sentinel_lower = sentinel.lower()
    for row in ws.iter_rows():
        for cell in row:
            if cell.value and sentinel_lower in str(cell.value).lower():
                return cell
    return None


def _row_values(ws, row: int, start_col: int = 1, max_cols: int = 15) -> list:
    """Return list of values for a row."""
    return [_cell_value(ws, row, c) for c in range(start_col, start_col + max_cols)]


def _parse_subtab_sheet(ws) -> dict:
    """Extract fee amounts and received status from a per-payee sub-tab sheet.

    Sub-tab layout (labeled rows, col A = label, col B = value):
        R7  C1="FEE %"          C2=0.15   → fee_pct = 15.0
        R8  C1="RECOUPED FEES+" C2=amount → fee_recouped
        R9  C1="DEFERED FEES -" C2=amount → fee_deferred
        R10 C3="Confirmed"/"Unconfirmed"  → client_received
        R11 C4="WAYPOINT FEE"   C5=amount C6="Confirmed"/"Unconfirmed" → fee_received/fee_collected
    """
    out = {
        "fee_pct":        0.0,
        "fee_deferred":   0.0,
        "fee_recouped":   0.0,
        "fee_collected":  0.0,
        "client_received": False,
        "fee_received":   False,
    }
    try:
        # Labeled header rows: col A (1) = label, col B (2) = value
        for r in range(1, 15):
            label = _str(_cell_value(ws, r, 1)).upper()
            val   = _cell_value(ws, r, 2)
            if "FEE %" in label:
                out["fee_pct"] = _clean_pct(val) if val is not None else 0.0
            elif "RECOUP" in label:
                out["fee_recouped"] = round(abs(_clean_amount(val)), 2) if val is not None else 0.0
            elif "DEFER" in label:
                # label is "DEFERED FEES -" (note: one F, intentional in Excel)
                out["fee_deferred"] = round(abs(_clean_amount(val)), 2) if val is not None else 0.0

        # C10: client received status (col C = 3, row 10)
        c10 = _str(_cell_value(ws, 10, 3)).lower()
        out["client_received"] = bool(c10 and "confirm" in c10 and "unconfirm" not in c10)

        # Fee received: col D (4) = "WAYPOINT FEE", col E (5) = amount,
        # col F (6) = "Confirmed"/"Unconfirmed"  (or legacy "Collected"/"NOT Collected")
        for r in range(8, 16):
            d_val = _cell_value(ws, r, 4)
            if d_val and "WAYPOINT FEE" in str(d_val).upper():
                status_s = _str(_cell_value(ws, r, 6)).lower()
                is_received = (
                    ("confirm" in status_s and "unconfirm" not in status_s)
                    or ("collect" in status_s and "not" not in status_s)
                )
                if is_received:
                    amt_v = _cell_value(ws, r, 5)
                    out["fee_collected"] = round(abs(_clean_amount(amt_v)), 2) if amt_v is not None else 0.0
                    out["fee_received"] = True
                break
    except Exception:
        pass
    return out


def _find_row(ws, sentinel: str) -> int | None:
    """Find first row number containing sentinel."""
    cell = _find_cell(ws, sentinel)
    return cell.row if cell else None


# ---------------------------------------------------------------------------
# Sheet classifier
# ---------------------------------------------------------------------------

KNOWN_TX_KEYWORDS = frozenset({
    "escrow", "endorsed", "carrier", "draw", "fee",
})


def _classify_sheets(wb) -> tuple:
    """Return (summary_ws, vendors_ws, tx_sheets, exp_sheets).

    tx_sheets and exp_sheets are lists of (seq_int, sheet_name, ws).
    """
    sheets = wb.sheetnames
    summary_ws = None
    vendors_ws = None
    # Track any "split"-named candidate separately so "vendor" can override it
    _split_candidate = None
    tx_sheets: list[tuple[int, str, object]] = []
    exp_sheets: list[tuple[int, str, object]] = []

    for i, name in enumerate(sheets):
        ws = wb[name]
        name_lower = name.lower().strip()

        if i == 0:
            summary_ws = ws
            continue

        # Numbered sheets: "1_Escrow", "2_Carrier", "03 Expense", etc.
        m = re.match(r'^(\d+)\s*[_\-\s]\s*(.+)$', name)
        if m:
            seq = int(m.group(1))
            type_part = m.group(2).strip().lower()
            if "expense" in type_part:
                exp_sheets.append((seq, name, ws))
            elif any(kw in type_part for kw in KNOWN_TX_KEYWORDS):
                # Only classified tx sheets; skip insured-name paired sheets
                tx_sheets.append((seq, name, ws))
            # else: skip (e.g., "2_Joyti Goundar" insured-payee tabs)
        else:
            # Non-numbered: find vendors/splits sheet.
            # Exclude utility sheets (templates, totals, repayment, notes).
            is_utility = any(p in name_lower for p in (
                "template", "total", "repay", "checks", "escrow", "draw",
            ))
            if not is_utility:
                if "vendor" in name_lower:
                    # Prefer "vendor"-named sheet — always overrides "split"
                    vendors_ws = ws
                elif "split" in name_lower and vendors_ws is None:
                    _split_candidate = ws

    # Use split candidate only if no vendor sheet was found
    if vendors_ws is None and _split_candidate is not None:
        vendors_ws = _split_candidate

    tx_sheets.sort(key=lambda x: x[0])
    exp_sheets.sort(key=lambda x: x[0])
    return summary_ws, vendors_ws, tx_sheets, exp_sheets


# ---------------------------------------------------------------------------
# Summary sheet parser
# ---------------------------------------------------------------------------

def _parse_summary_sheet(ws) -> tuple[dict, list[dict], list[str]]:
    """Return (claim_data, limits, warnings)."""
    warnings: list[str] = []
    claim = {
        "insured_name": "",
        "carrier": "",
        "contract_pct": 0.0,
        "job_number": "",
        "claim_number": "",
    }
    limits: list[dict] = []

    try:
        max_row = min(ws.max_row or 100, 150)
        max_col = min(ws.max_column or 10, 25)

        # ---- Claim header fields ----
        for r in range(1, max_row + 1):
            for c in range(1, max_col + 1):
                v = _cell_value(ws, r, c)
                if v is None:
                    continue
                vs = str(v).strip().lower()

                def _take_adjacent(row, col):
                    """Return first non-empty value to the right."""
                    for off in range(1, 6):
                        nv = _cell_value(ws, row, col + off)
                        if nv is not None and str(nv).strip():
                            return nv
                    return None

                if "insured" in vs and not claim["insured_name"]:
                    nv = _take_adjacent(r, c)
                    if nv:
                        claim["insured_name"] = _str(nv)

                elif "carrier" in vs and not claim["carrier"]:
                    nv = _take_adjacent(r, c)
                    if nv:
                        claim["carrier"] = _str(nv)

                elif (("contract" in vs and "%" in vs)
                      or ("fee" in vs and "%" in vs)
                      or ("contract" in vs and "pct" in vs)) and not claim["contract_pct"]:
                    nv = _take_adjacent(r, c)
                    if nv is not None:
                        claim["contract_pct"] = _clean_pct(nv)

                elif "job" in vs and ("#" in vs or "no" in vs or "number" in vs) and not claim["job_number"]:
                    nv = _take_adjacent(r, c)
                    if nv:
                        claim["job_number"] = _str_id(nv)

                elif "claim" in vs and ("#" in vs or "no" in vs or "number" in vs) and not claim["claim_number"]:
                    nv = _take_adjacent(r, c)
                    if nv:
                        claim["claim_number"] = _str_id(nv)

        # ---- Policy limits ----
        # Locate "Base Limit" header to anchor the column layout.
        # Expected structure:
        #   col (base_col - 1): coverage type
        #   col  base_col:      base limit
        #   col (base_col + 1): extended limit   ← read this
        #   col (base_col + 2): paid             ← skip (do NOT read)
        base_limit_cell = _find_cell(ws, "base limit")
        if base_limit_cell:
            hdr_row = base_limit_cell.row
            base_col = base_limit_cell.column   # e.g. col 13 (M)
            cov_col  = base_col - 1             # e.g. col 12 (L) — coverage type
            ext_col  = base_col + 1             # e.g. col 14 (N) — extended limit

            for r in range(hdr_row + 1, min(max_row + 1, hdr_row + 20)):
                cov_v = _cell_value(ws, r, cov_col)
                if cov_v is None or not str(cov_v).strip():
                    continue
                cov_s = str(cov_v).strip()
                # Skip header-like or total rows
                if any(kw in cov_s.upper() for kw in ("TOTAL", "NOTE", "LIMIT", "BASE", "EXTEND")):
                    continue
                # Skip purely numeric values (empty formula cells like 0.0)
                try:
                    float(cov_s)
                    continue
                except ValueError:
                    pass

                base_v = _cell_value(ws, r, base_col)
                ext_v  = _cell_value(ws, r, ext_col)

                base = _clean_amount(base_v) if base_v is not None else 0.0
                ext  = _clean_amount(ext_v)  if ext_v  is not None else 0.0

                # Skip rows with no limit values at all
                if base == 0.0 and ext == 0.0:
                    continue

                limits.append({
                    "coverage_type": cov_s,
                    "base_limit":    base,
                    "extended_limit": ext,
                })
        else:
            warnings.append("Could not find 'Base Limit' header in summary sheet — limits may be incomplete.")

    except Exception as e:
        warnings.append(f"Summary sheet parse error: {e}")

    return claim, limits, warnings


# ---------------------------------------------------------------------------
# Vendors / splits sheet parser
# ---------------------------------------------------------------------------

def _parse_vendors_sheet(ws) -> tuple[list[dict], list[str]]:
    """Return (assignees, warnings).

    Expected SPLITS table layout (from actual file analysis):
        col start_col      (J / col 10): label   e.g. "Assignee 1"
        col start_col + 1  (K / col 11): role    e.g. "adjuster"
        col start_col + 2  (L / col 12): name    e.g. "John Smith"
        col start_col + 4  (N / col 14): split % e.g. 0.40 or 40%
    """
    warnings: list[str] = []
    assignees: list[dict] = []

    if ws is None:
        return assignees, warnings

    try:
        # Find the SPLITS section header
        splits_cell = _find_cell(ws, "splits")
        if splits_cell is None:
            splits_cell = _find_cell(ws, "role")
        if splits_cell is None:
            warnings.append("Could not find SPLITS table in vendors/splits sheet.")
            return assignees, warnings

        start_row = splits_cell.row + 1
        start_col = splits_cell.column  # label column (e.g. col 10 / J)
        max_row   = min(ws.max_row or 100, start_row + 40)

        # Column offsets relative to the SPLITS header column
        ROLE_OFF = 1   # K — role / title
        NAME_OFF = 2   # L — person name
        PCT_OFF  = 4   # N — split %

        for r in range(start_row, max_row + 1):
            role_v = _cell_value(ws, r, start_col + ROLE_OFF)
            name_v = _cell_value(ws, r, start_col + NAME_OFF)
            pct_v  = _cell_value(ws, r, start_col + PCT_OFF)

            if role_v is None and name_v is None:
                continue

            role_s = _str(role_v).lower()
            name_s = _str(name_v)

            if not role_s and not name_s:
                continue

            # Skip header / label rows
            if role_s in ("role", "title", "position", "splits", "name", "%", "split %", "split", "amount"):
                continue

            # Skip placeholder rows (no pct and no real name)
            if "undistributed" in name_s.lower():
                continue

            # Map to canonical role
            mapped_role = "other"
            for key, val in ROLE_MAP.items():
                if key in role_s:
                    mapped_role = val
                    break

            pct = _clean_pct(pct_v) if pct_v is not None else 0.0

            if name_s or pct:
                assignees.append({
                    "role":      mapped_role,
                    "name":      name_s or role_s,
                    "split_pct": pct,
                })

    except Exception as e:
        warnings.append(f"Vendors sheet parse error: {e}")

    return assignees, warnings


# ---------------------------------------------------------------------------
# Transaction sheet parser
# ---------------------------------------------------------------------------

def _parse_transaction_sheet(ws, name: str, wb=None) -> tuple[dict, list[str]]:
    """Return (tx_data, warnings).

    Expected check sheet layout (from actual file analysis):
        Row 3 (header):  col 3=label, col 4=value area
                         col 6="Policy Limits", col 7="Base Limit",
                         col 8="Extended", col 9="Paid",
                         col 10="This Check"  ← per-check amounts
        Rows 4-9:        col 6=coverage type, col 10=per-check amount
        Row 9:           col 3="PAYEES", col 4=payee name

        Row ~12 (header): col 3=Type, col 4=Name, col 5=Amount, col 6=Fee%
        Rows 13+:         disbursement data with same column layout

        SPLITS section:  same layout as vendors sheet
                         (SPLITS label col = start_col, role=+1, name=+2, pct=+4)
    """
    warnings: list[str] = []

    m = re.match(r'^(\d+)\s*[_\-\s]\s*(.+)$', name)
    seq = int(m.group(1)) if m else 0
    type_raw = m.group(2).strip().lower() if m else ""

    # Resolve type from sheet name
    tx_type = "Carrier"
    for key, val in SHEET_TYPE_MAP.items():
        if type_raw == key or type_raw.startswith(key):
            tx_type = val
            break
    if tx_type == "Carrier":
        for key, val in SHEET_TYPE_MAP.items():
            if key in type_raw:
                tx_type = val
                break

    tx: dict = {
        "seq":           seq,
        "type":          tx_type,
        "check_number":  "",
        "received_date": None,
        "check_date":    None,
        "amount":        0.0,
        "payer":         "",
        "payees_text":   "",
        "void":          False,
        "coverages":     [],
        "disbursements": [],
        "splits":        [],
        "parse_warnings": warnings,
    }

    try:
        max_row = min(ws.max_row or 200, 300)

        # ---- G11: void marker ----
        g11 = _str(_cell_value(ws, 11, 7)).lower()
        if "void" in g11:
            tx["void"] = True

        # ---- Scan header fields (rows 1–25) ----
        for r in range(1, min(max_row + 1, 25)):
            for c in range(1, 8):
                v = _cell_value(ws, r, c)
                if v is None:
                    continue
                vs = str(v).strip().lower()

                def _adj(row, col, count=8):
                    for off in range(1, count + 1):
                        nv = _cell_value(ws, row, col + off)
                        if nv is not None and str(nv).strip():
                            return nv
                    return None

                if "check" in vs and "#" in vs and not tx["check_number"]:
                    nv = _adj(r, c)
                    if nv:
                        tx["check_number"] = _clean_check_number(nv)

                elif "received" in vs and "date" in vs and not tx["received_date"]:
                    nv = _adj(r, c)
                    if nv:
                        tx["received_date"] = _excel_date(nv)

                elif "received" in vs and not tx["received_date"]:
                    nv = _adj(r, c)
                    if nv:
                        tx["received_date"] = _excel_date(nv)

                elif ("check date" in vs or vs == "date") and not tx["check_date"]:
                    nv = _adj(r, c)
                    if nv:
                        tx["check_date"] = _excel_date(nv)

                elif "date" in vs and "received" not in vs and "check" not in vs and not tx["check_date"]:
                    nv = _adj(r, c)
                    if nv:
                        tx["check_date"] = _excel_date(nv)

                elif "amount" in vs and tx["amount"] == 0.0:
                    nv = _adj(r, c)
                    if nv is not None:
                        amt = _clean_amount(nv)
                        if amt != 0.0:
                            tx["amount"] = abs(amt)

                # "PAYEES" (who the check is made out to) — R9 C3 label
                # "payee" is NOT in "payer"/"payor", so these two don't conflict
                elif "payee" in vs and not tx["payees_text"]:
                    nv = _adj(r, c)
                    if nv:
                        tx["payees_text"] = _str(nv)

                elif ("payer" in vs or "payor" in vs or
                      ("from" in vs and len(vs) < 15)) and not tx["payer"]:
                    nv = _adj(r, c)
                    if nv:
                        tx["payer"] = _str(nv)

        # ---- Coverage allocation ----
        # Locate "This Check" column header — that column holds per-check amounts.
        # Coverage type is 4 columns to the left of "This Check" (based on file analysis).
        this_check_cell = _find_cell(ws, "this check")
        if this_check_cell:
            tc_col = this_check_cell.column   # e.g. col 10 (J)
            tc_row = this_check_cell.row      # e.g. row 3
            cov_col = tc_col - 4             # e.g. col  6 (F) — coverage type

            for r in range(tc_row + 1, min(max_row + 1, tc_row + 15)):
                cov_v = _cell_value(ws, r, cov_col)
                amt_v = _cell_value(ws, r, tc_col)

                if cov_v is None or not str(cov_v).strip():
                    continue
                cov_s = str(cov_v).strip()

                # Stop scanning at non-coverage section boundaries
                if any(kw in cov_s.upper() for kw in ("VOID", "SPLITS", "TYPE", "NAME")):
                    break
                # Skip header / total rows (but continue scanning past them)
                if any(kw in cov_s.upper() for kw in (
                        "POLICY", "LIMIT", "BASE", "EXTENDED", "PAID", "REMAIN",
                        "CHECK", "TOTAL", "COVERAGE TYPE", "PERCENTAGE")):
                    continue
                # Skip purely numeric values (formula 0 cells)
                try:
                    float(cov_s)
                    continue
                except ValueError:
                    pass

                amt = _clean_amount(amt_v) if amt_v is not None else 0.0
                if amt == 0.0:
                    continue
                tx["coverages"].append({
                    "coverage_type": cov_s,
                    "amount":        abs(amt),
                })
        else:
            # Fallback: original sentinel scan (for sheets that differ in layout)
            seen_cov: set[str] = set()
            for r in range(1, max_row + 1):
                for c in range(1, 15):
                    v = _cell_value(ws, r, c)
                    if v is None:
                        continue
                    vs = str(v).strip().upper()
                    cov_match = re.match(r'^(COV(?:ERAGE)?\s*[A-D])', vs)
                    if cov_match and cov_match.group(1) not in seen_cov:
                        seen_cov.add(cov_match.group(1))
                        cov_name = re.sub(r'COVERAGE\s*', 'COV ', cov_match.group(1))
                        cov_name = re.sub(r'COV\s*([A-D])', r'COV \1', cov_name)
                        for off in range(1, 8):
                            nv = _cell_value(ws, r, c + off)
                            if nv is not None and str(nv).strip():
                                amt = _clean_amount(nv)
                                if amt != 0.0:
                                    tx["coverages"].append({
                                        "coverage_type": cov_name,
                                        "amount":        abs(amt),
                                    })
                                break

        # ---- Disbursements ----
        # Find the disbursement header row: col 3="Type", col 5="Amount".
        # (Sheets use this layout rather than a "PAY TO" sentinel.)
        #   col 3 = recipient type (Insured / Vendor)
        #   col 4 = recipient name
        #   col 5 = amount
        #   col 6 = fee % (if any)
        disb_header_row = None
        for r in range(1, min(max_row + 1, 60)):
            v3 = _cell_value(ws, r, 3)
            v5 = _cell_value(ws, r, 5)
            if (v3 is not None and str(v3).strip().lower() == "type" and
                    v5 is not None and "amount" in str(v5).strip().lower()):
                disb_header_row = r
                break
        # Fallback: try "PAY TO" sentinel
        if disb_header_row is None:
            pay_to_cell = _find_cell(ws, "pay to")
            if pay_to_cell:
                disb_header_row = pay_to_cell.row

        if disb_header_row is not None:
            pr = disb_header_row

            for r in range(pr + 1, min(max_row + 1, pr + 40)):
                subtab_v = _cell_value(ws, r, 2)  # col B = sub-tab sheet name
                type_v   = _cell_value(ws, r, 3)
                name_v   = _cell_value(ws, r, 4)
                amt_v    = _cell_value(ws, r, 5)

                # Stop when we hit the SPLITS section
                if type_v and "split" in str(type_v).lower():
                    break
                # Skip empty rows (check name and amount)
                if all(v is None or str(v).strip() == "" for v in [type_v, name_v, amt_v]):
                    continue

                type_s = _str(type_v).lower()
                name_s = _str(name_v)
                amt    = _clean_amount(amt_v) if amt_v is not None else 0.0

                # Classify recipient type
                if any(kw in type_s for kw in ("insured", "owner", "homeowner", "client")):
                    recipient_type = "insured"
                elif any(kw in type_s for kw in ("vendor", "contractor", "sub")):
                    recipient_type = "vendor"
                elif re.search(
                        r'\b(inc\.?|llc\.?|corp\.?|co\.?|company|contractors?|roofing|'
                        r'plumbing|electric|hvac)\b',
                        name_s, re.IGNORECASE):
                    recipient_type = "vendor"
                else:
                    recipient_type = "insured"  # default

                if name_s or amt != 0.0:
                    disb = {
                        "recipient_type":    recipient_type,
                        "recipient_name":    name_s,
                        "amount":            round(abs(amt), 2),
                        "fee_pct":           None,
                        "fee_owed":          0.0,
                        "fee_collected":     0.0,
                        "fee_deferred":      0.0,
                        "fee_recouped":      0.0,
                        "fee_deferred_flag": False,
                        "fee_recouped_flag": False,
                        "client_received":   False,
                        "fee_received":      False,
                    }

                    # Pull fee details from per-payee sub-tab if available
                    subtab_name = _str(subtab_v)
                    if subtab_name and wb is not None and subtab_name in wb.sheetnames:
                        try:
                            sub = _parse_subtab_sheet(wb[subtab_name])
                            disb["fee_pct"]        = sub["fee_pct"] or None
                            disb["fee_deferred"]   = sub["fee_deferred"]
                            disb["fee_recouped"]   = sub["fee_recouped"]
                            # fee_collected left at 0 — user fills in post-import
                            disb["fee_owed"]       = round(abs(amt) * (sub["fee_pct"] / 100), 2) if sub["fee_pct"] else 0.0
                            disb["fee_deferred_flag"]  = sub["fee_deferred"] > 0
                            disb["fee_recouped_flag"]  = sub["fee_recouped"] > 0
                            disb["client_received"]    = sub["client_received"]
                            disb["fee_received"]       = sub["fee_received"]
                        except Exception:
                            pass

                    tx["disbursements"].append(disb)

        # ---- Per-check SPLITS ----
        # Same layout as vendors sheet:
        #   SPLITS header col = start_col; role=+1, name=+2, pct=+4
        splits_cell = _find_cell(ws, "splits")
        if splits_cell:
            sr = splits_cell.row
            sc = splits_cell.column

            for r in range(sr + 1, min(max_row + 1, sr + 25)):
                role_v = _cell_value(ws, r, sc + 1)
                name_v = _cell_value(ws, r, sc + 2)
                pct_v  = _cell_value(ws, r, sc + 4)

                if role_v is None and name_v is None:
                    continue
                role_s = _str(role_v).lower()
                name_s = _str(name_v)

                if not role_s and not name_s:
                    continue
                if role_s in ("role", "title", "splits", "name", "%", "split %", "amount"):
                    continue
                if "undistributed" in name_s.lower():
                    continue

                mapped_role = "other"
                for key, val in ROLE_MAP.items():
                    if key in role_s:
                        mapped_role = val
                        break

                pct = _clean_pct(pct_v) if pct_v is not None else 0.0

                if name_s or pct:
                    tx["splits"].append({
                        "role":      mapped_role,
                        "name":      name_s or role_s,
                        "split_pct": pct,
                    })

    except Exception as e:
        warnings.append(f"Transaction sheet '{name}' parse error: {e}")

    return tx, warnings


# ---------------------------------------------------------------------------
# Fee Invoice sheet parser
# ---------------------------------------------------------------------------

def _parse_fee_invoice_sheet(ws, name: str) -> tuple[dict, list[str]]:
    """Parse a Fee Invoice sheet.

    Layout (from actual file analysis):
        R2: C2="Name",           C3=insured_name
        R4: C2="Invoice Amount", C3=invoice_amount;  C5="Transaction Amount", C6=pay_amount
        R5: C2="Invoice Number", C3=invoice_number;  C5="Transaction Date",   C6=pay_date
        R6: C2="Invoice Date",   C3=invoice_date;    C5="Transaction #",      C6=reference

        R20+: SPLITS table — C4=label, C5=role, C6=name, C7=amount, C8=split_pct
    """
    warnings: list[str] = []

    m = re.match(r'^(\d+)\s*[_\-\s]\s*(.+)$', name)
    seq = int(m.group(1)) if m else 0

    tx: dict = {
        "seq":            seq,
        "type":           "Fee Invoice",
        "check_number":   "",
        "invoice_number": "",
        "received_date":  None,   # payment date (Transaction Date)
        "check_date":     None,   # invoice date
        "amount":         0.0,    # invoice amount
        "payer":          "",     # insured name
        "coverages":      [],
        "disbursements":  [],
        "splits":         [],
        "payment":        None,   # { amount, date, reference, method } or None
        "parse_warnings": warnings,
    }

    try:
        max_row = min(ws.max_row or 50, 100)

        # --- Labeled rows: col B (2) = label, col C (3) = value (left side)
        #                   col E (5) = label, col F (6) = value (right side)
        pay_amount_raw = None
        pay_date_raw   = None
        pay_ref_raw    = None

        for r in range(1, min(max_row + 1, 15)):
            label_b = _str(_cell_value(ws, r, 2)).upper()
            val_c   = _cell_value(ws, r, 3)
            label_e = _str(_cell_value(ws, r, 5)).upper()
            val_f   = _cell_value(ws, r, 6)

            if "INVOICE AMOUNT" in label_b and val_c is not None:
                tx["amount"] = round(abs(_clean_amount(val_c)), 2)
            elif "INVOICE NUMBER" in label_b and val_c is not None:
                tx["invoice_number"] = _clean_check_number(val_c)
            elif "INVOICE DATE" in label_b and val_c is not None:
                tx["check_date"] = _excel_date(val_c)
            elif ("NAME" in label_b or "INSURED" in label_b) and val_c is not None and not tx["payer"]:
                tx["payer"] = _str(val_c)

            if "TRANSACTION AMOUNT" in label_e and val_f is not None:
                pay_amount_raw = val_f
            elif "TRANSACTION DATE" in label_e and val_f is not None:
                pay_date_raw = val_f
                tx["received_date"] = _excel_date(val_f)
            elif "TRANSACTION #" in label_e and val_f is not None:
                pay_ref_raw = val_f

        # Build payment dict if payment amount is present
        pay_amt = abs(_clean_amount(pay_amount_raw)) if pay_amount_raw is not None else 0.0
        if pay_amt > 0:
            tx["payment"] = {
                "amount":    pay_amt,
                "date":      _excel_date(pay_date_raw) if pay_date_raw else tx["received_date"],
                "reference": _clean_check_number(pay_ref_raw) if pay_ref_raw is not None else "",
                "method":    "qb",
            }

        # --- Splits ---
        # SPLITS header at col D (4); role at +1 (E), name at +2 (F), pct at +4 (H)
        splits_row = None
        for r in range(15, min(max_row + 1, 40)):
            v = _cell_value(ws, r, 4)
            if v and "split" in str(v).lower():
                splits_row = r
                break

        if splits_row is not None:
            for r in range(splits_row + 1, min(max_row + 1, splits_row + 20)):
                role_v = _cell_value(ws, r, 5)   # col E
                name_v = _cell_value(ws, r, 6)   # col F
                pct_v  = _cell_value(ws, r, 8)   # col H

                if role_v is None and name_v is None:
                    continue
                role_s = _str(role_v).lower()
                name_s = _str(name_v)

                if not role_s and not name_s:
                    continue
                if role_s in ("role", "title", "splits", "name", "%", "split %", "amount"):
                    continue
                # Skip placeholder/instruction rows
                if "undistributed" in name_s.lower():
                    continue
                if len(name_s) > 80:
                    continue

                mapped_role = "other"
                for key, val in ROLE_MAP.items():
                    if key in role_s:
                        mapped_role = val
                        break

                pct = _clean_pct(pct_v) if pct_v is not None else 0.0

                if name_s or pct:
                    tx["splits"].append({
                        "role":      mapped_role,
                        "name":      name_s or role_s,
                        "split_pct": pct,
                    })

    except Exception as e:
        warnings.append(f"Fee invoice sheet '{name}' parse error: {e}")

    return tx, warnings


# ---------------------------------------------------------------------------
# Expense sheet parser
# ---------------------------------------------------------------------------

def _parse_expense_sheet(ws, name: str) -> tuple[dict, list[str]]:
    """Return (expense_data, warnings)."""
    warnings: list[str] = []

    m = re.match(r'^(\d+)\s*[_\-\s]\s*(.+)$', name)
    seq = int(m.group(1)) if m else 0

    exp: dict = {
        "seq":               seq,
        "payee_name":        "",
        "invoice_amount":    0.0,
        "invoice_date":      None,
        "invoice_number":    "",
        "responsible_party": "",
        "reason":            "",
        "client_amount":     None,   # None until parsed (0.0 is a valid value)
        "wp_amount":         None,
        "client_paid":       0.0,    # client paid to payee (R13)
        "wp_paid":           0.0,    # WP paid to payee (R14)
        "splits":            [],
        "parse_warnings":    warnings,
    }

    try:
        max_row = min(ws.max_row or 100, 150)
        max_col = min(ws.max_column or 10, 25)

        for r in range(1, max_row + 1):
            for c in range(1, max_col + 1):
                v = _cell_value(ws, r, c)
                if v is None:
                    continue
                vs = str(v).strip().lower()

                def _adj(row, col, count=5):
                    for off in range(1, count + 1):
                        nv = _cell_value(ws, row, col + off)
                        if nv is not None and str(nv).strip():
                            return nv
                    return None

                if ("payee" in vs or "pay to" in vs or
                        ("vendor" in vs and len(vs) < 20)) and not exp["payee_name"]:
                    nv = _adj(r, c)
                    if nv:
                        exp["payee_name"] = _str(nv)

                elif "amount" in vs and exp["invoice_amount"] == 0.0:
                    nv = _adj(r, c)
                    if nv is not None:
                        amt = _clean_amount(nv)
                        if amt != 0.0:
                            exp["invoice_amount"] = abs(amt)

                elif "invoice" in vs and "date" in vs and not exp["invoice_date"]:
                    nv = _adj(r, c)
                    if nv:
                        exp["invoice_date"] = _excel_date(nv)

                elif "date" in vs and "invoice" not in vs and not exp["invoice_date"]:
                    nv = _adj(r, c)
                    if nv:
                        exp["invoice_date"] = _excel_date(nv)

                elif "invoice" in vs and ("#" in vs or "no" in vs or "number" in vs) and not exp["invoice_number"]:
                    nv = _adj(r, c)
                    if nv:
                        exp["invoice_number"] = _str(nv)

                elif ("responsible" in vs or
                      ("party" in vs and "responsible" not in vs and len(vs) < 25)) and not exp["responsible_party"]:
                    nv = _adj(r, c)
                    if nv:
                        exp["responsible_party"] = _str(nv)

                elif ("reason" in vs or "description" in vs or "memo" in vs or
                      vs == "for") and not exp["reason"]:
                    nv = _adj(r, c)
                    if nv:
                        exp["reason"] = _str(nv)

                # "Client paid to payee" (R13) — must check before generic "client"
                elif "client" in vs and "paid" in vs and "payee" in vs and exp["client_paid"] == 0.0:
                    nv = _adj(r, c)
                    if nv is not None:
                        exp["client_paid"] = abs(_clean_amount(nv))

                # "WP paid to payee" (R14) — must check before generic "wp"
                elif ("wp" in vs or "waypoint" in vs) and "paid" in vs and "payee" in vs and exp["wp_paid"] == 0.0:
                    nv = _adj(r, c)
                    if nv is not None:
                        exp["wp_paid"] = abs(_clean_amount(nv))

                # "Client" responsible amount (R9) — exact label; value must be numeric
                # (avoids COMMENTS cell appearing to the right of "Client" label in R8)
                elif vs == "client" and exp["client_amount"] is None:
                    nv = _adj(r, c)
                    if isinstance(nv, (int, float)):
                        exp["client_amount"] = abs(float(nv))

                # "WP" responsible amount (R10) — exact label; value must be numeric
                elif vs == "wp" and exp["wp_amount"] is None:
                    nv = _adj(r, c)
                    if isinstance(nv, (int, float)):
                        exp["wp_amount"] = abs(float(nv))

        # Resolve None → 0.0 for amounts never found in the sheet
        if exp["client_amount"] is None:
            exp["client_amount"] = 0.0
        if exp["wp_amount"] is None:
            exp["wp_amount"] = 0.0

        # ---- Splits ----
        splits_cell = _find_cell(ws, "splits")
        if splits_cell:
            sr = splits_cell.row
            sc = splits_cell.column
            for r in range(sr + 1, min(max_row + 1, sr + 25)):
                role_v = _cell_value(ws, r, sc + 1)
                name_v = _cell_value(ws, r, sc + 2)
                pct_v  = _cell_value(ws, r, sc + 4)
                if role_v is None and name_v is None:
                    continue
                role_s = _str(role_v).lower()
                name_s = _str(name_v)
                if not role_s and not name_s:
                    continue
                if role_s in ("role", "title", "splits", "name", "%", "split %", "amount"):
                    continue
                if "undistributed" in name_s.lower():
                    continue
                mapped_role = "other"
                for key, val in ROLE_MAP.items():
                    if key in role_s:
                        mapped_role = val
                        break
                pct = _clean_pct(pct_v) if pct_v is not None else 0.0
                if name_s or pct:
                    exp["splits"].append({
                        "role":      mapped_role,
                        "name":      name_s or role_s,
                        "split_pct": pct,
                    })

    except Exception as e:
        warnings.append(f"Expense sheet '{name}' parse error: {e}")

    return exp, warnings


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def parse_workbook(wb) -> dict:
    """Parse a CAT Excel workbook. Returns structured dict.

    Structure:
    {
      "claim":        { insured_name, carrier, contract_pct, job_number, claim_number },
      "limits":       [{ coverage_type, base_limit, extended_limit }, ...],
      "assignees":    [{ role, name, split_pct }, ...],
      "transactions": [{ seq, type, check_number, received_date, check_date, amount,
                         payer, coverages, disbursements, splits, parse_warnings }, ...],
      "expenses":     [{ seq, payee_name, invoice_amount, invoice_date, invoice_number,
                         responsible_party, reason, parse_warnings }, ...],
      "parse_errors": [...],
    }
    """
    result: dict = {
        "claim": {
            "insured_name": "",
            "carrier":      "",
            "contract_pct": 0.0,
            "job_number":   "",
            "claim_number": "",
        },
        "limits":       [],
        "assignees":    [],
        "transactions": [],
        "expenses":     [],
        "parse_errors": [],
    }

    try:
        summary_ws, vendors_ws, tx_sheets, exp_sheets = _classify_sheets(wb)
    except Exception as e:
        result["parse_errors"].append(f"Failed to classify sheets: {e}")
        return result

    # Summary
    if summary_ws:
        try:
            claim, limits, warnings = _parse_summary_sheet(summary_ws)
            result["claim"] = claim
            result["limits"] = limits
            if warnings:
                result["parse_errors"].extend(warnings)
        except Exception as e:
            result["parse_errors"].append(f"Summary sheet error: {e}")
    else:
        result["parse_errors"].append("No summary sheet found (expected first sheet).")

    # Vendors / assignees
    if vendors_ws:
        try:
            assignees, warnings = _parse_vendors_sheet(vendors_ws)
            result["assignees"] = assignees
            if warnings:
                result["parse_errors"].extend(warnings)
        except Exception as e:
            result["parse_errors"].append(f"Vendors sheet error: {e}")

    # Transactions
    for seq, name, ws in tx_sheets:
        try:
            # Route fee invoice sheets to dedicated parser
            m_name = re.match(r'^(\d+)\s*[_\-\s]\s*(.+)$', name)
            type_raw = m_name.group(2).strip().lower() if m_name else ""
            if "fee" in type_raw:
                tx, _warnings = _parse_fee_invoice_sheet(ws, name)
            else:
                tx, _warnings = _parse_transaction_sheet(ws, name, wb=wb)
            result["transactions"].append(tx)
        except Exception as e:
            result["parse_errors"].append(f"Transaction sheet '{name}' error: {e}")
            result["transactions"].append({
                "seq": seq, "type": "Carrier", "check_number": "",
                "received_date": None, "check_date": None, "amount": 0.0,
                "payer": "", "coverages": [], "disbursements": [], "splits": [],
                "payment": None, "parse_warnings": [str(e)],
            })

    # Expenses
    for seq, name, ws in exp_sheets:
        try:
            exp, _warnings = _parse_expense_sheet(ws, name)
            result["expenses"].append(exp)
        except Exception as e:
            result["parse_errors"].append(f"Expense sheet '{name}' error: {e}")
            result["expenses"].append({
                "seq": seq, "payee_name": "", "invoice_amount": 0.0,
                "invoice_date": None, "invoice_number": "",
                "responsible_party": "", "reason": "",
                "parse_warnings": [str(e)],
            })

    return result
