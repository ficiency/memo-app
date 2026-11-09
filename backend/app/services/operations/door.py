"""The one door every edit goes through: canvas, agent, PowerPoint import and updates.

An operation is checked on the server before it lands. It is applied, refused (it breaks a rule of the firm
or changes a record figure) or kept aside (it was made on an older version of the element, so applying it
would overwrite someone else's change). Every operation is logged with its actor, reason and the memo's next
sequence number; nothing is lost. Nothing here knows any firm: locks, palette and section order come from the
memo's spec.

Operation: {"type": ..., "target": <element or slide id>, "base_revision": <the revision the editor saw>,
            "payload": {...}}
Types: set_text, set_cell, set_frame, set_style, set_user_lock, delete_element, set_chart_data, keep_aside
       (one element each), add_element, add_slide, duplicate_slide, move_slide, delete_slide (the memo's
       structure), apply_update, ignore_update (a newer deal record: its figures written in, or not).

How an edit of one element lands, in few round trips (the database is remote, each one is slow):
  1. read the memo, the element and its text in one statement, outside any transaction;
  2. check everything in Python (the spec's rules are kept in memory per version: a version never changes);
  3. write in one short transaction: the element's revision moves on only if it is still the one read
     (compare and set) and the memo's sequence moves on, in one statement; then the element's new rows, the
     operation's log and the commit, sent together. If the revision moved on in between, the edit is kept
     aside, as any edit made on an older version is. Nothing is locked while reading and checking.
Operations on the memo's structure (slides, a new box) change several rows; they lock the memo's row for
their transaction, so the structure changes one at a time.
"""
from __future__ import annotations

import copy
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from psycopg.types.json import Jsonb
from sqlalchemy import JSON, delete, select
from sqlalchemy.orm import Session

from app.db import spec as S
from app.db.history import Conflict, MemoUpdate, Operation
from app.db.memo import (ChartPoint, ChartSeries, Element, ElementChart, ElementParagraph, ElementTable, Memo,
                         Segment, Slide, TableCell, TableColumn, TableRow)
from app.db.record import RecordRevision
from app.services import updates
from app.services.figures import Figures
from app.services.memo_data import MemoData, fetch
from app.services.zones import snap

FRAME = ("x_in", "y_in", "w_in", "h_in", "rotation_deg")
STYLE = ("font", "size_pt", "bold", "italic", "caps", "color_ref", "align", "anchor")
COLOUR_FIELDS = ("color_ref", "fill_ref", "line_ref", "bullet_color_ref")
SLOTS = ("model_draft", "person_slot", "conditional")
STRUCTURE = ("edit_box", "add_element", "add_slide", "duplicate_slide", "move_slide", "delete_slide", "apply_update", "ignore_update")
MARKER = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")   # {{concept}}: a figure asked for by name
ELEMENT_OPS = ("set_text", "set_cell", "set_chart_data", "set_frame", "set_style", "delete_element", "set_user_lock")


@dataclass
class Result:
    status: str                    # applied, refused, kept_aside
    reason: str | None
    operation_id: uuid.UUID
    sequence: int
    created: list | None = None    # ids made by the operation (new element, new slide…)
    element: dict | None = None    # the element as it is now, for everyone to update their view
    slides: list | None = None     # the memo's slide order now, after a slide operation
    warnings: list | None = None   # applied, with something worth saying


@dataclass
class Check:
    """What an operation would do: its outcome, and for one element how it reads before and after."""
    status: str
    reason: str | None
    warnings: list
    before: dict | None
    after: dict | None
    slides: list | None


class Refuse(Exception):
    pass


class KeepAside(Exception):
    def __init__(self, message: str, kind: str | None = None):
        super().__init__(message)
        self.kind = kind                     # what was kept aside (deleted_box, added_box…); None = a stale edit


# --- the spec's rules, read once per version ----------------------------------------------------------------
@dataclass
class Rules:
    palette: dict           # lower-case name → upper-case hex
    sections: dict          # id → Section
    constraints: list       # OrderConstraint
    zones: dict             # layout id → (x, y, w, h) of its free zone, inches
    grid: object            # the firm's grid (margins, snap step), or None


_RULES: dict[uuid.UUID, Rules] = {}


def rules(session: Session, version_id) -> Rules:
    """A spec version never changes once memos are built on it, so its rules are read once per process."""
    if version_id not in _RULES:
        r = fetch(session, {
            "palette": (S.PaletteColor, select(S.PaletteColor).where(S.PaletteColor.spec_version_id == version_id)),
            "sections": (S.Section, select(S.Section).where(S.Section.spec_version_id == version_id)),
            "constraints": (S.OrderConstraint, select(S.OrderConstraint).where(S.OrderConstraint.spec_version_id == version_id)),
            "zones": (S.LayoutZone, select(S.LayoutZone).where(S.LayoutZone.kind == "free", S.LayoutZone.layout_id.in_(
                select(S.Layout.id).where(S.Layout.spec_version_id == version_id)))),
            "grid": (S.Grid, select(S.Grid).where(S.Grid.spec_version_id == version_id)),
        })
        _RULES[version_id] = Rules({p.name.lower(): p.hex.upper() for p in r["palette"]},
                                   {x.id: x for x in r["sections"]}, r["constraints"],
                                   {z.layout_id: (z.x_in, z.y_in, z.w_in, z.h_in) for z in r["zones"]},
                                   r["grid"][0] if r["grid"] else None)
    return _RULES[version_id]


# --- what an element edit writes ---------------------------------------------------------------------------
@dataclass
class Plan:
    """An element edit, worked out in memory: the element's new values (written with its revision, only if the
    revision is still the one read), the statements after that, the rows to add, and how the element reads
    afterwards (for everyone's screen)."""
    element: Element
    sets: dict = field(default_factory=dict)
    statements: list = field(default_factory=list)     # (sql, params)
    rows: list = field(default_factory=list)           # new model objects to insert
    after: object = None                               # callable(revision) → updates the read copy
    noop: bool = False                                 # nothing changes (the same text): logged as applied
    warnings: list = field(default_factory=list)       # applied, but worth saying (a figure the recipe expects removed)


class Door:
    def __init__(self, session: Session, memo_id):
        self.s = session
        self.memo_id = uuid.UUID(str(memo_id))
        self.memo: Memo | None = None

    # --- entry point ----------------------------------------------------------------------------
    def apply(self, op: dict, actor_type: str = "person", actor_id=None, on_behalf_of=None, change_set_id=None,
              held_by: str | None = None) -> Result:
        """held_by: who is editing the target element right now (real time), when it is not the actor."""
        kind = op.get("type")
        if kind in STRUCTURE:
            return self._apply_structure(op, actor_type, actor_id, on_behalf_of, change_set_id)
        return self._apply_element(op, actor_type, actor_id, on_behalf_of, change_set_id, held_by)

    def check(self, op: dict, actor_type: str = "person", actor_id=None, held_by: str | None = None) -> Check:
        """What the operation would do, without doing it: its outcome and, for one element, the element before and
        after. A proposal is checked this way when it is made, and applied through apply() when a person confirms
        it (the memo may have changed in between, so it is checked again then)."""
        kind = op.get("type")
        if kind in STRUCTURE:
            return self._check_structure(op, actor_type, actor_id)
        read, target, plan, status, reason, _ = self._decide(op, actor_type, actor_id, held_by)
        before = after = None
        if plan is not None:
            before = read.element_json(plan.element)
            if not plan.noop:
                plan.after((plan.element.revision or 0) + 1)
            after = read.element_json(plan.element)
        self.s.commit()
        return Check(status, reason, plan.warnings if plan is not None else [], before, after, None)

    def check_after(self, earlier: list[dict], op: dict, actor_type: str = "person", actor_id=None) -> Check:
        """A change checked as the memo would be after the earlier structure changes of the same proposal (a box
        on a slide the proposal adds first); everything is undone. Without earlier ones it is check()."""
        if not earlier or op.get("type") not in STRUCTURE:
            return self.check(op, actor_type, actor_id)
        self.memo = self.s.get(Memo, self.memo_id)
        self.rules = rules(self.s, self.memo.spec_version_id)
        outer = self.s.begin_nested()
        try:
            for e in earlier:
                inner = self.s.begin_nested()
                try:
                    getattr(self, f"_{e.get('type')}")(e, actor_type, actor_id)
                    self.s.flush()
                    inner.commit()
                except (Refuse, KeepAside):
                    inner.rollback()                         # refused itself: the later ones see the memo without it
            return self._check_structure(op, actor_type, actor_id)
        finally:
            if outer.is_active:
                outer.rollback()
            self.s.rollback()

    def _check_structure(self, op, actor_type, actor_id) -> Check:
        """A change to the memo's structure, tried inside a savepoint and undone."""
        self.memo = self.s.get(Memo, self.memo_id)
        if self.memo is None:
            raise ValueError(f"no memo {self.memo_id}")
        self.rules = rules(self.s, self.memo.spec_version_id)
        status, reason, slides = "applied", None, None
        nested = self.s.begin_nested()
        try:
            getattr(self, f"_{op.get('type')}")(op, actor_type, actor_id)
            self.s.flush()
            slides = [{"id": str(x.id), "position": x.position} for x in self.order()]
        except Refuse as e:
            status, reason = "refused", str(e)
        except KeepAside as e:
            status, reason = "kept_aside", str(e)
        finally:
            nested.rollback()
            self.s.rollback()
        return Check(status, reason, [], None, None, slides)

    # --- one element: read, check, compare and set ------------------------------------------------
    def _decide(self, op, actor_type, actor_id, held_by):
        """Read the element and work the edit out in memory: (read copy, target, plan, status, reason, aside kind)."""
        kind = op.get("type")
        target = _uuid(op.get("target"))
        if not self.s.in_transaction():                  # reads need no transaction: no BEGIN, no COMMIT
            self.s.connection(execution_options={"isolation_level": "AUTOCOMMIT"})
        try:
            read = MemoData(self.s, self.memo_id, element_ids=[target] if target and kind in ELEMENT_OPS else [])
        except IndexError:
            raise ValueError(f"no memo {self.memo_id}")
        self.memo = read.memo
        self.rules = rules(self.s, self.memo.spec_version_id)
        plan, status, reason, aside_kind = None, "applied", None, None
        try:
            if held_by and kind in ELEMENT_OPS:
                raise Refuse(f"{held_by} is editing this box")
            if kind == "keep_aside":
                payload = op.get("payload") or {}
                raise KeepAside(payload.get("reason") or "kept for a person to decide", payload.get("kind"))
            if kind not in ELEMENT_OPS:
                raise Refuse(f"unknown operation {kind!r}")
            el = self.element_in(read, target)
            if kind == "set_chart_data":
                raise Refuse("chart values come from the deal record; change them in the record")
            plan = getattr(self, f"_plan_{kind}")(read, el, op, actor_type, actor_id)
        except Refuse as e:
            status, reason, plan = "refused", str(e), None
        except KeepAside as e:
            status, reason, aside_kind, plan = "kept_aside", str(e), e.kind, None
        return read, target, plan, status, reason, aside_kind

    def _apply_element(self, op, actor_type, actor_id, on_behalf_of, change_set_id, held_by) -> Result:
        kind = op.get("type")
        read, target, plan, status, reason, aside_kind = self._decide(op, actor_type, actor_id, held_by)
        payload = _jsonable(op.get("payload") or {})
        if plan is not None and plan.warnings:
            payload = {**payload, "warnings": plan.warnings}      # kept with the edit, for the memo's pendings
        log = Operation(id=uuid.uuid4(), memo_id=self.memo.id, change_set_id=change_set_id, actor_type=actor_type,
                        actor_id=actor_id, on_behalf_of=on_behalf_of, op_type=kind or "?", target_id=target,
                        base_revision=op.get("base_revision"), payload=payload)
        pg = self.s.connection().connection.driver_connection
        element = None
        with pg.pipeline(), pg.transaction():
            cur = pg.cursor()
            if plan is not None and not plan.noop:
                cur.execute(*cas(plan, self.memo.id, actor_id))
                revision, sequence = cur.fetchone()          # the compare and set's answer: one round trip
                if revision is None:
                    status, reason = "kept_aside", "the element changed while this edit was being saved"
                else:
                    for sql, params in plan.statements:
                        cur.execute(sql, params)
                    insert(cur, plan.rows)
                    plan.after(revision)
                    element = read.element_json(plan.element)
            else:
                cur.execute("UPDATE memos SET sequence = sequence + 1 WHERE id = %(memo)s RETURNING sequence",
                            {"memo": self.memo.id})
                sequence = cur.fetchone()[0]
                if plan is not None:                             # applied, nothing to change
                    element = read.element_json(plan.element)
            log.sequence, log.status, log.reason = sequence, status, reason
            rows = [log]
            if status == "kept_aside":
                rows.append(Conflict(id=uuid.uuid4(), memo_id=self.memo.id, operation_id=log.id,
                                     element_id=target if kind in ELEMENT_OPS else None, status="open", detail=reason,
                                     kind=aside_kind or ("changed_since_export" if actor_type == "import" else "changed_since_seen")))
            insert(cur, rows)                                    # sent with the commit: the second round trip
        self.s.commit()                                          # ends the session's read-only use of the connection
        return Result(status, reason, log.id, sequence, [], element if status == "applied" else None, None,
                      plan.warnings if plan is not None and status == "applied" else None)

    def free_room(self, slide) -> tuple | None:
        """The free zone of a slide where people may place their own boxes: a slide of a section the firm leaves
        free (or a free slide), on a layout with a zone. None elsewhere."""
        sec = self.rules.sections.get(slide.section_id)
        if slide.kind != "free" and (sec is None or sec.status != "free"):
            return None
        return self.rules.zones.get(slide.layout_id)

    def inside(self, frame: dict, zone: tuple) -> None:
        x, y, w, h = zone
        f = frame
        if f["x_in"] < x - 0.05 or f["y_in"] < y - 0.05 or f["x_in"] + f["w_in"] > x + w + 0.05 or f["y_in"] + f["h_in"] > y + h + 0.05:
            raise Refuse(f"outside the free area the firm leaves on this slide (from {x:.1f}, {y:.1f} in, "
                         f"{w:.1f} × {h:.1f} in): the header, footer and margins stay as they are")
        if f["w_in"] < 0.3 or f["h_in"] < 0.2:
            raise Refuse("too small to hold anything")

    def snapped(self, frame: dict) -> dict:
        return {k: snap(v, self.rules.grid) for k, v in frame.items()}

    def element_in(self, read: MemoData, target) -> Element:
        el = next((e for e in read.elements if e.id == target), None)
        slides = {x.id for x in read.slides}
        if el is None or el.deleted_at is not None or el.slide_id not in slides:
            raise Refuse("no such element in this memo")
        return el

    def fresh(self, el: Element, op: dict) -> None:
        """The edit must be made on the element's current revision; otherwise it is kept aside, not applied."""
        base = op.get("base_revision")
        if base is not None and base != el.revision:
            raise KeepAside(f"the element changed since this edit was made (edit on revision {base}, now {el.revision})")

    def colour_ok(self, ref) -> None:
        if ref is None:
            return
        value = str(ref)
        if value.lower() in self.rules.palette or value.upper() in self.rules.palette.values():
            return
        raise Refuse(f"colour {value!r} is not in the firm's palette")

    # --- element edits, worked out in memory ------------------------------------------------------
    def _plan_set_text(self, read: MemoData, el: Element, op, actor_type, actor_id) -> Plan:
        """Replace an element's text. Figures (chips) are never typed: the ones there stay exactly as they are, in
        order, unless the edit removes them by name (payload remove_chips: their ids); a new one is asked for by
        its concept, {{concept}}, and written from the record as the firm writes it. A figure the firm's recipe
        puts in this box can be removed: the edit is applied with a warning (a pending for the memo)."""
        if el.firm_lock == "fixed":
            raise Refuse("this element is fixed by the firm")
        self.fresh(el, op)
        payload = op.get("payload") or {}
        new_text = str(payload.get("text", ""))
        old = read.paragraphs(element_id=el.id)
        chips = [seg for _, segs in old for seg in segs if seg.kind == "chip"]
        remove = {_uuid(x) for x in payload.get("remove_chips") or []}
        if remove - {c.id for c in chips}:
            raise Refuse("the figures to remove are not in this box")
        # new figures, asked for by concept: written from the record, never typed
        figures, paras = None, []
        for raw in new_text.split("\n"):
            text, spans, pos = "", [], 0
            for m in MARKER.finditer(raw):
                text += raw[pos:m.start()]
                if figures is None:
                    figures = Figures(self.s, self.s.get(S.Firm, self.memo.firm_id), read.version,
                                      self.s.get(RecordRevision, self.memo.record_revision_id))
                try:
                    chip = figures.chip(m.group(1))
                except ValueError as e:
                    raise Refuse(str(e))
                spans.append((len(text), len(text) + len(chip["text"]), chip))
                text += chip["text"]
                pos = m.end()
            paras.append((text + raw[pos:], spans))
        # the figures that stay must all be there, unchanged and in order (outside the new ones)
        placed: list[tuple[int, int, Segment]] = []          # (paragraph, offset, chip)
        p_index, offset = 0, 0
        for chip in [c for c in chips if c.id not in remove]:
            found = None
            while p_index < len(paras):
                text, spans = paras[p_index]
                at = text.find(chip.text or "", offset)
                while at >= 0 and any(a0 < at + len(chip.text) and at < a1 for a0, a1, _ in spans):
                    at = text.find(chip.text, at + 1)
                if chip.text and at >= 0:
                    found = (p_index, at)
                    break
                p_index, offset = p_index + 1, 0
            if found is None:
                raise Refuse(f"the figure {chip.text!r} comes from the deal record and cannot be typed over; "
                             f"change it in the record, or remove it from the box")
            placed.append((found[0], found[1], chip))
            offset = found[1] + len(chip.text)
        paragraphs, segments = [], []
        for i, (text, spans) in enumerate(paras):
            src = old[min(i, len(old) - 1)][0] if old else None
            para = ElementParagraph(id=uuid.uuid4(), element_id=el.id, table_cell_id=None, position=i,
                                    level=src.level if src else 0, role=src.role if src else None,
                                    **{k: getattr(src, k) if src else None for k in PARAGRAPH_COPY})
            paragraphs.append(para)
            items = sorted([(at, len(c.text), {"kind": "chip", "text": c.text, "concept_name": c.concept_name,
                                               "record_path": c.record_path, "value_snapshot": c.value_snapshot})
                            for p_i, at, c in placed if p_i == i] + [(a0, a1 - a0, chip) for a0, a1, chip in spans],
                           key=lambda x: x[0])
            pos, cursor = 0, 0
            for at, length, fields in items:
                if at > cursor:
                    segments.append(_segment(para.id, pos, "text", text[cursor:at]))
                    pos += 1
                segments.append(Segment(id=uuid.uuid4(), paragraph_id=para.id, position=pos, format_item=None,
                                        **{k: fields.get(k) for k in ("kind", "text", "concept_name", "record_path", "value_snapshot")}))
                pos += 1
                cursor = at + length
            if cursor < len(text) or pos == 0:
                segments.append(_segment(para.id, pos, "text", text[cursor:]))
        # a figure taken out and put back in the same edit is still there: only what is gone is worth saying
        stays = {c.concept_name for c in chips if c.id not in remove} | {chip["concept_name"] for _, spans in paras for _, _, chip in spans}
        warnings = self.recipe_warnings(el, [c for c in chips if c.id in remove and c.concept_name not in stays])

        def after(revision):
            el.revision = revision
            read.paras_of_element[el.id] = paragraphs
            for p in paragraphs:
                read.segments_of[p.id] = [x for x in segments if x.paragraph_id == p.id]

        return Plan(el, statements=[
            ("DELETE FROM segments WHERE paragraph_id IN (SELECT id FROM element_paragraphs WHERE element_id = %(el)s)", {"el": el.id}),
            ("DELETE FROM element_paragraphs WHERE element_id = %(el)s", {"el": el.id}),
        ], rows=paragraphs + segments, after=after, warnings=warnings)

    def recipe_warnings(self, el: Element, removed: list) -> list[str]:
        """Removed figures the firm's recipe puts in this box: allowed, and said (they become a pending)."""
        if not removed or el.origin_element_id is None:
            return []
        expected = set(self.s.scalars(select(S.Concept.name).join(S.RecipeSegment, S.RecipeSegment.concept_id == S.Concept.id)
                                      .where(S.RecipeSegment.element_id == el.origin_element_id,
                                             S.RecipeSegment.kind.in_(("figure", "fact")))))
        out = []
        for c in removed:
            if c.concept_name not in expected:
                continue
            name = c.concept_name.replace("_", " ")
            if (c.text or "").startswith("[no data"):      # the box never had it: still to be written
                out.append(f"The firm's spec puts {name} in this box: still pending until the box has it.")
            else:                                          # a figure the box had is gone
                out.append(f"The firm's spec puts {name} in this box: removed, it stays pending until it is back or accepted.")
        return out

    def _plan_set_cell(self, read: MemoData, el: Element, op, actor_type, actor_id) -> Plan:
        """Change one table cell's text. A cell that holds a record figure cannot be typed over; header and
        label cells belong to the firm's table."""
        self.fresh(el, op)
        payload = op.get("payload") or {}
        table = read.table_of.get(el.id)
        if table is None:
            raise Refuse("this element is not a table")
        row, col = int(payload.get("row", -1)), int(payload.get("col", -1))
        cell = next((c for c in read.cells_of.get(table.id, []) if c.row == row and c.col == col), None)
        if cell is None:
            raise Refuse("no such cell in the firm's table")
        new_text = str(payload.get("text", ""))
        paras = read.paragraphs(cell_id=cell.id)
        para, segs = paras[0] if paras else (None, [])
        old_text = "".join(x.text or "" for x in segs)
        if new_text.strip() == old_text.strip():
            return Plan(el, noop=True)
        if any(x.kind == "chip" for x in segs):
            raise Refuse(f"the figure {old_text!r} comes from the deal record and cannot be typed over; change it in the record")
        if cell.kind == "data" and table.table_template_id is None:        # a person's table: its data are the record's
            raise Refuse("this cell shows a figure of the deal record, which has none for it yet; it is filled in the record")
        if cell.kind == "fixed" and table.table_template_id is not None:   # a person's own table: all its words
            raise Refuse("this cell is part of the firm's table")
        rows, statements = [], []
        if para is None:
            para = ElementParagraph(id=uuid.uuid4(), element_id=None, table_cell_id=cell.id, position=0, level=0,
                                    role=None, **{k: None for k in PARAGRAPH_COPY})
            rows.append(para)
        else:
            statements.append(("DELETE FROM segments WHERE paragraph_id = %(p)s", {"p": para.id}))
        seg = _segment(para.id, 0, "text", new_text)
        rows.append(seg)

        def after(revision):
            el.revision = revision
            read.paras_of_cell[cell.id] = [para]
            read.segments_of[para.id] = [seg]

        return Plan(el, statements=statements, rows=rows, after=after)

    def _plan_set_frame(self, read: MemoData, el: Element, op, actor_type, actor_id) -> Plan:
        """Move or resize a box. Only on a free slide, inside its free zone, snapped to the firm's grid; a box the
        template placed there (its content area) may be resized too; fixed boxes (header, footer, logo) never."""
        slide = next(x for x in read.slides if x.id == el.slide_id)
        zone = self.free_room(slide)
        if el.firm_lock == "fixed" or (el.firm_lock == "content" and zone is None):
            raise Refuse("the firm's template places this element; it cannot move")
        if el.user_lock_by is not None:
            raise Refuse("a person locked this element")
        if zone is None:
            raise Refuse("boxes move only on the slides the firm leaves free")
        self.fresh(el, op)
        payload = op.get("payload") or {}
        frame = {k: float(payload[k]) if k in payload else float(getattr(el, k) or 0) for k in ("x_in", "y_in", "w_in", "h_in")}
        frame = self.snapped(frame)
        self.inside(frame, zone)
        sets = {k: v for k, v in frame.items() if abs(v - float(getattr(el, k) or 0)) > 1e-6}
        if "rotation_deg" in payload:
            sets["rotation_deg"] = float(payload["rotation_deg"])
        if not sets:
            return Plan(el, noop=True)
        return Plan(el, sets=sets, after=_set_on(el, sets))

    def _plan_set_style(self, read: MemoData, el: Element, op, actor_type, actor_id) -> Plan:
        if el.firm_lock == "fixed":
            raise Refuse("this element is fixed by the firm")
        self.fresh(el, op)
        payload = op.get("payload") or {}
        for k in COLOUR_FIELDS:
            if k in payload:
                self.colour_ok(payload[k])
        sets = {k: payload[k] for k in ("fill_ref", "line_ref", "line_width_pt") if k in payload}
        para_style = {k: payload[k] for k in STYLE if k in payload}
        statements = []
        if para_style:
            statements.append((f"UPDATE element_paragraphs SET {', '.join(f'{k} = %({k})s' for k in para_style)} "
                               f"WHERE element_id = %(el)s", {**para_style, "el": el.id}))
        set_element = _set_on(el, sets)

        def after(revision):
            set_element(revision)
            for p in read.paras_of_element.get(el.id, []):
                for k, v in para_style.items():
                    setattr(p, k, v)

        return Plan(el, sets=sets, statements=statements, after=after)

    def _plan_set_user_lock(self, read: MemoData, el: Element, op, actor_type, actor_id) -> Plan:
        locked = bool((op.get("payload") or {}).get("locked", True))
        if locked and not isinstance(actor_id, uuid.UUID):
            raise Refuse("a lock needs a person")
        sets = {"user_lock_by": actor_id if locked else None, "user_lock_at": datetime.now(timezone.utc) if locked else None}
        return Plan(el, sets=sets, after=_set_on(el, sets))

    def _plan_delete_element(self, read: MemoData, el: Element, op, actor_type, actor_id) -> Plan:
        slide = next(x for x in read.slides if x.id == el.slide_id)
        free_slide = self.free_room(slide) is not None
        if el.firm_lock == "fixed" or (el.firm_lock == "content" and not free_slide):
            # from PowerPoint a deleted template box is kept aside and the box stays; elsewhere it is refused
            if actor_type == "import":
                raise KeepAside("a box of the firm's template was deleted in PowerPoint; the box stays", "deleted_box")
            raise Refuse("the firm's template requires this element")
        if el.user_lock_by is not None:
            raise Refuse("a person locked this element")
        self.fresh(el, op)
        sets = {"deleted_at": datetime.now(timezone.utc)}
        return Plan(el, sets=sets, after=_set_on(el, sets))

    # --- the memo's structure: one transaction holding the memo's row ------------------------------
    def _apply_structure(self, op, actor_type, actor_id, on_behalf_of, change_set_id) -> Result:
        # Structure changes move several slides: the memo's row is locked until this one commits, so two of
        # them cannot interleave (and the sequence stays in commit order).
        self.memo = self.s.get(Memo, self.memo_id, with_for_update=True, populate_existing=True)
        if self.memo is None:
            raise ValueError(f"no memo {self.memo_id}")
        self.rules = rules(self.s, self.memo.spec_version_id)
        kind = op.get("type")
        created: list = []
        status, reason, aside_kind = "applied", None, None
        try:
            created = getattr(self, f"_{kind}")(op, actor_type, actor_id) or []
        except Refuse as e:
            status, reason = "refused", str(e)
        except KeepAside as e:
            status, reason, aside_kind = "kept_aside", str(e), e.kind
        if status != "applied":
            self.s.rollback()                                    # nothing half-done stays
            self.memo = self.s.get(Memo, self.memo_id, with_for_update=True, populate_existing=True)
        self.memo.sequence = (self.memo.sequence or 0) + 1
        if status == "applied":
            self.memo.status = "editing"                         # any change after a final review reopens the memo
        row = Operation(id=uuid.uuid4(), memo_id=self.memo.id, sequence=self.memo.sequence, change_set_id=change_set_id,
                        actor_type=actor_type, actor_id=actor_id, on_behalf_of=on_behalf_of, op_type=kind or "?",
                        target_id=_uuid(op.get("target")), base_revision=op.get("base_revision"),
                        payload=_jsonable(op.get("payload") or {}), status=status, reason=reason)
        self.s.add(row)
        self.s.flush()
        if status == "kept_aside":
            self.s.add(Conflict(memo_id=self.memo.id, operation_id=row.id, element_id=None,
                                kind=aside_kind or ("changed_since_export" if actor_type == "import" else "changed_since_seen"),
                                detail=reason, status="open"))
        self.s.commit()
        element = slides = None
        if status == "applied" and kind in ("add_element", "edit_box") and created:
            element = MemoData(self.s, self.memo.id, element_ids=created).element_json(self.s.get(Element, created[0]))
        elif status == "applied":
            slides = [{"id": str(x.id), "position": x.position, "section_id": str(x.section_id) if x.section_id else None,
                       "revision": x.revision} for x in self.order()]
        return Result(status, reason, row.id, row.sequence, created, element, slides)

    def slide(self, slide_id) -> Slide:
        slide = self.s.get(Slide, _uuid(slide_id))
        if slide is None or slide.memo_id != self.memo.id or slide.deleted_at is not None:
            raise Refuse("no such slide in this memo")
        return slide

    def paragraphs(self, el: Element) -> list[tuple[ElementParagraph, list[Segment]]]:
        paras = self.s.scalars(select(ElementParagraph).where(ElementParagraph.element_id == el.id).order_by(ElementParagraph.position)).all()
        return [(p, self.s.scalars(select(Segment).where(Segment.paragraph_id == p.id).order_by(Segment.position)).all()) for p in paras]

    def _add_element(self, op, actor_type, actor_id):
        """Add a box to a slide the firm leaves free, inside its free zone and snapped to the grid: text, a table of
        the record, a chart of the record. Template slides take no new boxes: refused, or kept aside from
        PowerPoint."""
        payload = op.get("payload") or {}
        slide = self.slide(payload.get("slide_id") or op.get("target"))
        zone = self.free_room(slide)
        if zone is None:
            if actor_type == "import":
                raise KeepAside(f"a {payload.get('type', 'box')} was added on a template slide in PowerPoint",
                                "pasted_picture" if payload.get("type") == "image" else "added_box")
            raise Refuse("this slide takes no new boxes: the firm lays it out; add a slide of a section it leaves free")
        for k in COLOUR_FIELDS:
            if k in payload:
                self.colour_ok(payload[k])
        kind = payload.get("type", "text")
        if kind not in ("text", "table", "chart", "image"):
            raise Refuse(f"a new box is text, a table, a chart or a picture, not {kind!r}")
        x, y, w, h = zone
        frame = {"x_in": float(payload.get("x_in", x)), "y_in": float(payload.get("y_in", y)),
                 "w_in": float(payload.get("w_in", min(w, 6.0))), "h_in": float(payload.get("h_in", min(h, 3.0)))}
        if actor_type != "import":                           # from PowerPoint the position is the file's
            frame = self.snapped(frame)
            self.inside(frame, zone)
        el = Element(id=uuid.uuid4(), slide_id=slide.id, type=kind, role=payload.get("role"),
                     fill_kind="person", firm_lock="free", revision=1, fill_ref=payload.get("fill_ref"),
                     line_ref=payload.get("line_ref"), rotation_deg=0, z_index=100, **frame)
        self.s.add(el)
        self.s.flush()
        if kind in ("table", "chart"):
            from app.services import boxes
            figures = Figures(self.s, self.s.get(S.Firm, self.memo.firm_id), self.s.get(S.SpecVersion, self.memo.spec_version_id),
                              self.s.get(RecordRevision, self.memo.record_revision_id))
            try:
                if kind == "table":
                    style = self.s.scalar(select(S.TableStyle.id).where(S.TableStyle.spec_version_id == self.memo.spec_version_id))
                    rows = boxes.build_table(figures, el, str(payload.get("table") or ""), payload.get("rows"), payload.get("columns"), style)
                else:
                    rows = boxes.build_chart(figures, el, str(payload.get("chart_type") or "column"), list(payload.get("series") or []),
                                             int(payload.get("periods") or 6), payload.get("names"))
            except ValueError as e:
                raise Refuse(str(e))
            for cls in (ElementTable, TableColumn, TableRow, TableCell, ElementParagraph, Segment, ElementChart, ChartSeries, ChartPoint):
                self.s.add_all([r for r in rows if type(r) is cls])
                self.s.flush()
            return [el.id]
        if payload.get("text") is not None:
            figures = None
            for i, line in enumerate(str(payload["text"]).split("\n")):
                para = ElementParagraph(element_id=el.id, position=i, level=0)
                self.s.add(para)
                self.s.flush()
                parts, pos = [], 0
                for m in MARKER.finditer(line):                  # {{concept}}: a figure from the record
                    if m.start() > pos:
                        parts.append({"kind": "text", "text": line[pos:m.start()]})
                    figures = figures or Figures(self.s, self.s.get(S.Firm, self.memo.firm_id),
                                                 self.s.get(S.SpecVersion, self.memo.spec_version_id),
                                                 self.s.get(RecordRevision, self.memo.record_revision_id))
                    try:
                        parts.append(figures.chip(m.group(1)))
                    except ValueError as e:
                        raise Refuse(str(e))
                    pos = m.end()
                if pos < len(line) or not parts:
                    parts.append({"kind": "text", "text": line[pos:]})
                for n, part in enumerate(parts):
                    self.s.add(Segment(paragraph_id=para.id, position=n, **part))
        return [el.id]

    def _edit_box(self, op, actor_type, actor_id):
        """A person's own table or chart built again from the deal record with other rows, columns, figures,
        periods or kind; what the person wrote on it (headers, row labels, series names) is kept, and the names
        given here win. The firm's tables and charts follow the firm's template: refused."""
        from app.services import boxes
        el = self.s.get(Element, _uuid(op.get("target")))
        slide = self.s.get(Slide, el.slide_id) if el is not None else None
        if el is None or el.deleted_at is not None or slide is None or slide.memo_id != self.memo.id:
            raise Refuse("no such box in this memo")
        d = MemoData(self.s, self.memo.id, element_ids=[el.id])
        table, chart = d.table_of.get(el.id), d.chart_of.get(el.id)
        if (table is None and chart is None) or (table is not None and table.table_template_id is not None) \
                or (chart is not None and chart.chart_template_id is not None):
            raise Refuse("only a table or chart people added can be changed this way; the firm's follow its template")
        if el.user_lock_by is not None:
            raise Refuse("a person locked this element")
        if op.get("base_revision") is not None and int(op["base_revision"]) != el.revision:
            raise Refuse("the box changed since you opened it; open it again")
        payload = op.get("payload") or {}
        kept = boxes.written(d, el)
        figures = Figures(self.s, self.s.get(S.Firm, self.memo.firm_id), self.s.get(S.SpecVersion, self.memo.spec_version_id),
                          self.s.get(RecordRevision, self.memo.record_revision_id))
        try:
            if table is not None:
                rows = boxes.build_table(figures, el, str(payload.get("table") or ""), payload.get("rows"), payload.get("columns"),
                                         table.table_style_id, kept.get("headers"), kept.get("labels"))
            else:
                names = {**kept.get("names", {}), **(payload.get("names") or {})}
                rows = boxes.build_chart(figures, el, str(payload.get("chart_type") or chart.chart_type), list(payload.get("series") or []),
                                         int(payload.get("periods") or 6), names)
        except ValueError as e:
            raise Refuse(str(e))
        if table is not None:                                  # the old table goes, cells and their words with it
            cells = select(TableCell.id).where(TableCell.element_table_id == table.id)
            paras = select(ElementParagraph.id).where(ElementParagraph.table_cell_id.in_(cells))
            self.s.execute(delete(Segment).where(Segment.paragraph_id.in_(paras)))
            self.s.execute(delete(ElementParagraph).where(ElementParagraph.table_cell_id.in_(cells)))
            for model in (TableCell, TableRow, TableColumn):
                self.s.execute(delete(model).where(model.element_table_id == table.id))
            self.s.execute(delete(ElementTable).where(ElementTable.id == table.id))
        else:
            series = select(ChartSeries.id).where(ChartSeries.element_chart_id == chart.id)
            self.s.execute(delete(ChartPoint).where(ChartPoint.chart_series_id.in_(series)))
            self.s.execute(delete(ChartSeries).where(ChartSeries.element_chart_id == chart.id))
            self.s.execute(delete(ElementChart).where(ElementChart.id == chart.id))
        for cls in (ElementTable, TableColumn, TableRow, TableCell, ElementParagraph, Segment, ElementChart, ChartSeries, ChartPoint):
            self.s.add_all([r for r in rows if type(r) is cls])
            self.s.flush()
        live = self.s.get(Element, el.id)
        live.revision = (live.revision or 0) + 1
        live.updated_by = actor_id if isinstance(actor_id, uuid.UUID) else None
        return [el.id]

    def update_revision(self, op):
        """The revision an update brings: a newer one of the deal (record), or the memo's own (the binding
        changed: the same record, figures from other places)."""
        rev = self.s.get(RecordRevision, _uuid((op.get("payload") or {}).get("revision_id")))
        current = self.s.get(RecordRevision, self.memo.record_revision_id)
        if rev is None or rev.deal_id != current.deal_id or rev.firm_id != self.memo.firm_id:
            raise Refuse("that record revision is not this deal's")
        return rev, current

    def decided(self, kind_status: str, rev, current, actor_id) -> None:
        self.s.add(MemoUpdate(memo_id=self.memo.id, kind="record" if rev.id != current.id else "binding",
                              from_id=current.id, to_id=rev.id, status=kind_status,
                              decided_by=actor_id if isinstance(actor_id, uuid.UUID) else None))

    def _apply_update(self, op, actor_type, actor_id):
        """A newer deal record's figures, or figures from where the binding now says, written into the memo:
        people's words stay, nothing is deleted."""
        rev, current = self.update_revision(op)
        self.update_count = updates.apply(self.s, self.memo, rev.id, actor_id)
        self.decided("applied", rev, current, actor_id)

    def _ignore_update(self, op, actor_type, actor_id):
        """The memo stays as it is; this update is not offered again."""
        rev, current = self.update_revision(op)
        self.decided("ignored", rev, current, actor_id)

    def order(self) -> list[Slide]:
        return self.s.scalars(select(Slide).where(Slide.memo_id == self.memo.id, Slide.deleted_at.is_(None))
                              .order_by(Slide.position)).all()

    def check_order(self, slides: list[Slide]) -> None:
        """The firm's order rules on the memo's sections, after a move or an addition."""
        sections = self.rules.sections
        names = [sections[s.section_id].name if s.section_id in sections else None for s in slides]
        first = {}
        last = {}
        for i, n in enumerate(names):
            if n is not None:
                first.setdefault(n, i)
                last[n] = i
        for c in self.rules.constraints:
            sec = sections.get(c.section_id)
            if sec is None or sec.name not in first:
                continue
            a, z = first[sec.name], last[sec.name]
            target = sections.get(c.target_section_id).name if c.target_section_id in sections else None
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
                others = [last[s.name] for s in sections.values() if s.status == c.target_status and s.name in last and s.name != sec.name]
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
        sec = self.rules.sections.get(slide.section_id)
        if sec is not None and sec.status == "locked":
            raise Refuse(f"{sec.name!r} is locked in place by the firm")
        to = int((op.get("payload") or {}).get("position", slide.position))
        slides = [s for s in self.order() if s.id != slide.id]
        slides.insert(max(0, min(to, len(slides))), slide)
        self.check_order(slides)
        self.renumber(slides)

    def _delete_slide(self, op, actor_type, actor_id):
        slide = self.slide(op.get("target"))
        sec = self.rules.sections.get(slide.section_id)
        others = [s for s in self.order() if s.section_id == slide.section_id and s.id != slide.id]
        if sec is not None and sec.status in ("locked", "mandatory") and not others:
            raise Refuse(f"{sec.name!r} is required by the firm")
        slide.deleted_at = datetime.now(timezone.utc)
        self.renumber([s for s in self.order() if s.id != slide.id])

    def _add_slide(self, op, actor_type, actor_id):
        """A slide of a section on one of the firm's layouts for it, with the layout's boxes filled as a new memo's
        would be (fixed words, figures from the record, marked slots to write). A blank layout gives a free
        slide. The section's count and the firm's order rules are checked."""
        payload = op.get("payload") or {}
        layout = self.s.get(S.Layout, _uuid(payload.get("layout_id")))
        if layout is None or layout.spec_version_id != self.memo.spec_version_id or layout.is_master:
            raise Refuse("the layout is not one of the firm's")
        allowed = set(self.s.scalars(select(S.SectionLayout.section_id).where(S.SectionLayout.layout_id == layout.id)))
        section_id = _uuid(payload.get("section_id")) or (next(iter(allowed)) if len(allowed) == 1 else None)
        sec = self.rules.sections.get(section_id)
        if sec is None:
            raise Refuse("say which section the slide is for")
        if allowed and section_id not in allowed:
            raise Refuse(f"the firm does not use {layout.name!r} for {sec.name!r}")
        slides = self.order()
        if sec.count == "one" and any(s.section_id == sec.id for s in slides):
            raise Refuse(f"the firm allows one {sec.name!r} slide")
        if sec.count == "n" and sec.count_n and sum(1 for s in slides if s.section_id == sec.id) >= sec.count_n:
            raise Refuse(f"the firm allows {sec.count_n} {sec.name!r} slides")
        slide = Slide(id=_uuid(payload.get("id")) or uuid.uuid4(), memo_id=self.memo.id, position=len(slides), section_id=sec.id,
                      layout_id=layout.id,
                      kind="free" if layout.is_blank else "template", background_ref=layout.background_ref, revision=1)
        self.s.add(slide)
        slides.insert(max(0, min(int(payload.get("position", len(slides))), len(slides))), slide)
        self.check_order(slides)
        self.s.flush()
        self.renumber(slides)
        self.fill_from_layout(slide, layout)
        return [slide.id]

    def fill_from_layout(self, slide: Slide, layout) -> None:
        """The layout's boxes on the slide, built by the generator (its recipes, formats and binding) against the
        memo's spec version, record revision and stage, so an added slide reads like the memo's own."""
        from app.services.generator.memo import Generator
        stage = self.s.get(S.Stage, self.memo.stage_id)
        gen = Generator(self.s, self.s.get(S.Firm, self.memo.firm_id).name, stage.name, self.memo.memo_date, self.memo.deal_team,
                        spec_version_id=self.memo.spec_version_id, revision_id=self.memo.record_revision_id)
        gen.memo, gen.memo_data["page"] = self.memo, str(slide.position + 1)
        for el in sorted(gen.elements_of[layout.id], key=lambda e: (e.z_index or 0, e.y_in or 0, e.x_in or 0)):
            gen.add_element(slide, el)
        for cls in (Element, ElementTable, TableColumn, TableRow, TableCell, ElementParagraph, Segment,
                    ElementChart, ChartSeries, ChartPoint):           # the generator's own log is not kept: this is one operation
            self.s.add_all([r for r in gen.rows if type(r) is cls])
            self.s.flush()

    def _duplicate_slide(self, op, actor_type, actor_id):
        """A copy of a slide right after it, with new identities for the slide and every element."""
        src = self.slide(op.get("target"))
        sec = self.rules.sections.get(src.section_id)
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


# --- writing --------------------------------------------------------------------------------------------
PARAGRAPH_COPY = tuple(c.key for c in ElementParagraph.__table__.columns
                       if c.key not in ("id", "element_id", "table_cell_id", "position", "level", "role"))


def cas(plan: Plan, memo_id, actor_id) -> tuple[str, dict]:
    """One statement: the element's new values and revision, only if its revision is still the one read, and
    the memo's next sequence. Returns (the new revision or null, the sequence)."""
    sets = "".join(f", {k} = %(set_{k})s" for k in plan.sets)
    sql = (f"WITH cas AS (UPDATE elements SET revision = revision + 1, updated_by = %(actor)s, updated_at = now(){sets} "
           f"WHERE id = %(el)s AND revision = %(rev)s AND deleted_at IS NULL RETURNING revision), "
           f"seq AS (UPDATE memos SET sequence = sequence + 1, "
           f"status = CASE WHEN EXISTS (SELECT 1 FROM cas) THEN 'editing' ELSE status END "
           f"WHERE id = %(memo)s RETURNING sequence) "
           f"SELECT (SELECT revision FROM cas), (SELECT sequence FROM seq)")
    params = {"actor": actor_id if isinstance(actor_id, uuid.UUID) else None, "el": plan.element.id,
              "rev": plan.element.revision, "memo": memo_id, **{f"set_{k}": v for k, v in plan.sets.items()}}
    return sql, params


def insert(cur, rows: list) -> None:
    """Insert model objects, one statement per table (several rows each), with no round trip of their own
    in a pipeline. Column defaults are filled here; server defaults (creation times) are left to the database."""
    by_table: dict = {}
    for r in rows:
        by_table.setdefault(type(r), []).append(r)
    for model, items in by_table.items():
        columns = [c for c in model.__table__.columns
                   if not (c.server_default is not None and all(getattr(r, c.key) is None for r in items))]

        def value(r, c):
            v = getattr(r, c.key)
            if v is None and c.default is not None and not callable(c.default.arg):
                v = c.default.arg
            return Jsonb(v) if isinstance(c.type, JSON) and v is not None else v

        sql = f"INSERT INTO {model.__tablename__} ({', '.join(c.name for c in columns)}) VALUES ({', '.join(['%s'] * len(columns))})"
        cur.executemany(sql, [[value(r, c) for c in columns] for r in items])


def _set_on(el: Element, sets: dict):
    def after(revision):
        el.revision = revision
        for k, v in sets.items():
            setattr(el, k, v)
    return after


def _segment(paragraph_id, position, kind, text) -> Segment:
    return Segment(id=uuid.uuid4(), paragraph_id=paragraph_id, position=position, kind=kind, text=text,
                   concept_name=None, record_path=None, value_snapshot=None, format_item=None)


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
    return copy.copy(value)
