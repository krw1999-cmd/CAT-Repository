from pathlib import Path
from typing import List

from pypdf import PdfReader, PdfWriter
from pdf2image import convert_from_path
from PIL import Image


def load_batch(path: Path):
    """Load a batch PDF and return (reader, images at 300 DPI)."""
    reader = PdfReader(str(path))
    images = convert_from_path(str(path), dpi=300)
    return reader, images


def save_merged_pdf(reader: PdfReader, page_indices: List[int], dest: Path) -> None:
    """Write a PDF from selected pages of the source reader."""
    writer = PdfWriter()
    for idx in page_indices:
        writer.add_page(reader.pages[idx])
    with open(dest, "wb") as f:
        writer.write(f)
