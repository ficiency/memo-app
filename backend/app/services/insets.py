"""Text insets of the firm's template boxes, as PowerPoint resolves them: a layout placeholder's own, else the
master placeholder's of the same kind, else PowerPoint's defaults (0.1 in left and right, 0.05 in top and
bottom). Kept on each layout box (and copied to the memo's boxes made from it), so the canvas, the fit measure
and the PowerPoint lay text out in the same width. Firms with no PowerPoint template have drawn boxes, which
have none. Nothing here knows any firm.
"""
from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.enum.shapes import PP_PLACEHOLDER
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db import spec as S
from app.db.memo import Element

EMU_IN = 914400
DEFAULTS = {"lIns": 91440, "tIns": 45720, "rIns": 91440, "bIns": 45720}
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
KIND = {PP_PLACEHOLDER.CENTER_TITLE: PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.SUBTITLE: PP_PLACEHOLDER.BODY,
        PP_PLACEHOLDER.OBJECT: PP_PLACEHOLDER.BODY}


def _body(shape) -> dict:
    tx = shape._element.find(".//{http://schemas.openxmlformats.org/presentationml/2006/main}txBody")
    body = tx.find(f"{A}bodyPr") if tx is not None else None
    return {k: int(body.get(k)) for k in DEFAULTS if body is not None and body.get(k) is not None}


def template_insets(path: Path) -> dict:
    """(layout name, placeholder idx) → (left, top, right, bottom) in inches."""
    prs = Presentation(str(path))
    out = {}
    for master in prs.slide_masters:
        on_master = {}
        for ph in master.placeholders:
            on_master.setdefault(KIND.get(ph.placeholder_format.type, ph.placeholder_format.type), _body(ph))
        for layout in master.slide_layouts:
            for ph in layout.placeholders:
                kind = KIND.get(ph.placeholder_format.type, ph.placeholder_format.type)
                own, inherited = _body(ph), on_master.get(kind, {})
                v = {k: own.get(k, inherited.get(k, d)) for k, d in DEFAULTS.items()}
                out[(layout.name, ph.placeholder_format.idx)] = tuple(round(v[k] / EMU_IN, 4) for k in ("lIns", "tIns", "rIns", "bIns"))
    return out


def write_insets(session: Session, version_id) -> int:
    """Each placeholder box of a spec version's layouts gets its template's insets; the count written."""
    from app.services.painters.pptx import materials
    version = session.get(S.SpecVersion, version_id)
    firm = session.get(S.Firm, session.get(S.Spec, version.spec_id).firm_id)
    templates = materials(firm.name).get("template") or []
    if not templates:
        return 0
    found = template_insets(templates[0])
    layouts = {l.id: l.name for l in session.scalars(select(S.Layout).where(S.Layout.spec_version_id == version.id))}
    n = 0
    for el in session.scalars(select(Element).where(Element.layout_id.in_(list(layouts)), Element.placeholder_idx.is_not(None))):
        ins = found.get((layouts[el.layout_id], el.placeholder_idx))
        if ins:
            el.inset_l_in, el.inset_t_in, el.inset_r_in, el.inset_b_in = ins
            n += 1
    session.commit()
    return n


def copy_to_memos(session: Session) -> int:
    """Memo boxes made from a layout box take its insets (for memos built before insets were kept)."""
    origin = Element.__table__.alias("origin")
    stmt = (update(Element.__table__)
            .where(Element.__table__.c.origin_element_id == origin.c.id, origin.c.inset_l_in.is_not(None))
            .values(inset_l_in=origin.c.inset_l_in, inset_t_in=origin.c.inset_t_in, inset_r_in=origin.c.inset_r_in,
                    inset_b_in=origin.c.inset_b_in))
    n = session.execute(stmt).rowcount
    session.commit()
    return n
