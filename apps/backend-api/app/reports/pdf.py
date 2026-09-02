"""Reusable multi-page PDF generator (pure stdlib, no external deps).

Produces a valid PDF/1.4 document from a title + flat list of text lines,
paginating automatically. Used by the reporting endpoints.
"""
from datetime import datetime, timezone
from typing import List


def _esc(value) -> str:
    text = str(value if value is not None else "")
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_pdf(title: str, lines: List[str], lines_per_page: int = 56) -> bytes:
    header = [
        title,
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
    ]
    all_lines = header + [str(l) for l in lines]
    pages = [all_lines[i:i + lines_per_page] for i in range(0, len(all_lines), lines_per_page)] or [[""]]
    n_pages = len(pages)

    objects: List[bytes] = []
    page_ids = [4 + i * 2 for i in range(n_pages)]
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode("ascii"))
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    for i, page_lines in enumerate(pages):
        content_id = 5 + i * 2
        y = 760
        content = ["BT", "/F1 10 Tf"]
        for ln in page_lines:
            content.append(f"1 0 0 1 72 {y} Tm ({_esc(ln)}) Tj")
            y -= 13
        content.append("ET")
        stream = "\n".join(content).encode("utf-8")
        page_obj = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>"
        ).encode("ascii")
        content_obj = b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream"
        objects.append(page_obj)
        objects.append(content_obj)

    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for idx, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{idx} 0 obj\n".encode("ascii"))
        pdf.extend(obj)
        pdf.extend(b"\nendobj\n")
    xref = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii")
    )
    return bytes(pdf)
