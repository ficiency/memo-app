"""Deal records in the database: load a revision from a file, and resolve paths against a revision.

Paths are those of the record readers (app/readers/deal_record.py), plus relative periods:
    <family>@last_actual          the latest period that is not a forecast
    <family>@last_actual-1        the one before it (-2, -3…)
    <family>@first_forecast       the earliest forecast period (+1, +2… for the next ones)
    <family>@latest               the latest period of any kind
A family is a path without its period: field:arr, table:pl_summary.revenue, series:arr_monthly.
A formula combines paths with + - * / ( ) and numbers.

Usage (from backend/):
    python -m app.services.records load <firm> <record file>
"""
from __future__ import annotations

import argparse
import ast
import operator
import re
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import REPO
from app.db.record import Deal, RecordRevision, RecordValue
from app.db.session import SessionLocal
from app.db.spec import Firm
from app.readers import deal_record
from app.readers.deal_record import period_end, fiscal_year_end

READERS = {deal_record.FORMAT: deal_record}                     # one reader per record format
RELATIVE = re.compile(r"^(?P<family>.+)@(?P<anchor>last_actual|first_forecast|latest)(?P<offset>[+-]\d+)?$")
PATH_TOKEN = re.compile(r"(?:field|table|angle_table|series|narrative|company|angle):[^\s+*/()]+")


def load(session: Session, firm_name: str, path: str | Path, fmt: str = deal_record.FORMAT) -> RecordRevision:
    """Read a record file and store it as the firm's next revision of that deal."""
    data = READERS[fmt].read(path)
    firm = session.scalar(select(Firm).where(Firm.name == firm_name))
    if not firm:
        raise ValueError(f"no firm {firm_name!r}")
    deal = session.scalar(select(Deal).where(Deal.name == data["deal"]))
    if not deal:
        deal = Deal(name=data["deal"])
        session.add(deal)
        session.flush()
    previous = session.scalar(select(RecordRevision).where(RecordRevision.deal_id == deal.id, RecordRevision.firm_id == firm.id)
                              .order_by(RecordRevision.number.desc()))
    revision = RecordRevision(deal_id=deal.id, firm_id=firm.id, number=(previous.number + 1) if previous else 1,
                              record_schema=data["format"], previous_id=previous.id if previous else None,
                              as_of=date.fromisoformat(data["as_of"]) if data.get("as_of") else None)
    session.add(revision)
    session.flush()
    session.add_all(RecordValue(revision_id=revision.id, date_value=date.fromisoformat(r["date_value"]) if r.get("date_value") else None,
                                **{k: r[k] for k in ("path", "family", "label", "value_kind", "is_forecast", "kind",
                                                     "number_value", "text_value", "unit", "period", "source")})
                    for r in data["rows"])
    session.commit()
    return revision


class Record:
    """A revision's values in memory, to resolve many paths quickly."""

    def __init__(self, session: Session, revision_id):
        self.revision = session.get(RecordRevision, revision_id)
        rows = session.scalars(select(RecordValue).where(RecordValue.revision_id == revision_id)).all()
        self.by_path = {r.path: r for r in rows}
        self.families: dict[str, list[RecordValue]] = {}
        for r in rows:
            if r.period is not None:
                self.families.setdefault(r.family, []).append(r)
        fy = fiscal_year_end(self.text("company:fiscal_year_end"))
        for fam in self.families.values():
            fam.sort(key=lambda r: period_end(r.period, fy) or date.min)

    def text(self, path: str) -> str | None:
        r = self.by_path.get(path)
        return r.text_value if r else None

    def rows(self, path: str) -> list[RecordValue]:
        """Every value of a family (a table column, a list, a series): for concepts that are a whole column
        or list, such as one value per case or per row."""
        return [r for r in self.by_path.values() if r.family == path and r.path != path]

    def resolve(self, path: str) -> RecordValue | None:
        """The value a path points at, or None. Relative paths pick a period from the family; a family path
        (a whole column or list) resolves to its first value."""
        if path in self.by_path:
            return self.by_path[path]
        family = self.rows(path)
        if family:
            return family[0]
        m = RELATIVE.match(path)
        if not m:
            return None
        rows = self.families.get(m.group("family")) or []
        offset = int(m.group("offset") or 0)
        if m.group("anchor") == "latest":
            pool, index = rows, len(rows) - 1 + offset
        elif m.group("anchor") == "last_actual":
            pool = [r for r in rows if not r.is_forecast]
            index = len(pool) - 1 + offset
        else:
            pool = [r for r in rows if r.is_forecast]
            index = offset
        return pool[index] if 0 <= index < len(pool) else None

    def evaluate(self, formula: str) -> float | None:
        """A formula over paths, e.g. 'field:arr@last_actual / field:sites@last_actual'."""
        values = {}
        for i, token in enumerate(PATH_TOKEN.findall(formula)):
            r = self.resolve(token)
            if r is None or r.number_value is None:
                return None
            values[f"v{i}"] = r.number_value
            formula = formula.replace(token, f"v{i}", 1)
        return _safe_eval(formula, values)


OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
       ast.USub: operator.neg}


def _safe_eval(expression: str, names: dict) -> float | None:
    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.Name) and node.id in names:
            return names[node.id]
        if isinstance(node, ast.BinOp) and type(node.op) in OPS:
            return OPS[type(node.op)](walk(node.left), walk(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in OPS:
            return OPS[type(node.op)](walk(node.operand))
        raise ValueError("not allowed")
    try:
        return float(walk(ast.parse(expression, mode="eval")))
    except (ValueError, SyntaxError, ZeroDivisionError, TypeError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("load")
    p.add_argument("firm")
    p.add_argument("file", type=Path)
    args = parser.parse_args()
    with SessionLocal() as session:
        rev = load(session, args.firm, args.file if args.file.is_absolute() else REPO / args.file)
        n = session.scalar(select(func.count()).select_from(RecordValue).where(RecordValue.revision_id == rev.id))
        sys.stdout.write(f"{args.firm}: record revision {rev.number} ({rev.record_schema}, as of {rev.as_of}), {n} values\n")


if __name__ == "__main__":
    main()
