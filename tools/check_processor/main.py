"""check_processor — main entry point.

Watches WATCH_FOLDER for new batch scan PDFs, splits them into per-check
files, runs OCR, and renames files automatically.

Scan order expected: [optional letter pages] [check front] [check back] [repeat]

- Check fronts detected via Claude Haiku (per-page classification, ~$0.002/batch)
- Fields extracted via Claude Sonnet Vision (~$0.05-0.10/batch)
- Letter + check pages merged into one output PDF per check
"""

import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from config import WATCH_FOLDER
from db import init_db, insert_check
from ocr import extract_fields, find_check_fronts
from pdf_utils import load_batch, save_merged_pdf

WATCH_PATH = Path(WATCH_FOLDER)
RAW_DIR = WATCH_PATH / "_raw"

_processed: set = set()
_db_conn = None

RENAMED_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2} check#", re.IGNORECASE)


def _build_filename(date: str, check_number: str, coverage: str, amount: str) -> str:
    coverage = coverage.strip().replace(" ", "-")
    try:
        amount_val = float(amount.lstrip("$").replace(",", ""))
        amount_fmt = f"${amount_val:,.2f}"
    except ValueError:
        amount_fmt = amount
    if coverage:
        return f"{date} check#{check_number}-{coverage}-{amount_fmt}.pdf"
    return f"{date} check#{check_number}-{amount_fmt}.pdf"


def _prompt_field(label: str) -> str:
    return input(f"  {label:<10} : ").strip()


def _process_group(
    reader, all_images: list, group: dict, check_index: int, total: int, conn=None
) -> None:
    print()
    print("─" * 45)
    has_letter = len(group["page_indices"]) > len(group["check_page_indices"])
    letter_count = len(group["page_indices"]) - len(group["check_page_indices"])
    label = f"  Check {check_index} of {total}"
    if has_letter:
        label += f"  (+{letter_count}-page letter)"
    print(label)
    print("─" * 45)

    # Send only check pages to Claude (letter pages cost nothing)
    check_images = [all_images[i] for i in group["check_page_indices"]]
    try:
        fields = extract_fields(check_images)
    except Exception as e:
        print(f"  ERROR in extract_fields: {e}")
        fields = {
            "date": "", "check_number": "", "amount": "", "coverage": "", "carrier": "",
            "payees": "", "client": "", "mortgage_co": "", "vendor": "",
            "insured_name": "", "claim_number": "",
            "policy_number": "", "loss_date": "", "loss_address": "", "bank": "", "memo": "",
        }

    date = fields["date"]
    check_number = fields["check_number"]
    coverage = fields["coverage"]
    amount = fields["amount"]
    carrier = fields.get("carrier", "")

    # Only prompt for the 4 filename fields Claude couldn't fill
    filename_fields = {"date", "check_number", "coverage", "amount"}
    missing = [k for k in filename_fields if not fields.get(k)]
    if missing:
        print(f"  Could not extract: {', '.join(missing)}")
        if not date:
            date = _prompt_field("Date")
        if not check_number:
            check_number = _prompt_field("Check #")
        if not coverage:
            coverage = _prompt_field("Coverage")
        if not amount:
            amount = _prompt_field("Amount")

    filename = _build_filename(date, check_number, coverage, amount)
    carrier_label = f"  [{carrier}]" if carrier else ""
    print(f"  → {filename}{carrier_label}")

    dest = WATCH_PATH / filename
    save_merged_pdf(reader, group["page_indices"], dest)
    _processed.add(dest)

    if conn is not None:
        record = {
            "file_name": filename,
            "check_date": date,
            "check_number": check_number,
            "coverage": coverage,
            "amount": amount,
            "payees": fields.get("payees", ""),
            "client": fields.get("client", ""),
            "mortgage_co": fields.get("mortgage_co", ""),
            "vendor": fields.get("vendor", ""),
            "insured_name": fields.get("insured_name", ""),
            "claim_number": fields.get("claim_number", ""),
            "policy_number": fields.get("policy_number", ""),
            "loss_date": fields.get("loss_date", ""),
            "loss_address": fields.get("loss_address", ""),
            "bank": fields.get("bank", ""),
            "memo": fields.get("memo", ""),
            "processed_at": datetime.now(timezone.utc).isoformat(),
        }
        insert_check(conn, record)
        print(f"  ✓ Saved to checks.db")


def process_batch(pdf_path: Path) -> None:
    if pdf_path in _processed:
        return
    if RENAMED_PATTERN.match(pdf_path.name):
        return

    _processed.add(pdf_path)
    print(f"\nDetected: {pdf_path.name}")

    try:
        reader, all_images = load_batch(pdf_path)
    except Exception as e:
        print(f"  Error reading PDF: {e}")
        return

    print(f"  {len(all_images)} pages — detecting checks...")
    check_fronts = find_check_fronts(all_images)
    print(f"  Check fronts on page(s): {[i+1 for i in check_fronts]}")

    # Build groups: letter pages before each check front + check front + next page (back)
    groups = []
    prev_end = 0
    for cf in check_fronts:
        letter_indices = list(range(prev_end, cf))
        check_indices = [cf]
        if cf + 1 < len(all_images):
            check_indices.append(cf + 1)
        prev_end = cf + 2

        groups.append({
            "page_indices": letter_indices + check_indices,
            "check_page_indices": check_indices,
        })

    total = len(groups)
    print(f"  {total} check(s) found — processing...")

    for i, group in enumerate(groups, start=1):
        try:
            _process_group(reader, all_images, group, i, total, conn=_db_conn)
        except KeyboardInterrupt:
            print("\n  Interrupted.")
            break

    # Archive original
    RAW_DIR.mkdir(exist_ok=True)
    archive_dest = RAW_DIR / pdf_path.name
    if archive_dest.exists():
        stem, suffix = pdf_path.stem, pdf_path.suffix
        counter = 1
        while archive_dest.exists():
            archive_dest = RAW_DIR / f"{stem}_{counter}{suffix}"
            counter += 1
    pdf_path.rename(archive_dest)
    print(f"\n  Done. Original archived → _raw/{archive_dest.name}")


def _is_batch_pdf(path: Path) -> bool:
    if path.suffix.lower() != ".pdf":
        return False
    if "_raw" in path.parts:
        return False
    if RENAMED_PATTERN.match(path.name):
        return False
    if path.name.startswith("_tmp_"):
        return False
    return True


def _scan_and_process() -> None:
    for path in sorted(WATCH_PATH.glob("*.pdf")):
        if _is_batch_pdf(path) and path not in _processed:
            time.sleep(1)
            process_batch(path)


def main():
    global _db_conn

    if not WATCH_PATH.exists():
        print(f"Error: WATCH_FOLDER does not exist: {WATCH_FOLDER}")
        print("Update WATCH_FOLDER in config.py and try again.")
        sys.exit(1)

    RAW_DIR.mkdir(exist_ok=True)
    _db_conn = init_db(WATCH_FOLDER)

    print("check_processor ready.")
    print(f"Watching: {WATCH_FOLDER}")
    print("Drop a batch scan PDF to begin. Ctrl+C to stop.\n")

    _scan_and_process()

    try:
        while True:
            time.sleep(5)
            _scan_and_process()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
