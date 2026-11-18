"""Transmission report as a one-page PDF, written without any PDF library."""

from src.core.models import JournalEntry
from src.core.text import local_time

_TITLE = "TeleSaurier – Sendebericht"


def report_lines(entry: JournalEntry, route_label: str) -> list[tuple[str, str]]:
    lines = [
        ("Ergebnis", entry.status.label),
        ("Abgeschlossen", local_time(entry.updated_at)),
        ("Aufgegeben", local_time(entry.created_at)),
        ("Empfänger", entry.number),
        ("Betreff", entry.subject or "–"),
        ("Seiten", str(entry.pages) if entry.pages else "unbekannt"),
        ("Versuche", str(entry.attempts)),
        ("Fax-Weg", route_label),
        ("Benutzer", entry.user or "–"),
    ]
    if entry.ticket_id is not None:
        lines.append(("Ticket-ID", str(entry.ticket_id)))
    if entry.detail:
        lines.append(("Details", entry.detail))
    return lines


def _pdf_text(text: str) -> bytes:
    """Encode for a PDF string in WinAnsi (covers German); unknown characters become '?'."""
    raw = text.encode("cp1252", errors="replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def build_report(entry: JournalEntry, route_label: str) -> bytes:
    content = [b"BT /F2 16 Tf 56 780 Td (" + _pdf_text(_TITLE) + b") Tj ET"]
    y = 740
    for label, value in report_lines(entry, route_label):
        content.append(b"BT /F2 11 Tf 56 %d Td (%s) Tj ET" % (y, _pdf_text(label + ":")))
        content.append(b"BT /F1 11 Tf 170 %d Td (%s) Tj ET" % (y, _pdf_text(value[:70])))
        y -= 22
    stream = b"\n".join(content)
    font = b"<< /Type /Font /Subtype /Type1 /BaseFont /%s /Encoding /WinAnsiEncoding >>"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R /F2 6 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        font % b"Helvetica",
        font % b"Helvetica-Bold",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(pdf)
    pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        pdf += b"%010d 00000 n \n" % offset
    pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(pdf)
