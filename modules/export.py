import io
from dataclasses import dataclass
from typing import List

@dataclass
class FailedFileRecord:
    filename: str
    reason: str

def build_failed_records(summary: dict) -> List[FailedFileRecord]:
    """Extract FailedFileRecord list from an ingest_folder() summary dict."""
    records = []
    for filename, info in summary.items():
        if info.get("added", 0) == 0:
            reason = info.get("error") or "No text could be extracted (image-only or empty document)"
            records.append(FailedFileRecord(filename=filename, reason=reason))
    return records

def generate_failed_files_excel(records: List[FailedFileRecord]) -> bytes:
    """Return an Excel workbook as bytes with one row per failed file."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, GradientFill, Color
    from openpyxl.styles.fills import FILL_NONE

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Failed Files"

    # Define styles
    no_fill = PatternFill(fill_type=None)  # Explicitly no fill
    black_bold_font = Font(bold=True, color="000000")
    black_font = Font(bold=False, color="000000")

    # Header row
    ws["A1"] = "File Name"
    ws["B1"] = "Reason for Not Ingesting"
    for cell in (ws["A1"], ws["B1"]):
        cell.font = black_bold_font
        cell.fill = no_fill

    # Data rows
    for row_idx, record in enumerate(records, start=2):
        cell_a = ws.cell(row=row_idx, column=1, value=record.filename)
        cell_b = ws.cell(row=row_idx, column=2, value=record.reason)
        cell_a.font = black_font
        cell_b.font = black_font
        cell_a.fill = no_fill
        cell_b.fill = no_fill

    ws.column_dimensions["A"].width = 42
    ws.column_dimensions["B"].width = 72

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()