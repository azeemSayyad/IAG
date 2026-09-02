"""Counter-stamp the agency's authorized signature onto a completed agreement.

The SignWell template intentionally leaves the "Company Representative /
Authorized Signature" line blank — the agency must NOT appear signed before the
agent signs. After the agent completes the document we overlay the agency's
signature image (PNG, transparent bg) onto that line here, so the downloaded
copy is executed by both parties.

Best-effort: if the image or PDF libs are missing, we log and return the
original bytes unchanged rather than failing the agent's signing flow.
"""
from __future__ import annotations

import io
import logging
import os
from datetime import date

from app.core.config import settings

logger = logging.getLogger(__name__)

# Page-relative placement of the agency signature (tunable). The agreement is
# US-Letter; the company-rep line sits in the right column of the signature page.
# Fractions are measured from the LEFT and TOP of the page.
_SIG_X_FRAC = 0.55          # left edge of the stamp
_SIG_BASELINE_FRAC = 0.345  # vertical baseline (where the signature line is)
_SIG_WIDTH_PT = 165.0       # drawn width in points
_SIG_MAX_HEIGHT_PT = 46.0   # cap height; aspect ratio preserved
_MATCH_TEXT = "authorized signature"


def _signature_path() -> str:
    p = settings.SIGNWELL_AGENCY_SIGNATURE_PATH or ""
    if not p:
        return ""
    if os.path.isabs(p):
        return p
    # Resolve relative to the backend-api root (parent of the app/ package).
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, p)


def stamp_agency_signature(pdf_bytes: bytes) -> bytes:
    """Return the agreement PDF with the agency signature overlaid. Best-effort."""
    sig_path = _signature_path()
    if not sig_path or not os.path.exists(sig_path):
        logger.warning("Agency signature image not found (%s) — leaving agreement un-counter-signed", sig_path)
        return pdf_bytes

    try:
        from pypdf import PdfReader, PdfWriter
        from reportlab.lib.utils import ImageReader
        from reportlab.pdfgen import canvas
    except Exception as exc:  # pragma: no cover - missing optional deps
        logger.error("PDF libs unavailable (%s) — cannot stamp agency signature", exc)
        return pdf_bytes

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        target_idx = _find_signature_page(reader)
        page = reader.pages[target_idx]
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)

        img = ImageReader(sig_path)
        iw, ih = img.getSize()
        draw_w = _SIG_WIDTH_PT
        draw_h = draw_w * (ih / iw) if iw else _SIG_MAX_HEIGHT_PT
        if draw_h > _SIG_MAX_HEIGHT_PT:
            draw_h = _SIG_MAX_HEIGHT_PT
            draw_w = draw_h * (iw / ih) if ih else _SIG_WIDTH_PT

        x = width * _SIG_X_FRAC
        y = height * (1.0 - _SIG_BASELINE_FRAC)  # reportlab origin is bottom-left

        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=(width, height))
        c.drawImage(img, x, y, width=draw_w, height=draw_h, mask="auto", preserveAspectRatio=True)
        # Date next to the signature.
        c.setFont("Helvetica", 9)
        c.drawString(x, y - 12, date.today().strftime("%m/%d/%Y"))
        c.save()
        buf.seek(0)

        overlay = PdfReader(buf).pages[0]
        writer = PdfWriter()
        for i, p in enumerate(reader.pages):
            if i == target_idx:
                p.merge_page(overlay)
            writer.add_page(p)

        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()
    except Exception as exc:  # never break finalize over a stamp
        logger.exception("Failed to stamp agency signature: %s", exc)
        return pdf_bytes


def _find_signature_page(reader) -> int:
    """Index of the page holding the company-rep line; falls back to last page."""
    for i, page in enumerate(reader.pages):
        try:
            text = (page.extract_text() or "").lower()
        except Exception:
            text = ""
        if _MATCH_TEXT in text:
            return i
    return len(reader.pages) - 1
