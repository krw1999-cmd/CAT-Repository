"""Carrier profile management — load, save, detect, and region-OCR."""

import json
import re
from pathlib import Path

from PIL import Image
import pytesseract

CARRIERS_FILE = Path(__file__).parent / "carriers.json"


def load_profiles() -> dict:
    if CARRIERS_FILE.exists():
        with open(CARRIERS_FILE) as f:
            return json.load(f)
    return {}


def save_profiles(profiles: dict) -> None:
    with open(CARRIERS_FILE, "w") as f:
        json.dump(profiles, f, indent=2)


def detect_carrier(text: str, profiles: dict) -> str:
    """Search OCR text for a known carrier name. Returns the profile key or ''."""
    text_lower = text.lower()
    for name in profiles:
        if name.lower() in text_lower:
            return name
    return ""


def find_text_region(image: Image.Image, target_text: str) -> dict:
    """Locate target_text in the image using pytesseract word data.

    Uses case-insensitive substring matching to handle minor OCR variations.
    Returns a region dict (values as fractions of image dimensions) or None.
    """
    w, h = image.size
    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
    target = target_text.strip().lower()

    best = None

    for i, word in enumerate(data["text"]):
        word_clean = word.strip().lower()
        if not word_clean:
            continue

        # Exact match preferred; also accept if one contains the other
        if word_clean == target or target in word_clean or word_clean in target:
            x = data["left"][i]
            y = data["top"][i]
            bw = data["width"][i]
            bh = data["height"][i]
            pad_x = int(w * 0.06)
            pad_y = int(h * 0.03)
            region = {
                "left": max(0.0, (x - pad_x) / w),
                "top": max(0.0, (y - pad_y) / h),
                "width": min(1.0, (bw + pad_x * 2) / w),
                "height": min(1.0, (bh + pad_y * 2) / h),
            }
            if word_clean == target:
                return region  # exact match — done
            best = region  # partial match — keep looking for better

    return best


def ocr_region(image: Image.Image, region: dict) -> str:
    """OCR a specific region of an image. Region values are fractions of image size."""
    w, h = image.size
    left = int(region["left"] * w)
    top = int(region["top"] * h)
    right = int((region["left"] + region["width"]) * w)
    bottom = int((region["top"] + region["height"]) * h)
    cropped = image.crop((left, top, right, bottom))
    return pytesseract.image_to_string(cropped).strip()


def extract_check_number_from_region(image: Image.Image, region: dict) -> str:
    """OCR the stored region and return the first 4–8 digit number found."""
    text = ocr_region(image, region)
    match = re.search(r"\b(\d{4,8})\b", text)
    return match.group(1) if match else ""


def extract_coverage_from_region(image: Image.Image, region: dict) -> str:
    """OCR the stored coverage region and match against known coverage keywords."""
    from ocr import COVERAGE_KEYWORDS, _parse_coverage
    text = ocr_region(image, region)
    result = _parse_coverage(text)
    # Fall back to raw OCR text if no keyword matched
    return result if result else text.strip()
