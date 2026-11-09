"""A memo moved to a newer version of its firm's spec (the brief: "a deal stays on its template version until
someone moves it; moving shows what changed and what needs a person").

A memo stays on the version it was built from until a person moves it. Moving follows the rules of every update
(docs/SCHEMA_DESIGN.md §8): it never deletes content.
- Slides: each goes to the new version's layout of the same name, and to the section of the same name. A slide
  whose layout the new version no longer has stays as it is (a pending says so).
- Boxes: a box made from a layout box goes to the new layout's box with the same role (and the same place among
  boxes of that role). A box nobody edited is built again from the new version (its style, place, recipe, table
  or chart); a box a person edited takes the new style and place and keeps its words. A box the new version no
  longer has is kept aside with what was in it when someone worked on it (a conflict, never lost). A box the new
  version adds is added. People's own boxes (on the slides the firm leaves free) stay as they are.
- The memo's stage goes to the new version's stage of the same name; sections it now requires and the memo lacks
  are pendings.
See changes runs the same move in a savepoint and undoes it, so it says exactly what Apply will do. Nothing here
knows any firm.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import spec as S
from app.db.history import Conflict, MemoUpdate
from app.db.memo import (ChartPoint, ChartSeries, Element, ElementChart, ElementParagraph, ElementTable,
                         Memo, Segment, Slide, TableCell, TableColumn, TableRow)
from app.services.generator.memo import COPY, Generator


def newer(session: Session, memo: Memo) -> S.SpecVersion | None:
    """The latest approved version of the memo's spec, if newer than the memo's and not ignored for it."""
    current = session.get(S.SpecVersion, memo.spec_version_id)
    latest = session.scalar(select(S.SpecVersion).where(S.SpecVersion.spec_id == current.spec_id, S.SpecVersion.status == "approved")
                            .order_by(S.SpecVersion.number.desc()))
    if latest is None or latest.number <= current.number:
        return None
    ignored = session.scalar(select(MemoUpdate.id).where(MemoUpdate.memo_id == memo.id, MemoUpdate.kind == "spec",
                                                         MemoUpdate.to_id == latest.id, MemoUpdate.status == "ignored"))
    return None if ignored else latest


def _key(elements: list[Element]) -> dict:
    """Layout box → (role, its place among the layout's boxes of that role), the same in every version."""
    out = {}
    groups = defaultdict(list)
    for e in elements:
        groups[e.role or e.name or e.type].append(e)
    for role, group in groups.items():
        for i, e in enumerate(sorted(group, key=lambda e: (e.y_in or 0, e.x_in or 0))):
            out[e.id] = (role, i)
    return out


def _text_of(session, el_id) -> str:
    paras = session.scalars(select(ElementParagraph).where(ElementParagraph.element_id == el_id).order_by(ElementParagraph.position)).all()
    return "\n".join("".join(s.text or "" for s in session.scalars(select(Segment).where(Segment.paragraph_id == p.id)
                                                                     .order_by(Segment.position))) for p in paras)


def _clear(session, ids: list) -> None:
    """The content (text, tables, charts) of these elements, not the elements: a few statements for all of them."""
    if not ids:
        return
    tables = select(ElementTable.id).where(ElementTable.element_id.in_(ids))
    cells = select(TableCell.id).where(TableCell.element_table_id.in_(tables))
    paras = select(ElementParagraph.id).where((ElementParagraph.element_id.in_(ids)) | (ElementParagraph.table_cell_id.in_(cells)))
    charts = select(ElementChart.id).where(ElementChart.element_id.in_(ids))
    series = select(ChartSeries.id).where(ChartSeries.element_chart_id.in_(charts))
    session.execute(delete(Segment).where(Segment.paragraph_id.in_(paras)))
    session.execute(delete(ElementParagraph).where((ElementParagraph.element_id.in_(ids)) | (ElementParagraph.table_cell_id.in_(cells))))
    for model in (TableCell, TableRow, TableColumn):
        session.execute(delete(model).where(model.element_table_id.in_(tables)))
    session.execute(delete(ElementTable).where(ElementTable.element_id.in_(ids)))
    session.execute(delete(ChartPoint).where(ChartPoint.chart_series_id.in_(series)))
    session.execute(delete(ChartSeries).where(ChartSeries.element_chart_id.in_(charts)))
    session.execute(delete(ElementChart).where(ElementChart.element_id.in_(ids)))


def move(session: Session, memo: Memo, new: S.SpecVersion, actor_id=None) -> dict:
    """Move the memo to the new version in this session (the caller commits, or rolls back for a preview).
    Returns what changed and what needs a person."""
    old = session.get(S.SpecVersion, memo.spec_version_id)
    if new.spec_id != old.spec_id or new.id == old.id:
        raise ValueError("that is not a newer version of this memo's spec")
    q = session.scalars
    old_layouts = {l.id: l for l in q(select(S.Layout).where(S.Layout.spec_version_id == old.id))}
    new_layouts = {l.name: l for l in q(select(S.Layout).where(S.Layout.spec_version_id == new.id))}
    old_sections = {x.id: x for x in q(select(S.Section).where(S.Section.spec_version_id == old.id))}
    new_sections = {x.name: x for x in q(select(S.Section).where(S.Section.spec_version_id == new.id))}
    stage = session.get(S.Stage, memo.stage_id) if memo.stage_id else None
    new_stage = session.scalar(select(S.Stage).where(S.Stage.spec_version_id == new.id, S.Stage.name == stage.name)) if stage else None
    if stage is not None and new_stage is None:
        raise ValueError(f"the new version has no stage {stage.name!r}")

    layout_boxes = defaultdict(list)                       # layout id → its boxes (both versions)
    for e in q(select(Element).where(Element.layout_id.in_(list(old_layouts) + [l.id for l in new_layouts.values()]))):
        layout_boxes[e.layout_id].append(e)
    key_of = {}
    for boxes in layout_boxes.values():
        key_of.update(_key(boxes))

    gen = Generator(session, session.get(S.Firm, memo.firm_id).name, stage.name if stage else "", memo.memo_date, memo.deal_team,
                    spec_version_id=new.id, revision_id=memo.record_revision_id)
    gen.memo = memo
    report = {"from": old.number, "to": new.number, "slides": 0, "rebuilt": 0, "restyled_kept_words": 0, "added": 0,
              "set_aside": [], "layouts_gone": [], "sections_gone": [], "removed_untouched": 0}

    def built(slide, src) -> tuple[Element, list]:
        """A layout box as the new version builds it on this slide: (the element, its content rows)."""
        start = len(gen.rows)
        gen.memo_data["page"] = str(slide.position + 1)
        gen.add_element(slide, src)
        rows = [r for r in gen.rows[start:] if type(r).__name__ != "Operation"]
        del gen.rows[start:]
        el = next(r for r in rows if isinstance(r, Element))
        return el, [r for r in rows if r is not el]

    cleared, rows_new, added_elements = [], [], []     # written at the end, all at once

    slides = q(select(Slide).where(Slide.memo_id == memo.id, Slide.deleted_at.is_(None)).order_by(Slide.position)).all()
    for slide in slides:
        old_layout = old_layouts.get(slide.layout_id)
        sec = old_sections.get(slide.section_id)
        if sec is not None:
            if sec.name in new_sections:
                slide.section_id = new_sections[sec.name].id
            else:
                report["sections_gone"].append(f"slide {slide.position + 1}: the section {sec.name!r} is not in the new version")
        layout = new_layouts.get(old_layout.name) if old_layout is not None else None
        if layout is None:
            if old_layout is not None:
                report["layouts_gone"].append(f"slide {slide.position + 1}: the layout {old_layout.name!r} is not in the new version; "
                                              f"it stays as it is")
            continue
        report["slides"] += 1
        slide.layout_id, slide.background_ref = layout.id, layout.background_ref
        slide.revision = (slide.revision or 0) + 1
        targets = {key_of[e.id]: e for e in layout_boxes[layout.id] if e.id in key_of}
        old_keys = {key_of[e.id] for e in layout_boxes.get(old_layout.id, []) if e.id in key_of}
        used = set()
        for el in q(select(Element).where(Element.slide_id == slide.id, Element.deleted_at.is_(None))).all():
            if el.origin_element_id is None:                     # a person's own box: stays as it is
                continue
            key = key_of.get(el.origin_element_id)
            src = targets.get(key) if key else None
            if src is None:
                if el.updated_by is not None:                    # someone's work: kept aside, never lost
                    text = _text_of(session, el.id)
                    session.add(Conflict(memo_id=memo.id, element_id=el.id, kind="spec_box_gone", status="open",
                                         detail=f"slide {slide.position + 1}: the box {(el.name or el.role or el.type)!r} is not in "
                                                f"spec v{new.number}" + (f"; it held: {text}" if text else "")))
                    report["set_aside"].append(f"slide {slide.position + 1} · {el.name or el.role or el.type}")
                else:
                    report["removed_untouched"] += 1
                el.deleted_at = datetime.now(timezone.utc)
                el.revision = (el.revision or 0) + 1
                continue
            used.add(src.id)
            fresh, rows = built(slide, src)
            edited_words = el.updated_by is not None and el.type == "text"
            for c in COPY:
                setattr(el, c, getattr(fresh, c))
            el.origin_element_id, el.revision = src.id, (el.revision or 0) + 1
            if edited_words:                                     # a person's words stay; the new style and place apply
                report["restyled_kept_words"] += 1
                continue
            cleared.append(el.id)
            for r in rows:                                       # the new content, on the same element (same identity)
                if getattr(r, "element_id", None) == fresh.id:
                    r.element_id = el.id
            rows_new.extend(rows)
            report["rebuilt"] += 1
        for src in layout_boxes[layout.id]:
            # a box the new version adds to this layout (not one a person removed from this slide)
            if src.id not in used and src.id in key_of and key_of[src.id] not in old_keys:
                fresh, rows = built(slide, src)
                added_elements.append(fresh)
                rows_new.extend(rows)
                report["added"] += 1
    session.flush()
    _clear(session, cleared)
    session.add_all(added_elements)
    session.flush()
    for cls in (ElementTable, TableColumn, TableRow, TableCell, ElementParagraph, Segment, ElementChart, ChartSeries, ChartPoint):
        session.add_all([r for r in rows_new if type(r) is cls])
        session.flush()
    memo.spec_version_id = new.id
    if new_stage is not None:
        memo.stage_id = new_stage.id
    session.flush()
    return report


def preview(session: Session, memo_id, version_id) -> dict:
    """What moving would do, without doing it."""
    memo = session.get(Memo, uuid.UUID(str(memo_id)))
    new = session.get(S.SpecVersion, uuid.UUID(str(version_id)))
    if new is None:
        raise ValueError("no such spec version")
    nested = session.begin_nested()
    try:
        return move(session, memo, new)
    finally:
        nested.rollback()
        session.rollback()
