"""A slide brought in from elsewhere (the brief: "upload a PPTX or PDF slide: native elements where possible, a
flagged picture where not"), as slides the firm leaves free.

Each slide (or PDF page) becomes a new slide of the section the firm leaves free, on its blank layout when it
has one (else its first layout with a free area), so the firm's header, footer and logo are there. Its content
is scaled into the free area:
- PPTX: the title goes in the layout's title box, in the firm's style; text, tables and charts come in as native
  boxes (their words and numbers as they were: typed numbers and the charts' numbers are pendings, since they are
  not the record's); pictures come in flagged; groups are opened. What cannot be brought in (shapes with no
  text, unsupported chart kinds, media) is listed, never dropped silently.
- PDF: each page comes in as a flagged picture.
Everything goes through the door (add_slide, add_element), as one change set. Nothing here knows any firm.
"""
from __future__ import annotations

import io
import math
import uuid
from collections import Counter
from pathlib import Path

from pptx import Presentation
from pptx.enum.chart import XL_CHART_TYPE
from pptx.enum.shapes import MSO_SHAPE_TYPE, PP_PLACEHOLDER
from sqlalchemy import select

from app.db import spec as S
from app.db.history import ChangeSet
from app.db.memo import Element, Memo, Slide
from app.db.session import SessionLocal
from app.services import storage
from app.services.operations.door import Door

MAX_SLIDES = 10
EMU_IN = 914400
CHARTS = {XL_CHART_TYPE.COLUMN_CLUSTERED: "column", XL_CHART_TYPE.COLUMN_STACKED: "column", XL_CHART_TYPE.BAR_CLUSTERED: "bar",
          XL_CHART_TYPE.BAR_STACKED: "bar", XL_CHART_TYPE.LINE: "line", XL_CHART_TYPE.LINE_MARKERS: "line"}
TITLES = (PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE)


def target(session, memo) -> tuple:
    """(section, layout, free area) for a slide from elsewhere: the section the firm leaves free, its blank layout
    when it has one, else its first layout with a free area."""
    sections = session.scalars(select(S.Section).where(S.Section.spec_version_id == memo.spec_version_id, S.Section.status == "free")
                               .order_by(S.Section.position)).all()
    zones = {z.layout_id: (z.x_in, z.y_in, z.w_in, z.h_in) for z in session.scalars(select(S.LayoutZone).where(S.LayoutZone.kind == "free"))}
    for sec in sections:
        layouts = [session.get(S.Layout, link.layout_id) for link in
                   session.scalars(select(S.SectionLayout).where(S.SectionLayout.section_id == sec.id))]
        layouts = [l for l in layouts if l is not None and l.id in zones]
        if layouts:
            layout = next((l for l in layouts if l.is_blank), layouts[0])
            return sec, layout, zones[layout.id]
    raise ValueError("the firm's spec has no section it leaves free, so a slide from elsewhere has nowhere to go")


class Placer:
    """Source coordinates (inches) into the free area: the content's bounds (x, y, w, h on the source page) scaled
    to fill it, keeping proportions, never larger than they were; then snapped inwards."""

    def __init__(self, bounds: tuple, zone: tuple, step: float):
        bx, by, bw, bh = bounds
        self.zx, self.zy, self.zw, self.zh = zone
        self.k = min(self.zw / max(bw, 0.01), self.zh / max(bh, 0.01), 1.0 if bw >= self.zw * 0.5 else 2.0)
        self.dx = self.zx + (self.zw - bw * self.k) / 2 - bx * self.k
        self.dy = self.zy - by * self.k
        self.step = step

    def frame(self, x, y, w, h) -> dict | None:
        x0, y0 = self.dx + x * self.k, self.dy + y * self.k
        x1, y1 = x0 + w * self.k, y0 + h * self.k
        up = lambda v: math.ceil(round(v / self.step, 6)) * self.step
        down = lambda v: math.floor(round(v / self.step, 6)) * self.step
        x0, y0 = max(up(x0), up(self.zx)), max(up(y0), up(self.zy))
        x1, y1 = min(down(x1), down(self.zx + self.zw)), min(down(y1), down(self.zy + self.zh))
        if x1 - x0 < 0.3 or y1 - y0 < 0.2:
            return None
        return {"x_in": round(x0, 3), "y_in": round(y0, 3), "w_in": round(x1 - x0, 3), "h_in": round(y1 - y0, 3)}


def _shapes(shapes, ox=0.0, oy=0.0, sx=1.0, sy=1.0):
    """Every shape with its frame in inches on the page; groups opened (their children placed as the group is)."""
    for sh in shapes:
        x = ox + (sh.left or 0) / EMU_IN * sx
        y = oy + (sh.top or 0) / EMU_IN * sy
        w = (sh.width or 0) / EMU_IN * sx
        h = (sh.height or 0) / EMU_IN * sy
        if sh.shape_type == MSO_SHAPE_TYPE.GROUP:
            xfrm = sh._element.grpSpPr.find("{http://schemas.openxmlformats.org/drawingml/2006/main}xfrm")
            off = xfrm.find("{http://schemas.openxmlformats.org/drawingml/2006/main}chOff") if xfrm is not None else None
            ext = xfrm.find("{http://schemas.openxmlformats.org/drawingml/2006/main}chExt") if xfrm is not None else None
            cx, cy = (int(off.get("x")) / EMU_IN, int(off.get("y")) / EMU_IN) if off is not None else (0, 0)
            cw, ch = (int(ext.get("cx")) / EMU_IN, int(ext.get("cy")) / EMU_IN) if ext is not None else (w or 1, h or 1)
            ksx, ksy = (w / cw if cw else 1), (h / ch if ch else 1)
            yield from _shapes(sh.shapes, x - cx * ksx, y - cy * ksy, ksx, ksy)
        else:
            yield sh, (x, y, w, h)


def _text(sh) -> str:
    return "\n".join("".join(r.text for r in p.runs) for p in sh.text_frame.paragraphs).strip()


def _title_role(session, layout) -> str | None:
    roles = [e.role for e in session.scalars(select(Element).where(Element.layout_id == layout.id, Element.type == "text"))
             if e.role and "title" in e.role and e.firm_lock != "fixed"]
    return roles[0] if roles else None


def upload(memo_id, data: bytes, filename: str, content_type: str | None, user) -> dict:
    """Bring a PPTX's slides (or a PDF's pages) in as free slides at the end of the memo. Returns what came in,
    what was refused and what could not be brought in."""
    suffix = Path(filename or "").suffix.lower()
    is_pdf = suffix == ".pdf" or (content_type or "").endswith("pdf")
    if not is_pdf and suffix != ".pptx":
        raise ValueError("upload a PowerPoint (.pptx) or a PDF")
    with SessionLocal() as session:
        memo = session.get(Memo, uuid.UUID(str(memo_id)))
        sec, layout, zone = target(session, memo)
        grid = session.scalar(select(S.Grid).where(S.Grid.spec_version_id == memo.spec_version_id))
        step = grid.snap_in if grid is not None and grid.snap_in else 0.1
        title_role = _title_role(session, layout)
        cs = ChangeSet(id=uuid.uuid4(), memo_id=memo.id, actor_type="person", on_behalf_of=user.id, summary=f"Uploaded {filename}",
                       status="applied")
        session.add(cs)
        session.commit()
        door = Door(session, memo.id)
        report = {"slides": 0, "boxes": Counter(), "pictures": 0, "skipped": [], "refused": []}

        def apply(op) -> bool:
            r = door.apply(op, actor_type="person", actor_id=user.id, change_set_id=cs.id)
            if r.status != "applied":
                report["refused"].append(r.reason or r.status)
            return r.status == "applied"

        def new_slide(texts: dict | None) -> str | None:
            """A slide of the free section at the end of the memo, its title written as it is added."""
            position = len(session.scalars(select(Slide.id).where(Slide.memo_id == memo.id, Slide.deleted_at.is_(None))).all())
            sid = str(uuid.uuid4())
            ok = apply({"type": "add_slide", "target": str(memo.id), "payload": {
                "id": sid, "section_id": str(sec.id), "layout_id": str(layout.id), "position": position, **({"texts": texts} if texts else {})}})
            if not ok:
                return None
            report["slides"] += 1
            # the layout's content box (inside the free area: "free content here") gives way to what came in
            zx, zy, zw, zh = zone
            for e in session.scalars(select(Element).where(Element.slide_id == uuid.UUID(sid), Element.deleted_at.is_(None),
                                                           Element.type == "text", Element.firm_lock == "content")):
                inside = e.x_in >= zx - 0.05 and e.y_in >= zy - 0.05 and e.x_in + e.w_in <= zx + zw + 0.05 and e.y_in + e.h_in <= zy + zh + 0.05
                if inside and e.role != title_role:
                    apply({"type": "delete_element", "target": str(e.id), "base_revision": e.revision})
            return sid

        def picture(sid, blob: bytes, ctype: str | None, frame: dict, alt: str) -> None:
            try:
                pic, ct = storage.as_picture(blob, ctype)
            except ValueError:
                report["skipped"].append(f"{alt}: not a picture")
                return
            asset = storage.put(session, pic, "image", memo.firm_id, ct, alt)
            session.commit()
            if apply({"type": "add_element", "target": sid, "payload": {"slide_id": sid, "type": "image", "asset_id": str(asset.id),
                                                                       "alt": alt, **frame}}):
                report["pictures"] += 1

        if is_pdf:
            import fitz
            doc = fitz.open(stream=data, filetype="pdf")
            for number, page in enumerate(doc):
                if number >= MAX_SLIDES:
                    report["skipped"].append(f"pages after {MAX_SLIDES} (upload them separately)")
                    break
                sid = new_slide(None)
                if sid is None:
                    break
                pw, ph = page.rect.width / 72, page.rect.height / 72
                placer = Placer((0, 0, pw, ph), zone, step)
                frame = placer.frame(0, 0, pw, ph)
                picture(sid, page.get_pixmap(dpi=150).tobytes("png"), "image/png", frame, f"{filename} page {number + 1}")
        else:
            prs = Presentation(io.BytesIO(data))
            page_w, page_h = prs.slide_width / EMU_IN, prs.slide_height / EMU_IN
            for number, slide in enumerate(prs.slides):
                if number >= MAX_SLIDES:
                    report["skipped"].append(f"slides after {MAX_SLIDES} (upload them separately)")
                    break
                shapes = list(_shapes(slide.shapes))
                title = next((sh for sh, _ in shapes if sh.is_placeholder and sh.placeholder_format.type in TITLES and sh.has_text_frame), None)
                texts = {title_role: _text(title)} if title is not None and title_role and _text(title) else None
                sid = new_slide(texts)
                if sid is None:
                    break
                content = [(x, y, w, h) for sh, (x, y, w, h) in shapes
                           if not (texts and sh is title) and w > 0 and h > 0 and _brought(sh)]
                bounds = (min(c[0] for c in content), min(c[1] for c in content),
                          max(c[0] + c[2] for c in content) - min(c[0] for c in content),
                          max(c[1] + c[3] for c in content) - min(c[1] for c in content)) if content else (0, 0, page_w, page_h)
                placer = Placer(bounds, zone, step)
                where = f"slide {number + 1}"
                for sh, (x, y, w, h) in shapes:
                    if texts and sh is title:
                        continue
                    frame = placer.frame(x, y, w, h)
                    name = sh.name or "a shape"
                    if frame is None:
                        report["skipped"].append(f"{where} · {name}: too small to place")
                        continue
                    payload = {"slide_id": sid, **frame}
                    if sh.shape_type == MSO_SHAPE_TYPE.PICTURE:
                        picture(sid, sh.image.blob, sh.image.content_type, frame, f"{filename} {where} {name}")
                        continue
                    if getattr(sh, "has_table", False) and sh.has_table:
                        cells = [[_text_cell(c) for c in row.cells] for row in sh.table.rows]
                        op_payload = {**payload, "type": "table", "cells": cells}
                    elif getattr(sh, "has_chart", False) and sh.has_chart:
                        kind = CHARTS.get(sh.chart.chart_type)
                        if kind is None:
                            report["skipped"].append(f"{where} · {name}: a {str(sh.chart.chart_type).split('.')[-1].split(' ')[0].lower()} chart "
                                                     f"(rebuild it from the record, or bring it as a picture)")
                            continue
                        plot = sh.chart.plots[0]
                        data_ = {"categories": [str(c) for c in plot.categories],
                                 "series": [{"name": s.name, "values": list(s.values)} for p in sh.chart.plots for s in p.series]}
                        op_payload = {**payload, "type": "chart", "chart_type": kind, "data": data_}
                    elif sh.has_text_frame and _text(sh):
                        op_payload = {**payload, "type": "text", "text": _text(sh)}
                    else:
                        report["skipped"].append(f"{where} · {name}: a shape with no text (decoration)")
                        continue
                    if apply({"type": "add_element", "target": sid, "payload": op_payload}):
                        report["boxes"][op_payload["type"]] += 1
        cs.summary = f"Uploaded {filename}: {report['slides']} slide(s)"
        session.commit()
        report["boxes"] = dict(report["boxes"])
        return report


def _brought(sh) -> bool:
    """Whether a shape comes in (a picture, a table, a chart of a kind the memo draws, text); the others are listed."""
    if sh.shape_type == MSO_SHAPE_TYPE.PICTURE or (getattr(sh, "has_table", False) and sh.has_table):
        return True
    if getattr(sh, "has_chart", False) and sh.has_chart:
        return sh.chart.chart_type in CHARTS
    return bool(sh.has_text_frame and _text(sh))


def _text_cell(cell) -> str:
    return "\n".join("".join(r.text for r in p.runs) for p in cell.text_frame.paragraphs).strip()
