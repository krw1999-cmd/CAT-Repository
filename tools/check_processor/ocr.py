"""Claude API integration for check processing.

Two functions:
- find_check_fronts: uses Haiku to classify each page individually,
  returning the positions of check fronts in a batch scan.
- extract_fields: uses Sonnet Vision to extract date, check number,
  amount, coverage, and carrier from check images.
"""

import base64
import json
import os
import re
import time
from io import BytesIO
from typing import List

import anthropic
import numpy as np
from PIL import Image as _Image
from PIL import Image, ImageFilter
from config import MORTGAGE_COMPANIES


_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY environment variable not set.\n"
                "Run: export ANTHROPIC_API_KEY='your-key-here'"
            )
        _client = anthropic.Anthropic(api_key=api_key)
    return _client


def _image_to_base64(image: Image.Image) -> str:
    """Convert a PIL image to a base64-encoded JPEG string."""
    buf = BytesIO()
    image.convert("RGB").save(buf, format="JPEG", quality=90)
    return base64.standard_b64encode(buf.getvalue()).decode("utf-8")


def find_check_fronts(images: List[Image.Image]) -> List[int]:
    """Return 0-indexed positions of check front pages using Claude Haiku.

    Classifies each page individually so the model focuses on one image
    at a time — more reliable than batch classification across many pages.

    Cost: ~$0.002 per batch of ~14 pages.
    """
    client = _get_client()

    def thumbnail(img):
        w = 700
        h = int(img.height * w / img.width)
        resized = img.resize((w, h)).convert("L")
        # Strip security backgrounds with adaptive threshold so Haiku
        # sees clean text instead of noise on complex check patterns
        arr = np.array(resized, dtype=np.float32)
        local_mean = np.array(
            resized.filter(ImageFilter.GaussianBlur(radius=20)), dtype=np.float32
        )
        clean = np.where(arr < local_mean - 8, 0, 255).astype(np.uint8)
        return _Image.fromarray(clean).convert("RGB")

    check_fronts = []
    for i, img in enumerate(images):
        content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": _image_to_base64(thumbnail(img)),
                },
            },
            {
                "type": "text",
                "text": (
                    "Is this the FRONT of a paper check?\n"
                    "A check front has a 'Pay to the order of' line, a dollar amount, "
                    "and a bank or company name. The check may appear at the top, middle, "
                    "or bottom of the page. Insurance company and mortgage company checks "
                    "count as checks.\n"
                    "Reply with only 'yes' or 'no'."
                ),
            },
        ]

        for attempt in range(4):
            try:
                response = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=5,
                    messages=[{"role": "user", "content": content}],
                )
                answer = response.content[0].text.strip().lower()
                if answer.startswith("yes"):
                    check_fronts.append(i)
                break
            except Exception as e:
                if attempt < 3 and ("529" in str(e) or "overloaded" in str(e).lower()):
                    wait = 15 * (attempt + 1)
                    print(f"  API overloaded, retrying in {wait}s...")
                    time.sleep(wait)
                else:
                    raise

    return check_fronts


def extract_fields(images: List[Image.Image]) -> dict:
    """Send check images to Claude Vision and extract structured fields.

    Returns a dict with keys: date, check_number, amount, coverage.
    Any field not found returns an empty string.
    """
    client = _get_client()

    # Build image content blocks (front + back)
    image_blocks = []
    for img in images:
        image_blocks.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": _image_to_base64(img),
            },
        })

    mortgage_list = ", ".join(MORTGAGE_COMPANIES)
    image_blocks.append({
        "type": "text",
        "text": (
            "These are images of a paper check (front and back). "
            "Extract the following fields and reply in this exact format, "
            "one field per line, nothing else:\n"
            "DATE: MM/DD/YYYY\n"
            "CHECK_NUMBER: (the full check number exactly as printed, including any letters)\n"
            "AMOUNT: (dollar amount, e.g. 12500.00)\n"
            "CARRIER: (the name of the company that issued the check)\n"
            f"COVERAGE: (if the carrier is a mortgage servicer such as {mortgage_list} "
            "— use 'draw'. Otherwise extract from the memo/description using these rules:\n"
            "- anything with 'personal property' (including unscheduled personal property) → PP\n"
            "- anything with 'depreciation' (including recoverable depreciation) → deprec\n"
            "- building or dwelling → building\n"
            "- contents → contents\n"
            "- ALE, additional living, loss of use → ALE\n"
            "Leave blank if not found.)\n\n"
            "If you cannot find a field, leave it blank after the colon. "
            "Do not include any explanation."
        ),
    })

    for attempt in range(4):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=256,
                messages=[{"role": "user", "content": image_blocks}],
            )
            return _parse_response(response.content[0].text.strip())
        except Exception as e:
            if attempt < 3 and ("529" in str(e) or "overloaded" in str(e).lower()):
                wait = 10 * (attempt + 1)
                print(f"  API overloaded, retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise


def _parse_response(text: str) -> dict:
    """Parse Claude's structured response into a dict."""
    fields = {"date": "", "check_number": "", "amount": "", "coverage": "", "carrier": ""}

    for line in text.splitlines():
        line = line.strip()
        if line.startswith("DATE:"):
            raw_date = line.split(":", 1)[1].strip()
            fields["date"] = _normalize_date(raw_date)
        elif line.startswith("CHECK_NUMBER:"):
            fields["check_number"] = line.split(":", 1)[1].strip().replace(" ", "")
        elif line.startswith("AMOUNT:"):
            raw = line.split(":", 1)[1].strip().lstrip("$").replace(",", "")
            try:
                fields["amount"] = f"${float(raw):,.2f}"
            except ValueError:
                fields["amount"] = raw
        elif line.startswith("CARRIER:"):
            fields["carrier"] = line.split(":", 1)[1].strip()
        elif line.startswith("COVERAGE:"):
            fields["coverage"] = line.split(":", 1)[1].strip()

    return fields


def _normalize_date(raw: str) -> str:
    """Convert MM/DD/YYYY or similar to YYYY-MM-DD."""
    from datetime import datetime

    formats = ["%m/%d/%Y", "%m-%d-%Y", "%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"]
    raw = raw.strip().rstrip(",")
    for fmt in formats:
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw  # return as-is if we can't parse it
