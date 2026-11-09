"""What a memo still needs from a person: computed from the memo as it is, never stored, so a pending goes
away by itself when it stops being true. Only a person's "It's fine" is kept (pending_dismissals: who, when).

Pendings never block editing or exporting (docs/SCHEMA_DESIGN.md §3). Each one says where it is and why:
- no_data: a figure the record has no value for ("[no data: …]");
- to_write: text the firm expects the model or a person to write (‹model: …›, ‹person: …›);
- recipe_missing: a figure the firm's spec puts in a box that the box does not have (removed, or never there);
- picture: a picture people added, which may stand in for a native chart, table or slide;
- typed_figure: an amount typed as text in a box people edited, not a figure of the record;
- section_missing: a section the memo's stage requires and the memo has no slide for;
- chart_not_from_record: a chart that brought its own numbers (an uploaded slide's).
Those a person may accept ("It's fine") are in DISMISSABLE; the others go away when the memo has what is
missing. New kinds (text that does not fit, a spec version's changes) are added here. Nothing here knows any firm.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import spec as S
from app.db.history import PendingDismissal
from app.db.memo import Element, Slide
from app.services.figures import TYPED_AMOUNT
from app.services.memo_data import MemoData, fetch

DISMISSABLE = {"picture", "typed_figure", "recipe_missing", "chart_not_from_record", "old_layout"}


def pendings(session: Session, memo_id) -> list[dict]:
    d = MemoData(session, memo_id)
    memo = d.memo
    # the rest in one round trip: what was accepted, what the spec puts in each box, what the stage requires
    origins = select(Element.origin_element_id).where(Element.slide_id.in_(select(Slide.id).where(Slide.memo_id == memo.id)))
    r = fetch(session, {
        "dismissed": (PendingDismissal, select(PendingDismissal).where(PendingDismissal.memo_id == memo.id)),
        "concepts": (S.Concept, select(S.Concept).where(S.Concept.spec_version_id == memo.spec_version_id)),
        "recipe": (S.RecipeSegment, select(S.RecipeSegment).where(S.RecipeSegment.element_id.in_(origins),
                                                                  S.RecipeSegment.kind.in_(("figure", "fact")))),
        "required": (S.StageSection, select(S.StageSection).where(S.StageSection.stage_id == memo.stage_id, S.StageSection.required)),
        "sections": (S.Section, select(S.Section).where(S.Section.spec_version_id == memo.spec_version_id)),
        "slide_layouts": (S.Layout, select(S.Layout).where(S.Layout.id.in_(select(Slide.layout_id).where(Slide.memo_id == memo.id)))),
    })
    dismissed = {(x.kind, x.target_id) for x in r["dismissed"]}
    position = {s.id: s.position + 1 for s in d.slides}
    out: list[dict] = []

    def add(kind, el, message, target=None):
        target = target or (el.id if el is not None else None)
        if (kind, target) in dismissed:
            return
        out.append({"kind": kind, "slide": position.get(el.slide_id) if el is not None else None,
                    "slide_id": str(el.slide_id) if el is not None else None, "element_id": str(el.id) if el is not None else None,
                    "target_id": str(target) if target else None, "where": (el.name or el.role or el.type).replace("_", " ") if el is not None else None,
                    "message": message, "dismissable": kind in DISMISSABLE})

    # what the firm's spec puts in each box (by the layout box it came from)
    expected: dict = {}
    names = {c.id: c.name for c in r["concepts"]}
    for seg in r["recipe"]:
        if seg.concept_id in names:
            expected.setdefault(seg.element_id, set()).add(names[seg.concept_id])

    for el in d.elements:
        segs = [x for _, ss in d.paragraphs(element_id=el.id) for x in ss]
        table = d.table_of.get(el.id)
        if table is not None:
            segs += [x for c in d.cells_of.get(table.id, []) for _, ss in d.paragraphs(cell_id=c.id) for x in ss]
        for x in segs:
            if x.kind == "chip" and (x.text or "").startswith("[no data"):
                add("no_data", el, f"{(x.concept_name or x.record_path or 'a figure').replace('_', ' ')} has no value in the deal record", x.id)
            elif x.kind in ("model_draft", "person_slot"):
                add("to_write", el, (x.text or "").strip("‹›"), x.id)
        have = {x.concept_name for x in segs if x.kind == "chip" and x.concept_name}
        for name in sorted(expected.get(el.origin_element_id, set()) - have):
            add("recipe_missing", el, f"the firm's spec puts {name.replace('_', ' ')} in this box", uuid.uuid5(el.id, name))
        chart = d.chart_of.get(el.id)
        if chart is not None and chart.chart_template_id is None and any(
                not (x.record_path or x.concept_name) for x in d.series_of.get(chart.id, [])):
            add("chart_not_from_record", el, "a chart whose numbers are not the record's: rebuild it from the record, or confirm it")
        image = d.image_of.get(el.id)
        if image is not None and image.flagged:
            add("picture", el, "a picture: if it shows figures, build it as a chart or table from the record; else confirm it")
        if el.updated_by is not None or el.firm_lock == "free":
            typed = [m.group(0) for x in segs if x.kind == "text" for m in TYPED_AMOUNT.finditer(x.text or "")]
            if typed:
                add("typed_figure", el, f"typed, not from the record: {', '.join(typed[:3])}")

    stale = {l.id: l for l in r["slide_layouts"] if l.spec_version_id != memo.spec_version_id}
    for s in d.slides:                                   # a layout the memo's spec version no longer has
        if s.layout_id in stale and ("old_layout", s.id) not in dismissed:
            out.append({"kind": "old_layout", "slide": s.position + 1, "slide_id": str(s.id), "element_id": None, "target_id": str(s.id),
                        "where": stale[s.layout_id].name, "message": "its layout is not in the firm's current template; it stays as it "
                        "was: move its content to a slide of the template, or keep it", "dismissable": True})
    have_sections = {s.section_id for s in d.slides}
    sections = {x.id: x for x in r["sections"]}
    for link in r["required"]:
        if link.section_id not in have_sections and link.section_id in sections and (("section_missing", link.section_id) not in dismissed):
            out.append({"kind": "section_missing", "slide": None, "slide_id": None, "element_id": None, "target_id": str(link.section_id),
                        "where": sections[link.section_id].name, "message": f"the stage requires a {sections[link.section_id].name} slide",
                        "dismissable": False})
    return sorted(out, key=lambda p: (p["slide"] or 0, p["kind"]))


def dismiss(session: Session, memo_id, kind: str, target_id, user_id) -> None:
    """A person's "It's fine" on a pending that may be accepted."""
    if kind not in DISMISSABLE:
        raise ValueError(f"a {kind.replace('_', ' ')} pending goes away when the memo has what is missing")
    session.add(PendingDismissal(memo_id=uuid.UUID(str(memo_id)), kind=kind, target_id=uuid.UUID(str(target_id)), dismissed_by=user_id))
    session.commit()
