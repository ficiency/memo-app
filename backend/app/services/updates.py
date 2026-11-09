"""A memo and a newer deal record: what would change, and applying it.

Every memo is pinned to the record revision it was built from. When the deal gets a newer revision, or a
person changes where some of the firm's figures come from (the binding), the memo shows it; the person sees
the changes (before → after) and applies or ignores them. Each decision is kept (memo_updates). What would change is
found by building the memo again in memory from the same spec version and stage with the new revision (the
generator's own formatting, never a second copy of it) and comparing it, box by box, with the memo as it is
now: the figures in text, in table cells and in charts.

Applying changes figures only: people's words stay, nothing is deleted. A figure the new revision has no
value for becomes "[no data: …]", a pending the memo shows. A table or chart whose shape changes (more or
fewer rows or points) is flagged for a person. Nothing here knows any firm.
"""
from __future__ import annotations

import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import spec as S
from app.db.history import MemoUpdate
from app.db.memo import (ChartPoint, ChartSeries, Element, ElementChart, ElementParagraph, ElementTable, Memo,
                         Segment, TableCell)
from app.db.record import RecordRevision
from app.services.generator.memo import Generator
from app.services.memo_data import MemoData


def revision_json(session: Session, r: RecordRevision) -> dict:
    creator = session.get(S.User, r.created_by) if r.created_by else None
    return {"id": str(r.id), "number": r.number, "as_of": r.as_of.isoformat() if r.as_of else None,
            "created_at": r.created_at.isoformat(), "created_by": creator.email if creator else None}


def available(session: Session, memo: Memo) -> dict:
    """The memo's revision; the deal's latest one for this firm if newer and not ignored; and the binding changes
    made since the memo was built or last decided on an update."""
    current = session.get(RecordRevision, memo.record_revision_id)
    latest = session.scalar(select(RecordRevision).where(RecordRevision.deal_id == current.deal_id,
                                                         RecordRevision.firm_id == current.firm_id)
                            .order_by(RecordRevision.number.desc()))
    decisions = session.scalars(select(MemoUpdate).where(MemoUpdate.memo_id == memo.id)).all()
    newer = latest if latest is not None and latest.number > current.number else None
    if newer is not None and any(d.kind == "record" and d.to_id == newer.id and d.status == "ignored" for d in decisions):
        newer = None
    since = max([memo.created_at] + [d.created_at for d in decisions])
    changed = session.scalars(select(S.BindingChange.concept_name).where(
        S.BindingChange.firm_id == memo.firm_id, S.BindingChange.record_schema == current.record_schema,
        S.BindingChange.created_at > since)).all()
    return {"current": revision_json(session, current), "available": revision_json(session, newer) if newer else None,
            "binding_changes": sorted(set(changed))}


def _by(rows, key) -> dict:
    out = defaultdict(list)
    for r in rows:
        out[getattr(r, key)].append(r)
    return out


def changes(session: Session, memo_id, revision_id) -> dict:
    """What applying a revision would change in the memo: one entry per figure, and warnings where a person is
    needed. Each change carries what applying writes."""
    memo = session.get(Memo, uuid.UUID(str(memo_id)))
    revision = session.get(RecordRevision, uuid.UUID(str(revision_id)))
    current = session.get(RecordRevision, memo.record_revision_id)
    if revision is None or revision.deal_id != current.deal_id or revision.firm_id != memo.firm_id:
        raise ValueError("that record revision is not this deal's")
    firm = session.get(S.Firm, memo.firm_id)
    stage = session.get(S.Stage, memo.stage_id)
    built = Generator(session, firm.name, stage.name, memo.memo_date, memo.deal_team, spec_version_id=memo.spec_version_id,
                      revision_id=revision.id).run(save=False)["rows"]
    kinds = _by(built, "__class__")
    g_elements = {e.origin_element_id: e for e in kinds[Element]}
    g_paras = _by(kinds[ElementParagraph], "element_id")
    g_cell_paras = _by(kinds[ElementParagraph], "table_cell_id")
    g_segs = _by(kinds[Segment], "paragraph_id")
    g_table = {t.element_id: t for t in kinds[ElementTable]}
    g_cells = _by(kinds[TableCell], "element_table_id")
    g_chart = {c.element_id: c for c in kinds[ElementChart]}
    g_series = _by(kinds[ChartSeries], "element_chart_id")
    g_points = _by(kinds[ChartPoint], "chart_series_id")

    def g_chips(paras) -> list:
        return [s for p in sorted(paras, key=lambda p: p.position) for s in sorted(g_segs[p.id], key=lambda s: s.position)
                if s.kind == "chip"]

    d = MemoData(session, memo.id)
    position = {s.id: s.position + 1 for s in d.slides}
    out, warnings = [], []

    def label(el: Element) -> str:
        return el.name or (el.role or el.type).replace("_", " ")

    def compare(el, where, mine: list, theirs: list) -> None:
        for a, b in zip(mine, theirs):
            if (a.text, a.record_path) != (b.text, b.record_path):
                out.append({"slide": position[el.slide_id], "element_id": str(el.id), "element": label(el), "where": where,
                            "kind": "figure", "segment_id": str(a.id), "concept": a.concept_name, "before": a.text,
                            "after": b.text, "missing": b.text.startswith("[no data"),
                            "write": {"text": b.text, "record_path": b.record_path, "value_snapshot": b.value_snapshot}})
        if len(mine) != len(theirs):
            warnings.append({"slide": position[el.slide_id], "element": label(el),
                             "message": f"{where}: {len(mine)} figures in the memo, {len(theirs)} from the new revision"})

    for el in d.elements:
        g = g_elements.get(el.origin_element_id)
        if g is None:
            continue
        mine = [s for _, segs in d.paragraphs(element_id=el.id) for s in segs if s.kind == "chip"]
        compare(el, "text", mine, g_chips(g_paras.get(g.id, [])))
        table, g_t = d.table_of.get(el.id), g_table.get(g.id)
        if table is not None and g_t is not None:
            cells = {(c.row, c.col): c for c in d.cells_of.get(table.id, [])}
            g_c = {(c.row, c.col): c for c in g_cells.get(g_t.id, [])}
            if max((k[0] for k in cells), default=-1) != max((k[0] for k in g_c), default=-1) or \
                    max((k[1] for k in cells), default=-1) != max((k[1] for k in g_c), default=-1):
                warnings.append({"slide": position[el.slide_id], "element": label(el),
                                 "message": "the table's rows or columns change with the new revision; the cells both have are updated"})
            for key, c in cells.items():
                if key in g_c:
                    mine = [s for _, segs in d.paragraphs(cell_id=c.id) for s in segs if s.kind == "chip"]
                    compare(el, f"row {key[0] + 1}, column {key[1] + 1}", mine, g_chips(g_cell_paras.get(g_c[key].id, [])))
        chart, g_ch = d.chart_of.get(el.id), g_chart.get(g.id)
        if chart is not None and g_ch is not None:
            mine_series = sorted(d.series_of.get(chart.id, []), key=lambda x: x.position)
            theirs_series = sorted(g_series.get(g_ch.id, []), key=lambda x: x.position)
            for a, b in zip(mine_series, theirs_series):
                before = [(p.category, p.value) for p in sorted(d.points_of.get(a.id, []), key=lambda p: p.position)]
                after = [(p.category, p.value) for p in sorted(g_points.get(b.id, []), key=lambda p: p.position)]
                if before != after:
                    show = lambda pts: ", ".join(f"{c} {v:,.4g}" if v is not None else f"{c} –" for c, v in pts)
                    out.append({"slide": position[el.slide_id], "element_id": str(el.id), "element": label(el),
                                "where": f"chart series {a.name or a.concept_name or a.position + 1}", "kind": "chart",
                                "series_id": str(a.id), "concept": a.concept_name, "before": show(before), "after": show(after),
                                "missing": not after,
                                "write": {"points": [{"category": p.category, "value": p.value, "is_forecast": p.is_forecast}
                                                     for p in sorted(g_points.get(b.id, []), key=lambda p: p.position)]}})
    out.sort(key=lambda c: (c["slide"], c["element"]))
    warnings += [{"slide": c["slide"], "element": c["element"], "message": f"{c['concept'] or 'a figure'} has no value in the new revision"}
                 for c in out if c["missing"]]
    return {"revision": revision_json(session, revision), "current": revision_json(session, current),
            "changes": out, "warnings": sorted(warnings, key=lambda w: w["slide"])}


def apply(session: Session, memo: Memo, revision_id, actor_id) -> int:
    """Write a revision's changes into the memo (inside the door's transaction): figures and chart points only,
    each changed element's revision moves on (an edit made before is then kept aside, not lost), and the memo is
    pinned to the revision. Returns how many figures changed."""
    plan = changes(session, memo.id, revision_id)
    touched = set()
    for c in plan["changes"]:
        if c["kind"] == "figure":
            seg = session.get(Segment, uuid.UUID(c["segment_id"]))
            for k, v in c["write"].items():
                setattr(seg, k, v)
        else:
            for p in session.scalars(select(ChartPoint).where(ChartPoint.chart_series_id == uuid.UUID(c["series_id"]))):
                session.delete(p)
            session.flush()
            for i, p in enumerate(c["write"]["points"]):
                session.add(ChartPoint(chart_series_id=uuid.UUID(c["series_id"]), position=i, **p))
        touched.add(uuid.UUID(c["element_id"]))
    for el in session.scalars(select(Element).where(Element.id.in_(touched))):
        el.revision = (el.revision or 0) + 1
        el.updated_by = actor_id if isinstance(actor_id, uuid.UUID) else None
    memo.record_revision_id = uuid.UUID(str(revision_id))
    return len(plan["changes"])
