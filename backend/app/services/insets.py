"""How the firm's template lays text out in each placeholder, as PowerPoint resolves it, kept on the boxes so the
canvas, the fit measure and the PowerPoint lay text out the same way:
- text insets: the layout placeholder's own, else the master placeholder's of the same kind, else PowerPoint's
  defaults (0.1 in left and right, 0.05 in top and bottom); kept on each box;
- paragraph format by level (indent, bullet or none, space before and after, line spacing): the layout
  placeholder's list style, else the master placeholder's, else the master's text style for its kind (title,
  body, other); kept on the box's paragraphs where they set none of their own.
Firms with no PowerPoint template have drawn boxes, which have none of this. Nothing here knows any firm.
"""
from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.enum.shapes import PP_PLACEHOLDER
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db import spec as S
from app.db.memo import Element, ElementParagraph

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


P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
STYLE_OF = {PP_PLACEHOLDER.TITLE: "titleStyle", PP_PLACEHOLDER.BODY: "bodyStyle"}


def _level(lst, level: int) -> dict:
    """A list style's paragraph format at a level (1-based): only what it sets."""
    if lst is None:
        return {}
    ppr = lst.find(f"{A}lvl{level}pPr")
    if ppr is None:
        return {}
    out = {}
    if ppr.get("marL") is not None:
        out["indent_in"] = round(int(ppr.get("marL")) / EMU_IN, 4)
    for tag, key in (("spcBef", "space_before_pt"), ("spcAft", "space_after_pt")):
        el = ppr.find(f"{A}{tag}/{A}spcPts")
        if el is not None:
            out[key] = int(el.get("val")) / 100
    ln = ppr.find(f"{A}lnSpc/{A}spcPct")
    if ln is not None:
        out["line_spacing"] = int(ln.get("val")) / 100000
    if ppr.find(f"{A}buNone") is not None:
        out["bullet_char"] = ""
    elif ppr.find(f"{A}buChar") is not None:
        out["bullet_char"] = ppr.find(f"{A}buChar").get("char")
    return out


def template_paragraphs(path: Path) -> dict:
    """(layout name, placeholder idx) → {level: paragraph format}, resolved as PowerPoint inherits it."""
    prs = Presentation(str(path))
    out = {}
    for master in prs.slide_masters:
        styles = master._element.find(f"{P}txStyles")
        on_master = {}
        for ph in master.placeholders:
            on_master.setdefault(KIND.get(ph.placeholder_format.type, ph.placeholder_format.type), ph._element.find(f".//{A}lstStyle"))
        for layout in master.slide_layouts:
            for ph in layout.placeholders:
                kind = KIND.get(ph.placeholder_format.type, ph.placeholder_format.type)
                own = ph._element.find(f".//{A}lstStyle")
                text_style = styles.find(f"{P}{STYLE_OF.get(kind, 'otherStyle')}") if styles is not None else None
                levels = {}
                for level in (1, 2, 3):
                    merged = {**_level(text_style, level), **_level(on_master.get(kind), level), **_level(own, level)}
                    if merged:
                        levels[level - 1] = merged
                out[(layout.name, ph.placeholder_format.idx)] = levels
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
    formats = template_paragraphs(templates[0])
    layouts = {l.id: l.name for l in session.scalars(select(S.Layout).where(S.Layout.spec_version_id == version.id))}
    n = 0
    boxes = session.scalars(select(Element).where(Element.layout_id.in_(list(layouts)), Element.placeholder_idx.is_not(None))).all()
    for el in boxes:
        ins = found.get((layouts[el.layout_id], el.placeholder_idx))
        if ins:
            el.inset_l_in, el.inset_t_in, el.inset_r_in, el.inset_b_in = ins
            n += 1
    by_box = {el.id: formats.get((layouts[el.layout_id], el.placeholder_idx)) or {} for el in boxes}
    for para in session.scalars(select(ElementParagraph).where(ElementParagraph.element_id.in_(list(by_box)))):
        _fill(para, by_box[para.element_id].get(para.level or 0, {}))
    session.commit()
    return n


def _fill(para, fmt: dict) -> None:
    """The template's paragraph format where the paragraph sets none of its own."""
    for key, value in fmt.items():
        if getattr(para, key) is None:
            setattr(para, key, value)


def copy_paragraph_formats(session: Session) -> int:
    """Memo paragraphs take their layout box's paragraph format where they set none (memos built before it was
    kept): the layout paragraph at the same level."""
    from app.db.memo import ElementParagraph as EP
    origin_of = dict(session.execute(select(Element.id, Element.origin_element_id).where(Element.origin_element_id.is_not(None))).all())
    layout_paras = {}
    for p in session.scalars(select(EP).where(EP.element_id.in_(set(origin_of.values())))):
        layout_paras.setdefault((p.element_id, p.level or 0), p)
    n = 0
    keys = ("indent_in", "space_before_pt", "space_after_pt", "line_spacing", "bullet_char")
    for p in session.scalars(select(EP).where(EP.element_id.in_(list(origin_of)))):
        src = layout_paras.get((origin_of[p.element_id], p.level or 0))
        if src is None:
            continue
        before = [getattr(p, k) for k in keys]
        _fill(p, {k: getattr(src, k) for k in keys if getattr(src, k) is not None})
        n += before != [getattr(p, k) for k in keys]
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
