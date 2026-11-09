"""A firm's figures as its memos write them: a concept's value in a deal record revision, through the firm's
binding, written in the firm's formats (£22.9m, (1,200), 18 March 2025…). The generator writes every figure of a
new memo with this, and the door writes a figure a person or the agent puts into a box, so a figure reads the
same wherever it comes from. Nothing here knows any firm.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import spec as S
from app.services.formats import format_date, format_number
from app.services.records import RELATIVE, Record

ITEM = {"money": "money", "percent": "percent", "multiple": "multiple", "count": "count", "ratio": "multiple",
        "date": "date", "per_share": "per_share", "currency": "currency", "number": "count"}
CURRENCY = {"£": "GBP", "€": "EUR", "$": "USD", "CHF": "CHF", "¥": "JPY"}


def by(rows, key) -> dict:
    out = defaultdict(list)
    for r in rows:
        out[getattr(r, key)].append(r)
    return out


def _value(row):
    """A record row's value: its number, else its text, else its date (as text)."""
    if row.number_value is not None:
        return row.number_value
    if row.text_value is not None:
        return row.text_value
    d = getattr(row, "date_value", None)
    return d.isoformat() if d is not None else None


class Figures:
    def __init__(self, session: Session, firm, version, revision):
        self.s = session
        self.firm, self.version, self.revision = firm, version, revision
        q = session.scalars
        self.record = Record(session, revision.id)
        self.concepts = {c.id: c for c in q(select(S.Concept).where(S.Concept.spec_version_id == version.id))}
        self.concept_named = {c.name: c for c in self.concepts.values()}
        self.bindings = {b.concept_name: b for b in q(select(S.Binding).where(
            S.Binding.firm_id == firm.id, S.Binding.record_schema == revision.record_schema))}
        self.formats = self.load_formats()
        self.counts: Counter = Counter()

    def chip(self, name: str, context: str = "prose") -> dict:
        """A figure as a chip segment's fields, or ValueError when the firm has no such figure or the record
        has no value for it (a chip is never made up). The name is a concept of the firm's, or a path of the deal
        record (one value: field:kinmoor.arr), written by the kind of value it is."""
        if ":" in name:
            return self.chip_at(name, context)
        concept = self.concept_named.get(name)
        if concept is None or concept.kind == "memo_data":
            raise ValueError(f"the firm has no figure called {name!r}")
        row, value, path = self.concept_value(name)
        if value is None:
            raise ValueError(f"the deal record has no value for {name!r}")
        text = self.write_value(value, self.kind_of(concept, row), context, row.unit if row is not None else None)
        if text.startswith("[no data"):
            raise ValueError(f"{name!r} cannot be written: {text}")
        return {"kind": "chip", "text": text, "concept_name": name, "record_path": path, "value_snapshot": str(value)}

    def chip_at(self, path: str, context: str = "prose") -> dict:
        """One value of the deal record as a chip, by its path."""
        row = self.record.resolve(path)
        value = None if row is None else _value(row)
        if value is None:
            raise ValueError(f"the deal record has no single value at {path!r}")
        text = self.write_value(value, row.value_kind, context, row.unit)
        if not text or text.startswith("[no data"):
            raise ValueError(f"{path!r} cannot be written: {text or 'empty'}")
        return {"kind": "chip", "text": text, "concept_name": None, "record_path": row.path, "value_snapshot": str(value)}

    def fields(self, context: str = "prose") -> list[dict]:
        """The record's single values (field:…) as the firm writes them: {path, value, label}."""
        out = []
        for path, row in self.record.by_path.items():
            if not path.startswith("field:"):                  # one value each (dated as of a day)
                continue
            value = _value(row)
            if value is None:
                continue
            text = self.write_value(value, row.value_kind, context, row.unit)
            if text and not text.startswith("[no data"):
                out.append({"path": path, "value": text[:80], "label": row.label or ""})
        return out

    # --- formats ---------------------------------------------------------------------------------
    def load_formats(self) -> dict[tuple[str, str], list[dict]]:
        formats = self.s.scalars(select(S.Format).where(S.Format.spec_version_id == self.version.id)).all()
        scales = by(self.s.scalars(select(S.FormatScale).where(S.FormatScale.format_id.in_([f.id for f in formats]))).all(), "format_id")
        out = defaultdict(list)
        for f in formats:
            out[(f.item, f.context)].append({c: getattr(f, c) for c in (
                "item", "context", "applies_to", "decimals", "symbol", "symbol_position", "unit_display", "negative_style",
                "thousands_separator", "decimal_separator", "suffix", "pattern", "example")}
                | {"scales": [{"scale": x.scale, "from_value": x.from_value, "decimals": x.decimals} for x in scales[f.id]]})
        return out

    def format_for(self, item: str, context: str) -> dict | None:
        """The format of a kind of value in a context. A table whose header carries the unit ("£k") uses a format
        that writes the unit in the header; nowhere else is such a format used. Otherwise: a general format of the
        context, else of 'any'; a format that applies only to some figures (applies_to) only when there is none."""
        candidates = {ctx: [f for f in self.formats.get((item, ctx)) or []] for ctx in (context, "any")}
        if context == "table":
            header = [f for fs in candidates.values() for f in fs if f.get("unit_display") == "header"]
            if header:
                return header[0]
        candidates = {ctx: [f for f in fs if f.get("unit_display") != "header"] for ctx, fs in candidates.items()}
        for ctx in (context, "any"):
            general = [f for f in candidates[ctx] if not f.get("applies_to")]
            if general:
                return general[0]
        for ctx in ("any", context):                               # no general one: the reporting ('any') format first
            if candidates[ctx]:
                return candidates[ctx][0]
        return None

    def kind_of(self, concept, row) -> str | None:
        """The concept's value kind when it is one the formats know, else the record's (11)."""
        ck = concept.value_kind if concept else None
        return ck if ck in ITEM else (row.value_kind if row is not None else ck)

    def write_value(self, value, value_kind: str | None, context: str, unit: str | None = None) -> str:
        """A record value as the firm writes it."""
        if value is None:
            return ""
        if isinstance(value, str):
            if ITEM.get(value_kind or "") == "date" and re.match(r"\d{4}-\d{2}-\d{2}", value):
                fmt = self.format_for("date", context)
                return format_date(value, fmt) if fmt else value
            if value.startswith("[") and value.endswith("]"):     # a list in the record: one item per line (16)
                try:
                    items = json.loads(value)
                    if not isinstance(items, list):
                        return value
                    # an item with fields reads as its texts: "Minimum cash: Cash at bank at each month end…"
                    return "\n".join(": ".join(str(v) for v in x.values() if isinstance(v, (str, int, float)))
                                     if isinstance(x, dict) else str(x) for x in items)
                except ValueError:
                    return value
            return value
        if value_kind == "months":                                  # a duration: the number and its unit (4)
            fmt = self.format_for("count", context) or {"item": "count"}
            n = format_number(float(value), {**fmt, "decimals": 0 if float(value).is_integer() else 1})
            return f"{n} {'month' if value == 1 else 'months'}"
        item = ITEM.get(value_kind or "") or ("count" if float(value).is_integer() else None)
        fmt = self.format_for(item, context) if item else None
        if not fmt:
            return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"
        if item == "money" and unit and fmt.get("symbol") and CURRENCY.get(fmt["symbol"], unit) != unit:
            # the firm writes another currency and the record has no rate: say so instead of a wrong figure (13)
            self.counts["needs_fx"] += 1
            return f"[no data: {unit}→{CURRENCY.get(fmt['symbol'])} rate]"
        if value < 0:                                              # negatives follow the firm's negative style
            neg = self.format_for("negative", context)
            if neg and neg.get("negative_style"):
                fmt = {**fmt, "negative_style": neg["negative_style"]}
        return format_number(float(value), fmt)

    # --- record values for concepts ---------------------------------------------------------------
    def concept_value(self, name: str | None):
        """(record row or None, value, path) for a concept through the firm's binding."""
        b = self.bindings.get(name or "")
        if not b or b.kind == "missing":
            return None, None, None
        if b.kind == "derived":
            return None, self.record.evaluate(b.formula or ""), b.formula
        row = self.record.resolve(b.path or "")
        if row is None:
            return None, None, b.path
        return row, row.number_value if row.number_value is not None else row.text_value, row.path

    def family_of(self, name: str | None) -> list:
        """The periods (or rows) of a concept's family, oldest first."""
        b = self.bindings.get(name or "")
        if not b or b.kind != "path" or not b.path:
            return []
        m = RELATIVE.match(b.path)
        family = m.group("family") if m else (self.record.by_path[b.path].family if b.path in self.record.by_path else b.path)
        return self.record.families.get(family) or self.record.rows(family)

