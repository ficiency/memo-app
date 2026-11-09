"""The memo as a PDF for committee: the PowerPoint the painter makes (the firm's layouts, the same fit), converted,
so the PDF reads exactly as the editable file does.

Conversion is behind one function with two engines: LibreOffice (headless, for a server) when it is installed,
else PowerPoint itself on a Windows machine that has it (driven through PowerShell). PDF_ENGINE chooses one
(libreoffice, powerpoint); left unset, the first available. One conversion at a time. The export is recorded
(format pdf, seconds) but is never what an edited PowerPoint is compared with: only PowerPoint exports are.
Nothing here knows any firm.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from app.db.history import Export
from app.db.memo import Asset, Memo
from app.db.session import SessionLocal
from app.services.painters.pptx import Painter

_LOCK = threading.Lock()
TIMEOUT_S = 240


def _soffice() -> str | None:
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    for path in (r"C:\Program Files\LibreOffice\program\soffice.exe", "/Applications/LibreOffice.app/Contents/MacOS/soffice"):
        if Path(path).exists():
            return path
    return None


def engine() -> str:
    """The converter this machine has: libreoffice or powerpoint (ValueError when none)."""
    wanted = os.environ.get("PDF_ENGINE", "").lower()
    if wanted in ("", "libreoffice") and _soffice():
        return "libreoffice"
    if wanted in ("", "powerpoint") and sys.platform == "win32":
        return "powerpoint"
    raise ValueError("no PDF converter here: install LibreOffice, or use a Windows machine with PowerPoint")


def convert(pptx: Path, pdf: Path) -> str:
    """PowerPoint file → PDF; returns the engine used."""
    pptx, pdf = pptx.resolve(), pdf.resolve()
    pdf.parent.mkdir(parents=True, exist_ok=True)
    used = engine()
    with _LOCK:
        if used == "libreoffice":
            subprocess.run([_soffice(), "--headless", "--convert-to", "pdf", "--outdir", str(pdf.parent), str(pptx)],
                           check=True, capture_output=True, timeout=TIMEOUT_S)
            made = pdf.parent / (pptx.stem + ".pdf")
            if made != pdf:
                made.replace(pdf)
        else:
            script = (f"$pp = New-Object -ComObject PowerPoint.Application; "
                      f"$p = $pp.Presentations.Open('{pptx}', $true, $false, $false); "
                      f"$p.SaveAs('{pdf}', 32); $p.Close(); $pp.Quit()")       # 32: ppSaveAsPDF
            subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True, capture_output=True, timeout=TIMEOUT_S)
    if not pdf.exists():
        raise ValueError("the PDF was not made")
    return used


def export_pdf(memo_id, out: Path) -> dict:
    """Paint the memo, convert it, record the export. Returns the timings."""
    started = time.perf_counter()
    pptx = out.with_suffix(".pptx")
    with SessionLocal() as session:
        memo = session.get(Memo, uuid.UUID(str(memo_id)))
        painted = Painter(session, memo.id).paint(pptx)
        paint_s = time.perf_counter() - started
        t = time.perf_counter()
        used = convert(pptx, out)
        convert_s = time.perf_counter() - t
        asset = Asset(id=uuid.uuid4(), firm_id=memo.firm_id, kind="export", storage_path=str(out), content_type="application/pdf")
        session.add(asset)
        session.flush()
        session.add(Export(id=uuid.uuid4(), memo_id=memo.id, sequence=memo.sequence or 0, format="pdf", asset_id=asset.id,
                           seconds=round(time.perf_counter() - started, 2)))
        session.commit()
    pptx.unlink(missing_ok=True)
    return {"engine": used, "paint_s": round(paint_s, 2), "convert_s": round(convert_s, 2),
            "seconds": round(time.perf_counter() - started, 2), "slides": painted.get("slides")}
