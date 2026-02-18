# check_processor

Watches a folder for batch scan PDFs, splits them into one-per-check files (2 pages each), runs OCR to extract date/check number/coverage/amount, prompts for confirmation, and renames files using the convention:

```
YYYY-MM-DD check#NUMBER-COVERAGE-$AMOUNT.pdf
```

Original batch PDFs are archived to a `_raw/` subfolder and never deleted.

---

## Setup

### 1. Install system dependencies (Homebrew)

```bash
brew install tesseract poppler
```

### 2. Install Python dependencies

```bash
pip install watchdog pypdf pdf2image pytesseract Pillow
```

### 3. Configure your watch folder

Edit `tools/check_processor/config.py`:

```python
WATCH_FOLDER = "/path/to/your/Google Drive/Checks Inbox"
AUTO_MODE = False
```

- Set `WATCH_FOLDER` to the local path where scanned check PDFs land.
- Set `AUTO_MODE = True` to skip confirmation prompts once OCR is reliable.

---

## Usage

```bash
python tools/check_processor/main.py
```

Then drop a batch scan PDF (multi-page, 2 pages per check) into the watched folder.

The tool will:
1. Detect the new PDF
2. Split it into one file per check
3. Run OCR on each check
4. Show a confirmation prompt (unless `AUTO_MODE = True`):

```
─────────────────────────────────────────────
  Processing check 1 of 3
─────────────────────────────────────────────
  Date       [2026-01-15]  :
  Check #    [8842]        :
  Coverage   [building]    :
  Amount     [$12500.00]   :

→ 2026-01-15 check#8842-building-$12500.00.pdf
Save? [Y/n]:
```

Press **Enter** to accept a field. Type a new value to override it. At "Save?" press Enter or `y` to rename.

---

## Output

```
Checks Inbox/
    2026-02-18 check#8842-building-$12500.00.pdf
    2026-02-18 check#8843-ALE-$3200.00.pdf
    _raw/
        original_scan_batch.pdf
```

---

## Filename convention

- With coverage: `YYYY-MM-DD check#NUMBER-COVERAGE-$AMOUNT.pdf`
- Without coverage: `YYYY-MM-DD check#NUMBER-$AMOUNT.pdf`
- Multi-word coverage types use hyphens: `additional-living`, `loss-of-use`
