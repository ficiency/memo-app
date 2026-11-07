"""Bind a firm's concepts to paths of a deal record: once per firm and record format, at its first memo.

Code builds an index of the record from its normalised values (any format with a reader), the model maps
each concept of the firm's latest spec to a path, a relative path, a formula or "missing", and code checks
every answer resolves to a real value of a compatible kind. Memo data (stage, date, team) is set when a
memo is created and is not bound.

Usage (from backend/):
    python -m app.services.binding <firm>
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import spec as S
from app.db.record import RecordRevision
from app.db.session import SessionLocal
from app.services.onboarding.llm import call_with_checks
from app.services.records import RELATIVE, Record

COMPATIBLE = {                                                  # concept value kind → record value kinds that fit
    "money": {"money", "number"}, "percent": {"percent", "number"}, "multiple": {"multiple", "number"},
    "count": {"count", "number"}, "ratio": {"multiple", "percent", "number"}, "date": {"date", "text"},
    "period": {"text", "date", "months", "number"},
}

INSTRUCTIONS = """You connect the concepts a firm uses in its investment memos to the data in a deal record.

You get the firm's concepts (name, type, value kind, where the firm uses it and examples from past memos of
other deals) and an index of the record: every path, its kind, unit, label and periods. Values change and
periods move from deal to deal, so for a figure that belongs to a period use a relative path:
  <family>@last_actual, <family>@last_actual-1 (and -2, -3…), <family>@first_forecast (+1, +2…), <family>@latest
where the family is shown in the index (field:arr, table:pl_summary.revenue, series:arr_monthly). Use an exact
path only for a value that is not part of a period series.

Families with rows: one row is <prefix>[<row key>].<column> (the index shows an example). Only a concept marked
"one value per period, case or row" may be a whole family (angle_table:x.returns.irr for IRR by case); any
other concept is one value: pick its row or item, e.g. narrative:series_c_terms[Anti-dilution].text.

For every concept return one of:
  found:   "path": an exact or relative path from the index
  derive:  "formula": arithmetic (+ - * / and numbers) over paths, when the record holds the ingredients
  missing: nothing in the record fits; say what would be needed
Use only paths of the index; never invent one. Prefer the firm's own angle (marked in the index) when a
concept has one there. Table rows of a concept are the table's column for that concept.

Return JSON only:
{"bindings": [{"concept": "...", "status": "found|derive|missing", "path": "...", "formula": "...",
               "confidence": "high|low", "why": "<one line>"}]}"""


def concepts(session: Session, version_id) -> list[dict]:
    rows = session.scalars(select(S.Concept).where(S.Concept.spec_version_id == version_id,
                                                   S.Concept.kind != "memo_data").order_by(S.Concept.name)).all()
    # concepts that are a list: a table row (one value per period or per case) or a chart series
    listed = set(session.scalars(select(S.TableTemplateRow.concept_id).join(S.TableTemplate).where(
        S.TableTemplate.spec_version_id == version_id))) | set(session.scalars(select(S.ChartTemplateSeries.concept_id)
        .join(S.ChartTemplate).where(S.ChartTemplate.spec_version_id == version_id)))
    return [{"name": c.name, "type": c.kind, "value_kind": c.value_kind, "list": c.id in listed,
             "description": c.description + ("; one value per period, case or row" if c.id in listed else "") if c.description
             else ("one value per period, case or row" if c.id in listed else None)} for c in rows]


def record_index(record: Record, firm_angle: str | None) -> str:
    """The record as families: one line per path or per family of periods (any format, from its values)."""
    families: dict[str, list] = defaultdict(list)
    for r in record.by_path.values():
        families[r.family or r.path].append(r)
    lines = []
    for fam in sorted(families):
        rows = families[fam]
        first = rows[0]
        mark = "  [this firm's angle]" if firm_angle and fam.startswith((f"angle:{firm_angle}.", f"angle_table:{firm_angle}.")) else ""
        kind = f"{first.value_kind or first.kind}{' ' + first.unit if first.unit and first.value_kind == 'money' else ''}"
        if len(rows) == 1 and rows[0].path == fam:
            value = first.number_value if first.number_value is not None else (first.text_value or "")[:60]
            lines.append(f"{fam} | {kind} | {(first.label or '')[:70]} | {value}{mark}")
        else:
            periods = [r for r in rows if r.period is not None]
            if periods:
                shown = ", ".join(f"{r.period}{' F' if r.is_forecast else ' A'}" for r in periods[:14])
                lines.append(f"{fam} | {kind} | {(first.label or '')[:60]} | periods: {shown}{mark}")
            else:
                keys = list(dict.fromkeys(r.path.split("[", 1)[-1].split("]", 1)[0] for r in rows))
                fields = list(dict.fromkeys(r.path.split("].", 1)[1] for r in rows if "]." in r.path and r.path.startswith(fam + "[")))
                example = rows[0].path
                desc = f"rows: {', '.join(keys[:12])}" + (f" | fields: {', '.join(fields[:10])}" if fields else "")
                lines.append(f"{fam} | {kind} | {desc} | e.g. {example}{mark}")
    return "\n".join(lines)


def make_validate(record: Record, wanted: list[dict]):
    kinds = {c["name"]: c for c in wanted}

    def check_value(where, row, concept):
        errors = []
        vk = (concept or {}).get("value_kind")
        if concept and concept["type"] == "figure" and row.number_value is None:
            errors.append(f"{where}: a figure needs a number, but {row.path} holds text")
        if vk in COMPATIBLE and row.value_kind and row.value_kind not in COMPATIBLE[vk] | {"text"}:
            errors.append(f"{where}: concept is {vk} but {row.path} is {row.value_kind}")
        return errors

    def validate(answer: dict) -> list[str]:
        errors = []
        got = {b.get("concept"): b for b in answer.get("bindings") or []}
        for name in kinds:
            if name not in got:
                errors.append(f"concept {name!r} has no binding")
        for name, b in got.items():
            where = f"concept {name!r}"
            if name not in kinds:
                errors.append(f"{where} is not one of the concepts")
                continue
            status = b.get("status")
            if status == "found":
                path = str(b.get("path") or "")
                row = record.resolve(path)
                family = record.rows(path) if row is None else []
                if family and kinds[name].get("list"):
                    errors += check_value(where, family[0], kinds[name])   # a whole column or list, where one is used
                elif family:
                    errors.append(f"{where}: {path!r} is a whole column or list, but this concept is one value; "
                                  f"pick the item, e.g. {family[0].path}")
                elif row is None:
                    rel = RELATIVE.match(str(b.get("path") or ""))
                    hint = " (no such family or period)" if rel else " (not in the index; use a path as listed, or a relative path)"
                    errors.append(f"{where}: path {b.get('path')!r} does not resolve{hint}")
                else:
                    errors += check_value(where, row, kinds[name])
            elif status == "derive":
                if record.evaluate(str(b.get("formula") or "")) is None:
                    errors.append(f"{where}: formula {b.get('formula')!r} does not evaluate (every path must resolve to a number)")
            elif status != "missing":
                errors.append(f"{where}: status must be found, derive or missing")
        return errors

    return validate


def bind(firm_name: str) -> dict:
    with SessionLocal() as session:
        firm = session.scalar(select(S.Firm).where(S.Firm.name == firm_name))
        spec = session.scalar(select(S.Spec).where(S.Spec.firm_id == firm.id))
        version = session.scalar(select(S.SpecVersion).where(S.SpecVersion.spec_id == spec.id)
                                 .order_by(S.SpecVersion.number.desc()))
        revision = session.scalar(select(RecordRevision).where(RecordRevision.firm_id == firm.id)
                                  .order_by(RecordRevision.number.desc()))
        record = Record(session, revision.id)
        # which angle of the record is this firm's view: the one named after the firm's memo
        angle = next((p.split(".")[0].split(":")[1] for p, r in record.by_path.items()
                      if p.startswith("angle:") and p.endswith(".memo_name") and r.text_value == spec.name), None)
        wanted = concepts(session, version.id)
        text = (f"FIRM MEMO: {spec.name}; this firm's angle: {angle or 'none'}\n\n=== CONCEPTS\n"
                + "\n".join(f"{c['name']} | {c['type']} | {c['value_kind'] or ''} | {c['description'] or ''}" for c in wanted)
                + f"\n\n=== RECORD INDEX ({revision.record_schema})\n{record_index(record, angle)}")
        result = call_with_checks(INSTRUCTIONS, text, [], make_validate(record, wanted))
        session.execute(delete(S.Binding).where(S.Binding.firm_id == firm.id,
                                                S.Binding.record_schema == revision.record_schema))
        counts = defaultdict(int)
        for b in result["answer"].get("bindings") or []:
            status = b.get("status")
            kind = {"found": "path", "derive": "derived"}.get(status, "missing")
            counts[kind] += 1
            session.add(S.Binding(firm_id=firm.id, record_schema=revision.record_schema, concept_name=b.get("concept"),
                                  kind=kind, path=b.get("path") if kind == "path" else None,
                                  formula=b.get("formula") if kind == "derived" else None))
        session.commit()
        att = result["attempts"]
        return {"firm": firm_name, "concepts": len(wanted), "angle": angle, **counts, "unresolved": result["unresolved"],
                "calls": len(att), "tokens_in": sum(a["prompt_tokens"] or 0 for a in att),
                "tokens_out": sum(a["completion_tokens"] or 0 for a in att), "answer": result["answer"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("firm")
    r = bind(parser.parse_args().firm)
    sys.stdout.write(f"{r['firm']}: {r['concepts']} concepts → found {r.get('path', 0)}, derived {r.get('derived', 0)}, "
                     f"missing {r.get('missing', 0)}; angle {r['angle']}; calls {r['calls']}, tokens {r['tokens_in']}/{r['tokens_out']}; "
                     f"still failing {len(r['unresolved'])}\n")
    for b in r["answer"].get("bindings") or []:
        if b.get("status") == "missing":
            sys.stdout.write(f"  missing {b.get('concept')}: {b.get('why')}\n")


if __name__ == "__main__":
    main()
