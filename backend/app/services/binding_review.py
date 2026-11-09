"""A person reviews and corrects the firm's binding (concept → where its value comes from in the deal record).

The binding is made once per firm and kind of record by the model, and checked by code: a path exists and has a
compatible kind. Nothing can prove it is the right datum, so a person confirms it. For each concept of the
firm's latest spec this shows where the value comes from, its value in the latest record, where the spec uses
it, and what code finds worth a look:
  missing     the record has no value for it
  broken      the path or formula gives nothing in this record
  diverged    places that held the same value in the last uploaded record no longer do: one was edited, the
              other was not (the memo may read the one nobody edited)
  mirror      another concept shows the same value from another place
  unit        the record's kind of value is not the concept's (money read as a count…)
and, for information only: the same value elsewhere in the record (often by chance), a place fixed to one
period while the record has several.
A person changes where a value comes from (a record value, a formula, or none) and confirms; every change is
kept (binding_changes), and memos built before it are offered the change (app/services/updates.py).
Nothing here knows any firm.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import spec as S
from app.db.memo import Element
from app.db.record import RecordRevision
from app.services.records import RELATIVE, Record

NUMERIC = ("money", "percent", "multiple", "count", "number", "months")


def latest(session: Session, firm: S.Firm):
    spec = session.scalar(select(S.Spec).where(S.Spec.firm_id == firm.id))
    version = session.scalar(select(S.SpecVersion).where(S.SpecVersion.spec_id == spec.id).order_by(S.SpecVersion.number.desc()))
    revision = session.scalar(select(RecordRevision).where(RecordRevision.firm_id == firm.id).order_by(RecordRevision.number.desc()))
    return version, revision


def last_upload(session: Session, revision: RecordRevision) -> RecordRevision:
    """The latest revision of the deal that came from a file (not edited by a person)."""
    return session.scalar(select(RecordRevision).where(RecordRevision.deal_id == revision.deal_id,
                                                       RecordRevision.firm_id == revision.firm_id,
                                                       RecordRevision.created_by.is_(None))
                          .order_by(RecordRevision.number.desc())) or revision


def trivial(value: float, kind: str | None) -> bool:
    """Values that repeat by chance (zero, small counts, years, fractions): not worth a duplicate warning."""
    if kind == "percent" or abs(value) < 10:
        return True
    return float(value).is_integer() and 1900 <= value <= 2100 and kind not in ("money",)


def resolve(record: Record, b) -> tuple[str, object, list]:
    """(status, value, record rows behind it) for a binding against a revision."""
    if b is None or b.kind == "missing":
        return "missing", None, []
    if b.kind == "derived":
        value = record.evaluate(b.formula or "")
        return ("derived", value, []) if value is not None else ("broken", None, [])
    row = record.resolve(b.path or "")
    if row is not None:
        return "found", row.number_value if row.number_value is not None else row.text_value or row.date_value, [row]
    rows = record.rows(b.path or "")
    if rows:                                                     # a whole list: table rows, a chart's series
        return "list", f"{len(rows)} values", rows
    return "broken", None, []


def review(session: Session, firm: S.Firm) -> dict:
    version, revision = latest(session, firm)
    record = Record(session, revision.id)
    upload = last_upload(session, revision)
    uploaded = record if upload.id == revision.id else Record(session, upload.id)
    q = session.scalars
    concepts = [c for c in q(select(S.Concept).where(S.Concept.spec_version_id == version.id)) if c.kind != "memo_data"]
    bindings = {b.concept_name: b for b in q(select(S.Binding).where(S.Binding.firm_id == firm.id,
                                                                     S.Binding.record_schema == revision.record_schema))}
    # where the spec uses each concept: layout and box
    layouts = {l.id: l.name for l in q(select(S.Layout).where(S.Layout.spec_version_id == version.id))}
    elements = {e.id: e for e in q(select(Element).where(Element.layout_id.in_(list(layouts))))}
    uses: dict = defaultdict(set)

    def where(element_id) -> str | None:
        e = elements.get(element_id)
        return f"{layouts.get(e.layout_id, '?')} · {e.name or e.role or e.type}" if e else None

    for r in q(select(S.RecipeSegment).where(S.RecipeSegment.spec_version_id == version.id, S.RecipeSegment.concept_id.isnot(None))):
        uses[r.concept_id].add(where(r.element_id))
    tables = {t.id: t for t in q(select(S.TableTemplate).where(S.TableTemplate.spec_version_id == version.id))}
    for row in q(select(S.TableTemplateRow).where(S.TableTemplateRow.table_template_id.in_(list(tables)),
                                                  S.TableTemplateRow.concept_id.isnot(None))):
        uses[row.concept_id].add(f"{where(tables[row.table_template_id].element_id)} · row {row.label or ''}".strip())
    charts = {c.id: c for c in q(select(S.ChartTemplate).where(S.ChartTemplate.spec_version_id == version.id))}
    for series in q(select(S.ChartTemplateSeries).where(S.ChartTemplateSeries.chart_template_id.in_(list(charts)),
                                                        S.ChartTemplateSeries.concept_id.isnot(None))):
        uses[series.concept_id].add(f"{where(charts[series.chart_template_id].element_id)} · chart series")
    users = {u.id: u.email for u in q(select(S.User).where(S.User.firm_id == firm.id))}

    out, by_value = [], defaultdict(list)
    for c in concepts:
        b = bindings.get(c.name)
        status, value, rows = resolve(record, b)
        row = rows[0] if status == "found" else None
        flags = []
        if status == "missing":
            flags.append({"kind": "missing", "text": "the record has no value for it: the memo shows a pending"})
        if status == "broken":
            flags.append({"kind": "broken", "text": "this place gives nothing in the latest record"})
        if row is not None and row.number_value is not None:
            was = uploaded.by_path.get(row.path)
            if was is not None and was.number_value is not None and not trivial(was.number_value, was.value_kind):
                # places equal in the uploaded record that are not equal any more: one was edited, the other not
                apart = []
                for r in uploaded.by_path.values():
                    now = record.by_path.get(r.path)
                    if r.path != row.path and r.number_value == was.number_value and now is not None \
                            and now.number_value != row.number_value:
                        apart.append({"path": r.path, "label": r.label, "value": now.number_value})
                if apart:
                    flags.append({"kind": "diverged", "text": f"held the same value as {len(apart)} other place(s) in the uploaded record; "
                                                              f"they differ now (one was edited, the other not)", "places": apart[:6]})
            if not trivial(row.number_value, row.value_kind):
                same = [r for r in record.by_path.values() if r.number_value == row.number_value and r.path != row.path
                        and (r.value_kind == row.value_kind or not r.value_kind or not row.value_kind)]
                tables = sorted({r.family.split("[")[0] if r.family else r.path for r in same})
                if same:
                    flags.append({"kind": "same_value", "info": True,
                                  "text": f"the same value is also in {len(tables)} other part(s) of the record (often by chance)",
                                  "places": [{"path": t, "label": None} for t in tables[:8]]})
                by_value[(row.number_value, row.value_kind)].append(c.name)
        if row is not None and c.value_kind in NUMERIC and row.value_kind and row.value_kind != c.value_kind \
                and {c.value_kind, row.value_kind} != {"count", "number"}:
            flags.append({"kind": "unit", "text": f"the concept is {c.value_kind}, the record's value is {row.value_kind}"})
        if row is not None and b is not None and b.path and not RELATIVE.match(b.path) and row.period \
                and len(record.families.get(row.family) or []) > 1:
            flags.append({"kind": "fixed", "info": True, "text": f"bound to {row.period} only; the record has {len(record.families[row.family])} periods "
                                                   f"(a relative place such as @last_actual moves on with the record)"})
        out.append({
            "concept": c.name, "description": c.description if hasattr(c, "description") else None,
            "concept_kind": c.kind, "value_kind": c.value_kind, "used_in": sorted(u for u in uses.get(c.id, ()) if u),
            "kind": b.kind if b else "missing", "path": b.path if b else None, "formula": b.formula if b else None,
            "status": status, "value": value if not isinstance(value, float) else round(value, 6),
            "label": row.label if row is not None else None, "unit": row.unit if row is not None else None,
            "period": row.period if row is not None else None, "flags": flags,
            "approved_by": users.get(b.approved_by) if b and b.approved_by else None,
            "approved_at": b.approved_at.isoformat() if b and b.approved_at else None,
        })
    for names in by_value.values():                              # two concepts, one value, two places
        if len(names) > 1:
            for item in out:
                if item["concept"] in names:
                    others = [n for n in names if n != item["concept"]]
                    item["flags"].append({"kind": "mirror", "text": f"shows the same value as {', '.join(others)}, "
                                                                    f"from {'another place' if len({x['path'] for x in out if x['concept'] in names}) > 1 else 'the same place'}"})
    for item in out:                                             # a mirror from the same place is fine
        item["flags"] = [f for f in item["flags"] if not (f["kind"] == "mirror" and f["text"].endswith("the same place"))]
    look = lambda x: [f for f in x["flags"] if not f.get("info")]
    # what most likely shows a wrong figure first; a plain missing value (a pending the memo shows) after
    order = {"diverged": 0, "broken": 1, "mirror": 2, "unit": 3, "missing": 4}
    out.sort(key=lambda x: (min((order.get(f["kind"], 5) for f in look(x)), default=6), x["approved_at"] is not None, x["concept"]))
    return {"spec_version": version.number, "revision": {"id": str(revision.id), "number": revision.number},
            "record_schema": revision.record_schema, "concepts": out,
            "counts": {k: sum(1 for x in out if x["status"] == k) for k in ("found", "list", "derived", "missing", "broken")},
            "to_look_at": sum(1 for x in out if look(x)), "approved": sum(1 for x in out if x["approved_at"]),
            "uploaded_revision": upload.number}


def change(session: Session, firm: S.Firm, user: S.User, concept: str, kind: str, path: str | None, formula: str | None) -> dict:
    """Where a concept's value comes from, set by a person: checked against the latest record, kept as a
    change, and confirmed by whoever made it."""
    version, revision = latest(session, firm)
    if session.scalar(select(S.Concept.id).where(S.Concept.spec_version_id == version.id, S.Concept.name == concept)) is None:
        raise ValueError(f"{concept!r} is not a concept of the firm's spec")
    if kind not in ("path", "derived", "missing"):
        raise ValueError("a binding is a record value (path), a formula (derived) or none (missing)")
    record = Record(session, revision.id)
    if kind == "path" and record.resolve(path or "") is None and not record.rows(path or ""):
        raise ValueError(f"{path!r} is not in the latest record")
    if kind == "derived" and record.evaluate(formula or "") is None:
        raise ValueError("the formula gives no value with the latest record")
    b = session.scalar(select(S.Binding).where(S.Binding.firm_id == firm.id, S.Binding.record_schema == revision.record_schema,
                                               S.Binding.concept_name == concept))
    session.add(S.BindingChange(firm_id=firm.id, record_schema=revision.record_schema, concept_name=concept,
                                before_kind=b.kind if b else None, before_path=b.path if b else None,
                                before_formula=b.formula if b else None, after_kind=kind,
                                after_path=path if kind == "path" else None, after_formula=formula if kind == "derived" else None,
                                changed_by=user.id))
    if b is None:
        b = S.Binding(firm_id=firm.id, record_schema=revision.record_schema, concept_name=concept)
        session.add(b)
    b.kind, b.path, b.formula = kind, path if kind == "path" else None, formula if kind == "derived" else None
    b.approved_by, b.approved_at = user.id, datetime.now(timezone.utc)
    session.commit()
    return {"concept": concept, "kind": kind, "path": b.path, "formula": b.formula}


def approve(session: Session, firm: S.Firm, user: S.User, concepts: list[str]) -> int:
    """A person confirms these bindings as they are."""
    _, revision = latest(session, firm)
    rows = session.scalars(select(S.Binding).where(S.Binding.firm_id == firm.id, S.Binding.record_schema == revision.record_schema,
                                                   S.Binding.concept_name.in_(concepts))).all()
    now = datetime.now(timezone.utc)
    for b in rows:
        b.approved_by, b.approved_at = user.id, now
    session.commit()
    return len(rows)
