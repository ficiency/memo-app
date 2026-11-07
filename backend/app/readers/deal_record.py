"""Read a deal record in the Soal deal-record format into normalised values, one per datum.

Each value has a path, a kind, the value, its unit and period, whether the period is a forecast, and its
family (the path without the period), so later code can ask for "the last actual year" of a figure
without knowing which year that is. A record in another format needs its own reader producing the same
rows; nothing after the reader depends on the JSON's shape.

Paths:
    field:<id>                               a field (fields[])
    table:<table>[<row key>].<column>        a table cell; the row key is the row's first value
    angle_table:<angle>.<table>[<row>].<col> a cell of a table inside an angle
    series:<name>[<x>]                       a point of a series
    narrative:<key> | narrative:<key>[<id or index>].<field>
    company:<key> | angle:<angle>.<key>      identity and an angle's own text

Conventions of the format (data/deal/README.md): money in whole currency units, percentages as fractions,
multiples as plain numbers, ISO dates; FY labels end on the company's fiscal year end.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

FORMAT = "soal-deal-record-v1"
MONTHS = {m: i for i, m in enumerate(("january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"), start=1)}


def fiscal_year_end(text: str | None) -> tuple[int, int]:
    """'31 March' → (3, 31); December 31 when the record does not say."""
    m = re.match(r"(\d{1,2})\s+([A-Za-z]+)", text or "")
    if m and m.group(2).lower() in MONTHS:
        return MONTHS[m.group(2).lower()], int(m.group(1))
    return 12, 31


def period_end(period, fy_end: tuple[int, int]) -> date | None:
    """The date a period ends: an ISO date, a month (2026-08), a year, or an FY label (FY26, FY2026)."""
    if period is None:
        return None
    p = str(period).strip()
    m = re.fullmatch(r"FY\s?'?(\d{2}|\d{4})", p, re.I)
    if m:
        year = int(m.group(1))
        year = 2000 + year if year < 100 else year
        return date(year, *fy_end)
    m = re.fullmatch(r"(\d{4})-(\d{2})(?:-(\d{2}))?", p)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        if m.group(3):
            return date(y, mo, int(m.group(3)))
        nxt = date(y + (mo == 12), mo % 12 + 1, 1)
        return date.fromordinal(nxt.toordinal() - 1)          # last day of that month
    if re.fullmatch(r"\d{4}", p):
        return date(int(p), 12, 31)
    return None


def _kind(value, declared: str | None) -> str:
    if declared in ("money", "count", "percent", "number", "multiple", "months"):
        return "number" if isinstance(value, (int, float)) and not isinstance(value, bool) else "text"
    if declared == "date":
        return "date"
    return "number" if isinstance(value, (int, float)) and not isinstance(value, bool) else "text"


def _text(v) -> str:
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)


def read(path: str | Path) -> dict:
    record = json.loads(Path(path).read_text(encoding="utf-8"))
    as_of = date.fromisoformat(str(record.get("as_of"))[:10]) if record.get("as_of") else None
    fy_end = fiscal_year_end((record.get("company") or {}).get("fiscal_year_end"))
    rows: list[dict] = []

    def add(path_, value, declared=None, unit=None, period=None, family=None, label=None, source=None):
        end = period_end(period, fy_end)
        kind = _kind(value, declared)
        rows.append({
            "path": path_, "kind": kind, "value_kind": declared, "label": label,
            "number_value": float(value) if kind == "number" else None,
            "text_value": None if kind == "number" or value is None else _text(value),
            "date_value": str(value)[:10] if kind == "date" and isinstance(value, str) else None,
            "unit": unit, "period": None if period is None else str(period),
            "is_forecast": bool(end and as_of and end > as_of), "family": family or path_, "source": source})

    for f in record.get("fields") or []:
        # the id without its period segment, wherever it sits (arr.fy26 → arr, pl.fy27.revenue → pl.revenue)
        family = ".".join(seg for seg in f["id"].split(".")
                          if not re.fullmatch(r"fy\d{2,4}|q[1-4](-?\d{2,4})?|h[12](-?\d{2,4})?|\d{4}(-\d{2})?", seg, re.I))
        add(f"field:{f['id']}", f.get("value"), f.get("kind"), f.get("unit"), f.get("period"),
            f"field:{family}", f.get("label"), f.get("source"))

    def table(prefix: str, name: str, t: dict) -> None:
        cols = [c if isinstance(c, dict) else {"id": str(c)} for c in t.get("columns") or []]
        for row in t.get("rows") or []:
            key = str(row[0])
            period = key if period_end(key, fy_end) else None
            for col, value in zip(cols[1:], row[1:]):
                add(f"{prefix}{name}[{key}].{col['id']}", value, col.get("kind"), t.get("unit"), period,
                    f"{prefix}{name}.{col['id']}", f"{col.get('label') or col['id']} ({key})", t.get("source"))

    for name, t in (record.get("tables") or {}).items():
        table("table:", name, t)
    for name, s in (record.get("series") or {}).items():
        for x, y in zip(s.get("x") or [], s.get("y") or []):
            add(f"series:{name}[{x}]", y, "number", s.get("unit"), x, f"series:{name}", name, s.get("source"))
    for key, v in (record.get("narrative") or {}).items():
        if isinstance(v, list):
            for i, item in enumerate(v):
                # an item is named by its id, else by its first short text (a term, a workstream), else its index,
                # so a path says which item it is: narrative:series_c_terms[Anti-dilution].text
                ident = i
                if isinstance(item, dict):
                    first = next((x for x in item.values() if isinstance(x, str) and len(x) <= 60), None)
                    ident = item.get("id") or first or i
                if isinstance(item, dict):
                    for k, val in item.items():
                        if k != "id":
                            add(f"narrative:{key}[{ident}].{k}", val, "text", family=f"narrative:{key}")
                else:
                    add(f"narrative:{key}[{ident}]", item, "text", family=f"narrative:{key}")
        else:
            add(f"narrative:{key}", v, "text")
    for key, v in (record.get("company") or {}).items():
        add(f"company:{key}", v, "number" if isinstance(v, (int, float)) else "text")
    for angle, body in (record.get("angles") or {}).items():
        for key, v in body.items():
            if key == "tables":
                for name, t in v.items():
                    table(f"angle_table:{angle}.", name, t)
            else:
                add(f"angle:{angle}.{key}", v, "text")
    angles = {name: body.get("memo_name") for name, body in (record.get("angles") or {}).items()}
    return {"format": FORMAT, "deal": (record.get("company") or {}).get("name") or record.get("record_id"),
            "as_of": str(as_of) if as_of else None, "currency": record.get("reporting_currency"),
            "angles": angles, "rows": rows}
