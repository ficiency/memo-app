"""Where a slide leaves room for people's own boxes: each layout's free zone, worked out from the layout itself.

The brief: one-off slides get a bounded canvas, "inside a locked header, footer and sidebar". A layout's free
zone is what lies between its header (the boxes at the top: section label, title, rules, logo) and its footer
(source line, footer, page number), inside the firm's side margins. The boxes of the layout that fill that
space (a large content box) are not header or footer: they sit inside the zone. The master's boxes count for
layouts that show it. Snapping uses the firm's grid when it has one (a snap step), else 0.1 in.
Nothing here knows any firm.
"""
from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import spec as S
from app.db.memo import Element

SNAP_IN = 0.1
GAP_IN = 0.1                      # room kept from the header above and the footer below


def free_zone(page_w: float, page_h: float, boxes: list[Element], grid) -> tuple[float, float, float, float] | None:
    """(x, y, w, h) of the free zone in inches, or None when the layout leaves no room."""
    left = (grid.margin_left_in if grid and grid.margin_left_in is not None else 0.5)
    right = (grid.margin_right_in if grid and grid.margin_right_in is not None else 0.5)
    top_limit = (grid.margin_top_in if grid and grid.margin_top_in is not None else 0.4)
    bottom_limit = page_h - (grid.margin_bottom_in if grid and grid.margin_bottom_in is not None else 0.6)
    header = [b for b in boxes if (b.y_in or 0) + (b.h_in or 0) <= page_h * 0.35 and (b.h_in or 0) < page_h * 0.3]
    footer = [b for b in boxes if (b.y_in or 0) >= page_h * 0.75]
    top = max([top_limit] + [(b.y_in or 0) + (b.h_in or 0) + GAP_IN for b in header])
    bottom = min([bottom_limit] + [(b.y_in or 0) - GAP_IN for b in footer])
    if bottom - top < 1 or page_w - left - right < 1:
        return None
    return round(left, 2), round(top, 2), round(page_w - left - right, 2), round(bottom - top, 2)


def write_zones(session: Session, version_id) -> int:
    """Each layout's free zone for a spec version (written again each time)."""
    version = session.get(S.SpecVersion, version_id)
    layouts = session.scalars(select(S.Layout).where(S.Layout.spec_version_id == version_id)).all()
    master = next((l for l in layouts if l.is_master), None)
    grid = session.scalar(select(S.Grid).where(S.Grid.spec_version_id == version_id))
    elements = session.scalars(select(Element).where(Element.layout_id.in_([l.id for l in layouts]))).all()
    of = {}
    for e in elements:
        of.setdefault(e.layout_id, []).append(e)
    session.execute(delete(S.LayoutZone).where(S.LayoutZone.layout_id.in_([l.id for l in layouts])))
    n = 0
    for l in layouts:
        if l.is_master:
            continue
        boxes = of.get(l.id, []) + (of.get(master.id, []) if master is not None and l.shows_master else [])
        zone = free_zone(version.page_w_in, version.page_h_in, boxes, grid)
        if zone:
            x, y, w, h = zone
            session.add(S.LayoutZone(layout_id=l.id, kind="free", x_in=x, y_in=y, w_in=w, h_in=h))
            n += 1
    session.commit()
    return n


def snap(value: float, grid=None) -> float:
    step = grid.snap_in if grid is not None and grid.snap_in else SNAP_IN
    return round(round(value / step) * step, 3)
