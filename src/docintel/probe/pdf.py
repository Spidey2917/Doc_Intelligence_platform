"""PDF probe: page count, text-layer density, encryption, fillable forms."""

from __future__ import annotations

import io
import logging

from pypdf import PdfReader

from docintel.models import ArtifactRecord

logging.getLogger("pypdf").setLevel(logging.ERROR)

SAMPLE_PAGES = 6
# Below this many extracted characters per sampled page we call the PDF
# scanned. A cover page with a logo can dip under it, which is why several
# pages are sampled rather than the first one.
SCANNED_CHARS_PER_PAGE = 40.0


def probe_pdf(record: ArtifactRecord, data: bytes) -> None:
    reader = PdfReader(io.BytesIO(data), strict=False)
    record.is_encrypted = bool(reader.is_encrypted)
    if reader.is_encrypted:
        try:
            opened = reader.decrypt("")
        except Exception as exc:  # noqa: BLE001
            record.error = f"encrypted pdf: {type(exc).__name__}"
            return
        if not opened:
            record.error = "encrypted pdf: password required"
            return

    page_count = len(reader.pages)
    record.page_count = page_count
    if page_count == 0:
        return

    indices = sample_indices(page_count, SAMPLE_PAGES)
    chars = 0
    for i in indices:
        try:
            text = reader.pages[i].extract_text() or ""
        except Exception:  # noqa: BLE001 - one bad page is not a failed probe
            text = ""
        chars += len(text.strip())
    record.text_chars_per_page = round(chars / len(indices), 1)
    record.is_scanned = record.text_chars_per_page < SCANNED_CHARS_PER_PAGE

    try:
        root = getattr(reader, "root_object", None)
        if root is None:
            root = reader.trailer["/Root"]
        record.has_acroform = "/AcroForm" in root
    except Exception:  # noqa: BLE001
        record.has_acroform = None


def sample_indices(n: int, k: int) -> list[int]:
    """Up to k page indices spread evenly across the document, always including first and last."""
    if n <= k:
        return list(range(n))
    step = (n - 1) / (k - 1)
    return sorted({round(i * step) for i in range(k)})
