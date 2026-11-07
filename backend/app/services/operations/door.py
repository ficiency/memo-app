"""The one door every edit goes through: canvas, agent, PowerPoint import and updates.

An operation is checked on the server before it lands. It is applied, refused (it breaks a rule of the firm
or changes a record figure) or kept aside (it was made on an older version of the element, so applying it
would overwrite someone else's change). Every operation is logged with its actor and reason; nothing is
lost. Nothing here knows any firm: locks, palette and section order come from the memo's spec.

Operation: {"type": ..., "target": <element or slide id>, "base_revision": <the revision the editor saw>,
            "payload": {...}}
Types: set_text, set_frame, set_style, add_element, delete_element, add_slide, duplicate_slide, move_slide,
       delete_slide, set_user_lock.
"""
from __future__ import annotations

import copy
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import spec as S
from app.db.history import Conflict, Operation
from app.db.memo import (ChartPoint, ChartSeries, Element, ElementChart, ElementParagraph, ElementTable, Memo,
                         Segment, Slide, TableCell, TableColumn, TableRow)

FRAME = ("x_in", "y_in", "w_in", "h_in", "rotation_deg")
STYLE = ("font", "size_pt", "bold", "italic", "caps", "color_ref", "align", "anchor")
COLOUR_FIELDS = ("color_ref", "fill_ref", "line_ref", "bullet_color_ref")
SLOTS = ("model_draft", "person_slot", "conditional")


@dataclass
class Result:
    status: str                    # applied, refused, kept_aside
    reason: str | None
    operation_id: uuid.UUID
    sequence: int
    created: list | None = None    # ids made by the operation (new element, new slide…)
    element: dict | None = None    # the element as it is now, for everyone to update their view
    slides: list | None = None     # the memo's slide order now, after a slide operation


class Refuse(Exception):
    pass


class KeepAside(Exception):
    def __init__(self, message: str, kind: str | None = None):
        super().__init__(message)
        self.kind = kind                     # what was kept aside (deleted_box, added_box…); None = a stale edit


class Door:
    def __init__(self, session: Session, memo_id):
        self.s = session
        self.memo = session.get(Memo, memo_id)
        if self.memo is None:
            raise ValueError(f"no memo {memo_id}")
        v = self.memo.spec_version_id
        self.palette = {p.name.lower(): p.hex.upper() for p in session.scalars(select(S.PaletteColor).where(S.PaletteColor.spec_version_id == v))}
        self.sections = {x.id: x for x in session.scalars(select(S.Section).where(S.Section.spec_version_id == v))}
        self.constraints = session.scalars(select(S.OrderConstraint).where(S.OrderConstraint.spec_version_id == v)).all()

    # --- entry point ----------------------------------------------------------------------------
    def apply(self, op: dict, actor_type: str = "person", actor_id=None, on_behalf_of=None, change_set_id=None,
              held_by: str | None = None) -> Result:
        """held_by: who is editing the target element right now (real time), when it is not the actor."""
        # One operation at a time per memo: lock the memo's row until this operation commits, so two edits that
        # arrive together cannot take the same sequence or both pass the same element revision.
        self.memo = self.s.get(Memo, self.memo.id, with_for_update=True, populate_existing=True)
        kind = op.get("type")
        handler = getattr(self, f"_{kind}", None)
        created: list = []
        status, reason, aside_kind = "applied", None, None
        try:
            if handler is None:
                raise Refuse(f"unknown operation {kind!r}")
            if held_by and self._is_element(op):
                raise Refuse(f"{held_by} is editing this box")
            created = handler(op, actor_type, actor_id) or []
        except Refuse as e:
            status, reason = "refused", str(e)
        except KeepAside as e:
            status, reason, aside_kind = "kept_aside", str(e), e.kind
        self.memo.sequence = (self.memo.sequence or 0) + 1
        if status == "applied":
            self.memo.status = "editing"                     # any change after a final review reopens the memo
        row = Operation(id=uuid.uuid4(), memo_id=self.memo.id, sequence=self.memo.sequence, change_set_id=change_set_id,
                        actor_type=actor_type, actor_id=actor_id, on_behalf_of=on_behalf_of, op_type=kind or "?",
                        target_id=_uuid(op.get("target")), base_revision=op.get("base_revision"),
                        payload=_jsonable(op.get("payload") or {}), status=status, reason=reason)
        self.s.add(row)
        self.s.flush()
        if status == "kept_aside":
            self.s.add(Conflict(memo_id=self.memo.id, operation_id=row.id, element_id=_uuid(op.get("target")) if self._is_element(op) else None,
                                kind=aside_kind or ("changed_since_export" if actor_type == "import" else "changed_since_seen"),
                                detail=reason, status="open"))
        self.s.commit()
        element = slides = None
        if status == "applied" and self._is_element(op):
            el = self.s.get(Element, _uuid(op.get("target")))
            element = self.state(el) if el is not None else None
        elif status == "applied" and op.get("type") in ("add_element",) and created:
            element = self.state(self.s.get(Element, created[0]))
        elif status == "applied" and "slide" in str(op.get("type")):
            slides = [{"id": str(x.id), "position": x.position, "section_id": str(x.section_id) if x.section_id else None,
                       "revision": x.revision} for x in self.order()]
        return Result(status, reason, row.id, row.sequence, created, element, slides)

    def state(self, el: Element) -> dict:
        """An element as clients need it to redraw: frame, locks, revision and its text by paragraph (chips and
        slots marked)."""
        return {
            "id": str(el.id), "slide_id": str(el.slide_id), "revision": el.revision, "deleted": el.deleted_at is not None,
            "frame": {k: getattr(el, k) for k in FRAME}, "firm_lock": el.firm_lock,
            "user_lock": str(el.user_lock_by) if el.user_lock_by else None,
            "paragraphs": [[{"kind": s.kind, "text": s.text, "concept": s.concept_name} for s in segs]
                           for _, segs in self.paragraphs(el)],
        }

    # --- helpers --------------------------------------------------------------------------------
    def _is_element(self, op) -> bool:
        return op.get("type") in ("set_text", "set_cell", "set_chart_data", "set_frame", "set_style", "delete_element",
                                  "set_user_lock")

    def element(self, op) -> Element:
        el = self.s.get(Element, _uuid(op.get("target")))
        if el is None or el.deleted_at is not None or el.slide_id is None:
            raise Refuse("no such element in this memo")
        slide = self.s.get(Slide, el.slide_id)
        if slide is None or slide.memo_id != self.memo.id:
            raise Refuse("no such element in this memo")
        return el

    def slide(self, slide_id) -> Slide:
        slide = self.s.get(Slide, _uuid(slide_id))
        if slide is None or slide.memo_id != self.memo.id or slide.deleted_at is not None:
            raise Refuse("no such slide in this memo")
        return slide

    def fresh(self, el: Element, op: dict) -> None:
        """The edit must be made on the element's current revision; otherwise it is kept aside, not applied."""
        base = op.get("base_revision")
        if base is not None and base != el.revision:
            raise KeepAside(f"the element changed since this edit was made (edit on revision {base}, now {el.revision})")

    def bump(self, el: Element, actor_id) -> None:
        el.revision = (el.revision or 0) + 1
        el.updated_by = actor_id if isinstance(actor_id, uuid.UUID) else None
        el.updated_at = datetime.now(timezone.utc)

    def colour_ok(self, ref) -> None:
        if ref is None:
            return
        value = str(ref)
        if value.lower() in self.palette or value.upper() in self.palette.values():
            return
        raise Refuse(f"colour {value!r} is not in the firm's palette")

    def paragraphs(self, el: Element) -> list[tuple[ElementParagraph, list[Segment]]]:
        paras = self.s.scalars(select(ElementParagraph).where(ElementParagraph.element_id == el.id).order_by(ElementParagraph.position)).all()
        return [(p, self.s.scalars(select(Segment).where(Segment.paragraph_id == p.id).order_by(Segment.position)).all()) for p in paras]

    # --- element operations --------------------------------------------------------------------
    def _set_text(self, op, actor_type, actor_id):
        """Replace an element's text. Chips (record figures) must stay exactly as they are, in order: text
        around them may change, the figures may not. Marked slots become plain text when filled."""
        el = self.element(op)
        if el.firm_lock == "fixed":
            raise Refuse("this element is fixed by the firm")
        self.fresh(el, op)
        new_text = str((op.get("payload") or {}).get("text", ""))
        old = self.paragraphs(el)
        chips = [seg for _, segs in old for seg in segs if seg.kind == "chip"]
        new_paras = new_text.split("\n")
        # every chip must appear, unchanged and in order, in the new text
        placed: list[tuple[int, int, Segment]] = []          # (paragraph, offset, chip)
        p_index, offset = 0, 0
        for chip in chips:
            found = None
            while p_index < len(new_paras):
                at = new_paras[p_index].find(chip.text or "", offset)
                if chip.text and at >= 0:
                    found = (p_index, at)
                    break
                p_index, offset = p_index + 1, 0
            if found is None:
                raise Refuse(f"the figure {chip.text!r} comes from the deal record and cannot be typed over; "
                             f"change it in the record")
            placed.append((found[0], found[1], chip))
            offset = found[1] + len(chip.text)
        style = old[0][0] if old else None
        for _, segs in old:                              # segments first, then their paragraphs
            for seg in segs:
                self.s.delete(seg)
        self.s.flush()
        for para, _ in old:
            self.s.delete(para)
        self.s.flush()
        for i, text in enumerate(new_paras):
            src = old[min(i, len(old) - 1)][0] if old else style
            para = ElementParagraph(element_id=el.id, position=i, level=src.level if src else 0,
                                    **{k: getattr(src, k) for k in STYLE} if src else {})
            self.s.add(para)
            self.s.flush()
            pos, cursor = 0, 0
            for _, at, chip in [x for x in placed if x[0] == i]:
                if at > cursor:
                    self.s.add(Segment(paragraph_id=para.id, position=pos, kind="text", text=text[cursor:at]))
                    pos += 1
                self.s.add(Segment(paragraph_id=para.id, position=pos, kind="chip", text=chip.text,
                                   concept_name=chip.concept_name, record_path=chip.record_path,
                                   value_snapshot=chip.value_snapshot, format_item=chip.format_item))
                pos += 1
                cursor = at + len(chip.text)
            if cursor < len(text) or pos == 0:
                self.s.add(Segment(paragraph_id=para.id, position=pos, kind="text", text=text[cursor:]))
        self.bump(el, actor_id)

    def _set_cell(self, op, actor_type, actor_id):
        """Change one table cell's text. A cell that holds a record figure cannot be typed over; header and
        label cells belong to the firm's table."""
        el = self.element(op)
        self.fresh(el, op)
        payload = op.get("payload") or {}
        table = self.s.scalar(select(ElementTable).where(ElementTable.element_id == el.id))
        if table is None:
            raise Refuse("this element is not a table")
        cell = self.s.scalar(select(TableCell).where(TableCell.element_table_id == table.id,
                                                     TableCell.row == int(payload.get("row", -1)),
                                                     TableCell.col == int(payload.get("col", -1))))
        new_text = str(payload.get("text", ""))
        if cell is None:
            raise Refuse("no such cell in the firm's table")
        para = self.s.scalar(select(ElementParagraph).where(ElementParagraph.table_cell_id == cell.id))
        segs = self.s.scalars(select(Segment).where(Segment.paragraph_id == para.id).order_by(Segment.position)).all() if para else []
        old_text = "".join(x.text or "" for x in segs)
        if new_text.strip() == old_text.strip():
            return
        if any(x.kind == "chip" for x in segs):
            raise Refuse(f"the figure {old_text!r} comes from the deal record and cannot be typed over; change it in the record")
        if cell.kind == "fixed":
            raise Refuse("this cell is part of the firm's table")
        for x in segs:
            self.s.delete(x)
        self.s.flush()
        if para is None:
            para = ElementParagraph(table_cell_id=cell.id, position=0)
            self.s.add(para)
            self.s.flush()
        self.s.add(Segment(paragraph_id=para.id, position=0, kind="text", text=new_text))
        self.bump(el, actor_id)

    def _set_chart_data(self, op, actor_type, actor_id):
        """Chart values come from the deal record: a chart edited by hand is refused."""
        self.element(op)
        raise Refuse("chart values come from the deal record; change them in the record")

    def _keep_aside(self, op, actor_type, actor_id):
        """Something from PowerPoint the memo cannot take as it is (a new slide, a box moved to another
        slide): kept for a person to decide."""
        raise KeepAside((op.get("payload") or {}).get("reason") or "kept for a person to decide",
                        (op.get("payload") or {}).get("kind"))

    def _set_frame(self, op, actor_type, actor_id):
        el = self.element(op)
        if el.firm_lock in ("fixed", "content"):
            raise Refuse("the firm's template places this element; it cannot move")
        if el.user_lock_by is not None:
            raise Refuse("a person locked this element")
        self.fresh(el, op)
        payload = op.get("payload") or {}
        for k in FRAME:
            if k in payload:
                setattr(el, k, float(payload[k]))
        self.bump(el, actor_id)

    def _set_style(self, op, actor_type, actor_id):
        el = self.element(op)
        if el.firm_lock == "fixed":
            raise Refuse("this element is fixed by the firm")
        self.fresh(el, op)
        payload = op.get("payload") or {}
        for k in COLOUR_FIELDS:
            if k in payload:
                self.colour_ok(payload[k])
        for k in ("fill_ref", "line_ref", "line_width_pt"):
            if k in payload:
                setattr(el, k, payload[k])
        para_style = {k: payload[k] for k in STYLE if k in payload}
        if para_style:
            for para, _ in self.paragraphs(el):
                for k, v in para_style.items():
                    setattr(para, k, v)
        self.bump(el, actor_id)

    def _set_user_lock(self, op, actor_type, actor_id):
        el = self.element(op)
        locked = bool((op.get("payload") or {}).get("locked", True))
        el.user_lock_by = actor_id if locked and isinstance(actor_id, uuid.UUID) else None
        el.user_lock_at = datetime.now(timezone.utc) if locked else None
        if locked and el.user_lock_by is None:
            raise Refuse("a lock needs a person")
        self.bump(el, actor_id)

    def _delete_element(self, op, actor_type, actor_id):
        el = self.element(op)
        if el.firm_lock in ("fixed", "content"):
            # from PowerPoint a deleted template box is kept aside and the box stays; elsewhere it is refused
            if actor_type == "import":
                raise KeepAside("a box of the firm's template was deleted in PowerPoint; the box stays", "deleted_box")
            raise Refuse("the firm's template requires this element")
        if el.user_lock_by is not None:
            raise Refuse("a person locked this element")
        self.fresh(el, op)
        el.deleted_at = datetime.now(timezone.utc)
        self.bump(el, actor_id)

    def _add_element(self, op, actor_type, actor_id):
        """Add a box to a slide. Template slides take no new boxes: refused, or kept aside from PowerPoint."""
        payload = op.get("payload") or {}
        slide = self.slide(payload.get("slide_id") or op.get("target"))
        if slide.kind == "template":
            if actor_type == "import":
                raise KeepAside(f"a {payload.get('type', 'box')} was added on a template slide in PowerPoint",
                                "pasted_picture" if payload.get("type") == "image" else "added_box")
            raise Refuse("template slides take no new boxes; add a free slide")
        for k in COLOUR_FIELDS:
            if k in payload:
                self.colour_ok(payload[k])
        el = Element(id=uuid.uuid4(), slide_id=slide.id, type=payload.get("type", "text"), role=payload.get("role"),
                     fill_kind="person", firm_lock="free", revision=1, fill_ref=payload.get("fill_ref"),
                     line_ref=payload.get("line_ref"), **{k: float(payload.get(k, 0)) for k in FRAME})
        self.s.add(el)
        self.s.flush()
        if payload.get("text") is not None:
            for i, text in enumerate(str(payload["text"]).split("\n")):
                para = ElementParagraph(element_id=el.id, position=i, level=0)
                self.s.add(para)
                self.s.flush()
                self.s.add(Segment(paragraph_id=para.id, position=0, kind="text", text=text))
        return [el.id]

    # --- slide operations ----------------------------------------------------------------------
    def order(self) -> list[Slide]:
        return self.s.scalars(select(Slide).where(Slide.memo_id == self.memo.id, Slide.deleted_at.is_(None))
                              .order_by(Slide.position)).all()

    def check_order(self, slides: list[Slide]) -> None:
        """The firm's order rules on the memo's sections, after a move or an addition."""
        names = [self.sections[s.section_id].name if s.section_id in self.sections else None for s in slides]
        first = {}
        last = {}
        for i, n in enumerate(names):
            if n is not None:
                first.setdefault(n, i)
                last[n] = i
        for c in self.constraints:
            sec = self.sections.get(c.section_id)
            if sec is None or sec.name not in first:
                continue
            a, z = first[sec.name], last[sec.name]
            target = self.sections.get(c.target_section_id).name if c.target_section_id in self.sections else None
            ok = True
            if c.kind == "first":
                ok = a == 0
            elif c.kind == "last":
                ok = z == len(names) - 1
            elif c.kind == "position" and c.position:
                ok = a == c.position - 1
            elif c.kind in ("after", "immediately_after") and target in first:
                ok = a > last[target] and (c.kind == "after" or a == last[target] + 1)
            elif c.kind in ("before", "immediately_before") and target in first:
                ok = z < first[target] and (c.kind == "before" or z == first[target] - 1)
            elif c.kind == "after_all" and c.target_status:
                others = [last[s.name] for s in self.sections.values() if s.status == c.target_status and s.name in last and s.name != sec.name]
                ok = not others or a > max(others)
            if not ok:
                rule = f"{c.kind}" + (f" {target}" if target else f" {c.target_status}" if c.target_status else f" {c.position}" if c.position else "")
                raise Refuse(f"the firm's order puts {sec.name!r} {rule.replace('_', ' ')}")

    def renumber(self, slides: list[Slide]) -> None:
        for i, s in enumerate(slides):
            if s.position != i:
                s.position = i
                s.revision = (s.revision or 0) + 1

    def _move_slide(self, op, actor_type, actor_id):
        slide = self.slide(op.get("target"))
        sec = self.sections.get(slide.section_id)
        if sec is not None and sec.status == "locked":
            raise Refuse(f"{sec.name!r} is locked in place by the firm")
        to = int((op.get("payload") or {}).get("position", slide.position))
        slides = [s for s in self.order() if s.id != slide.id]
        slides.insert(max(0, min(to, len(slides))), slide)
        self.check_order(slides)
        self.renumber(slides)

    def _delete_slide(self, op, actor_type, actor_id):
        slide = self.slide(op.get("target"))
        sec = self.sections.get(slide.section_id)
        others = [s for s in self.order() if s.section_id == slide.section_id and s.id != slide.id]
        if sec is not None and sec.status in ("locked", "mandatory") and not others:
            raise Refuse(f"{sec.name!r} is required by the firm")
        slide.deleted_at = datetime.now(timezone.utc)
        self.renumber([s for s in self.order() if s.id != slide.id])

    def _add_slide(self, op, actor_type, actor_id):
        """A free slide on a layout of the firm (a template slide comes from a section and its layout)."""
        payload = op.get("payload") or {}
        layout = self.s.get(S.Layout, _uuid(payload.get("layout_id")))
        if layout is None or layout.spec_version_id != self.memo.spec_version_id:
            raise Refuse("the layout is not one of the firm's")
        slides = self.order()
        slide = Slide(id=uuid.uuid4(), memo_id=self.memo.id, position=len(slides), section_id=_uuid(payload.get("section_id")),
                      layout_id=layout.id, kind=payload.get("kind", "free"), background_ref=layout.background_ref, revision=1)
        self.s.add(slide)
        slides.insert(max(0, min(int(payload.get("position", len(slides))), len(slides))), slide)
        self.check_order(slides)
        self.s.flush()
        self.renumber(slides)
        return [slide.id]

    def _duplicate_slide(self, op, actor_type, actor_id):
        """A copy of a slide right after it, with new identities for the slide and every element."""
        src = self.slide(op.get("target"))
        sec = self.sections.get(src.section_id)
        if sec is not None and sec.count == "one":
            raise Refuse(f"the firm allows one {sec.name!r} slide")
        slides = self.order()
        new = Slide(id=uuid.uuid4(), memo_id=self.memo.id, position=src.position + 1, section_id=src.section_id,
                    layout_id=src.layout_id, kind=src.kind, background_ref=src.background_ref, revision=1)
        self.s.add(new)
        self.s.flush()
        for el in self.s.scalars(select(Element).where(Element.slide_id == src.id, Element.deleted_at.is_(None))):
            self.copy_element(el, new.id)
        slides.insert(src.position + 1, new)
        self.renumber(slides)
        return [new.id]

    def copy_element(self, el: Element, slide_id) -> Element:
        cols = {c.key for c in Element.__table__.columns} - {"id", "slide_id", "revision", "updated_by", "updated_at", "user_lock_by", "user_lock_at"}
        new = Element(id=uuid.uuid4(), slide_id=slide_id, revision=1, **{c: getattr(el, c) for c in cols})
        self.s.add(new)
        self.s.flush()
        for para, segs in self.paragraphs(el):
            self.copy_paragraph(para, segs, element_id=new.id)
        table = self.s.scalar(select(ElementTable).where(ElementTable.element_id == el.id))
        if table:
            t = ElementTable(id=uuid.uuid4(), element_id=new.id, table_template_id=table.table_template_id, table_style_id=table.table_style_id)
            self.s.add(t)
            self.s.flush()
            for model in (TableColumn, TableRow):
                for r in self.s.scalars(select(model).where(model.element_table_id == table.id)):
                    self.s.add(_clone(r, element_table_id=t.id))
            for cell in self.s.scalars(select(TableCell).where(TableCell.element_table_id == table.id)):
                c = _clone(cell, element_table_id=t.id)
                self.s.add(c)
                self.s.flush()
                for p in self.s.scalars(select(ElementParagraph).where(ElementParagraph.table_cell_id == cell.id)):
                    self.copy_paragraph(p, self.s.scalars(select(Segment).where(Segment.paragraph_id == p.id)).all(), table_cell_id=c.id)
        chart = self.s.scalar(select(ElementChart).where(ElementChart.element_id == el.id))
        if chart:
            ch = _clone(chart, element_id=new.id)
            self.s.add(ch)
            self.s.flush()
            for series in self.s.scalars(select(ChartSeries).where(ChartSeries.element_chart_id == chart.id)):
                cs = _clone(series, element_chart_id=ch.id)
                self.s.add(cs)
                self.s.flush()
                for p in self.s.scalars(select(ChartPoint).where(ChartPoint.chart_series_id == series.id)):
                    self.s.add(_clone(p, chart_series_id=cs.id))
        return new

    def copy_paragraph(self, para, segs, **owner):
        p = _clone(para, **({"element_id": None, "table_cell_id": None} | owner))
        self.s.add(p)
        self.s.flush()
        for seg in segs:
            self.s.add(_clone(seg, paragraph_id=p.id))


def _clone(row, **changes):
    cols = {c.key for c in type(row).__table__.columns} - {"id"}
    return type(row)(id=uuid.uuid4(), **({c: getattr(row, c) for c in cols} | changes))


def _uuid(value):
    if value is None or isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def _jsonable(value):
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    return value
