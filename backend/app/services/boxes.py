"""New boxes a person (or the agent) puts on a free slide: a table or a chart built from the deal record, every
value bound to it (a table's cells are chips, a chart's points come from the record's families), written in the
firm's formats. The door adds them; nothing here knows any firm.

A table is one of the record's tables (table:<name> or angle_table:<angle>.<name>): its rows and columns, or the
ones asked for. A chart is one or more of the record's period families (field:arr, table:pl_summary.revenue,
series:arr_monthly…), the last periods of each.
"""
from __future__ import annotations

import re
import uuid

from app.db.memo import (ChartPoint, ChartSeries, Element, ElementChart, ElementParagraph, ElementTable, Segment,
                         TableCell, TableColumn, TableRow)
from app.services.figures import Figures

CELL = re.compile(r"^(?P<table>(?:table|angle_table):[^\[]+)\[(?P<row>.+)\]\.(?P<col>[^.\]]+)$")


def record_tables(figures: Figures) -> dict[str, dict]:
    """The record's tables: {name: {"rows": [...], "columns": [...]}} in the record's order."""
    out: dict[str, dict] = {}
    for path in figures.record.by_path:
        m = CELL.match(path)
        if not m:
            continue
        t = out.setdefault(m.group("table"), {"rows": [], "columns": []})
        if m.group("row") not in t["rows"]:
            t["rows"].append(m.group("row"))
        if m.group("col") not in t["columns"]:
            t["columns"].append(m.group("col"))
    return out


def period_families(figures: Figures) -> dict[str, str]:
    """The record's families with several periods (what a chart can show): {family: label}."""
    out = {}
    for family, rows in figures.record.families.items():
        numbers = [r for r in rows if r.number_value is not None and r.period]
        if len(numbers) >= 2:
            label = re.sub(r"\s*[,(]?\s*(FY\s?\d{2,4}|\d{4}(-\d{2})?(-\d{2})?)\)?.*$", "", numbers[0].label or family).strip()
            out[family] = label or family
    return out


def _label(text: str) -> str:
    return text.replace("_", " ").strip().capitalize()


def build_table(figures: Figures, el: Element, name: str, rows: list[str] | None, columns: list[str] | None,
                table_style_id) -> list:
    """The table's rows (header, then one row per record row), its cells as chips of the record. Raises
    ValueError for a table the record does not have."""
    tables = record_tables(figures)
    if name not in tables:
        raise ValueError(f"the deal record has no table {name!r}")
    t = tables[name]
    rows = [r for r in (rows or t["rows"]) if r in t["rows"]][:20]
    columns = [c for c in (columns or t["columns"]) if c in t["columns"]][:8]
    if not rows or not columns:
        raise ValueError("choose at least one row and one column of the table")
    out: list = []
    table = ElementTable(id=uuid.uuid4(), element_id=el.id, table_style_id=table_style_id)
    out.append(table)
    width = (el.w_in or 6) / (len(columns) + 1)
    for i in range(len(columns) + 1):
        out.append(TableColumn(id=uuid.uuid4(), element_table_id=table.id, position=i, width_in=width))

    def cell(r: int, c: int, kind: str, segment: dict) -> None:
        tc = TableCell(id=uuid.uuid4(), element_table_id=table.id, row=r, col=c, row_span=1, col_span=1, kind=kind)
        para = ElementParagraph(id=uuid.uuid4(), table_cell_id=tc.id, element_id=None, position=0, level=0)
        out.extend([tc, para, Segment(id=uuid.uuid4(), paragraph_id=para.id, position=0, format_item=None, **segment)])

    text = lambda s: {"kind": "text", "text": s, "concept_name": None, "record_path": None, "value_snapshot": None}
    out.append(TableRow(id=uuid.uuid4(), element_table_id=table.id, position=0, kind="header"))
    cell(0, 0, "fixed", text(""))
    for c, col in enumerate(columns, start=1):
        cell(0, c, "fixed", text(_label(col)))
    for r, key in enumerate(rows, start=1):
        out.append(TableRow(id=uuid.uuid4(), element_table_id=table.id, position=r, kind="data"))
        cell(r, 0, "fixed", text(key))
        for c, col in enumerate(columns, start=1):
            v = figures.record.by_path.get(f"{name}[{key}].{col}")
            if v is None:
                cell(r, c, "data", text(""))
                continue
            value = v.number_value if v.number_value is not None else v.text_value
            written = figures.write_value(value, v.value_kind, "table", v.unit)
            cell(r, c, "data", {"kind": "chip", "text": written, "concept_name": None, "record_path": v.path,
                                "value_snapshot": None if value is None else str(value)})
    return out


def build_chart(figures: Figures, el: Element, chart_type: str, families: list[str], periods: int) -> list:
    """A chart of record families: one series each, the last periods of each, forecasts marked."""
    known = period_families(figures)
    families = [f for f in families if f in known][:4]
    if not families:
        raise ValueError("choose a figure of the record that has several periods")
    chart = ElementChart(id=uuid.uuid4(), element_id=el.id, chart_type=chart_type if chart_type in ("column", "bar", "line") else "column")
    out: list = [chart]
    for i, family in enumerate(families):
        series = ChartSeries(id=uuid.uuid4(), element_chart_id=chart.id, position=i, name=known[family], concept_name=None,
                             record_path=family, color_ref=None)
        out.append(series)
        rows = [r for r in figures.record.families[family] if r.number_value is not None and r.period][-max(2, min(periods, 12)):]
        labels = categories([str(r.period) for r in rows])
        for j, (r, label) in enumerate(zip(rows, labels)):
            out.append(ChartPoint(id=uuid.uuid4(), chart_series_id=series.id, position=j, category=label,
                                  value=r.number_value, is_forecast=r.is_forecast))
    return out


def categories(periods: list[str]) -> list[str]:
    """Periods as a chart's categories: dates that all end on the same day of the year read as years (fiscal
    year ends), other dates as months; labels such as FY26 stay as they are."""
    dates = [re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", p) for p in periods]
    if periods and all(dates):
        if len({(m.group(2), m.group(3)) for m in dates}) == 1:
            return [m.group(1) for m in dates]
        return [f"{m.group(1)}-{m.group(2)}" for m in dates]
    return periods
