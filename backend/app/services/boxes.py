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
                table_style_id, headers: dict | None = None, labels: dict | None = None) -> list:
    """The table's rows (header, then one row per record row), its cells as chips of the record. Headers and row
    labels a person wrote (by column and row of the record) are kept. Raises ValueError for a table the record
    does not have."""
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
    headers, labels = headers or {}, labels or {}
    cell(0, 0, "fixed", text(headers.get("", "")))
    for c, col in enumerate(columns, start=1):
        cell(0, c, "fixed", text(headers.get(col, _label(col))))
    for r, key in enumerate(rows, start=1):
        out.append(TableRow(id=uuid.uuid4(), element_table_id=table.id, position=r, kind="data"))
        cell(r, 0, "fixed", text(labels.get(key, key)))
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


def build_chart(figures: Figures, el: Element, chart_type: str, families: list[str], periods: int,
                names: dict | None = None) -> list:
    """A chart of record families: one series each, the last periods of each, forecasts marked. A series is
    named as the person named it, else as the record labels the figure."""
    known = period_families(figures)
    families = [f for f in families if f in known][:4]
    if not families:
        raise ValueError("choose a figure of the record that has several periods")
    chart = ElementChart(id=uuid.uuid4(), element_id=el.id, chart_type=chart_type if chart_type in ("column", "bar", "line") else "column")
    out: list = [chart]
    for i, family in enumerate(families):
        name = str((names or {}).get(family) or "").strip() or known[family]
        series = ChartSeries(id=uuid.uuid4(), element_chart_id=chart.id, position=i, name=name, concept_name=None,
                             record_path=family, color_ref=None)
        out.append(series)
        rows = [r for r in figures.record.families[family] if r.number_value is not None and r.period][-max(2, min(periods, 12)):]
        labels = categories([str(r.period) for r in rows])
        for j, (r, label) in enumerate(zip(rows, labels)):
            out.append(ChartPoint(id=uuid.uuid4(), chart_series_id=series.id, position=j, category=label,
                                  value=r.number_value, is_forecast=r.is_forecast))
    return out


def build_literal_table(el: Element, rows: list[list[str]], table_style_id) -> list:
    """A table as it came (an uploaded slide's): the first row a header, every cell its text. Its numbers are
    not the record's: they show as typed figures among the memo's pendings."""
    rows = [[str(c) for c in r[:12]] for r in rows[:40] if r]
    if not rows:
        raise ValueError("the table is empty")
    n_cols = max(len(r) for r in rows)
    out: list = []
    table = ElementTable(id=uuid.uuid4(), element_id=el.id, table_style_id=table_style_id)
    out.append(table)
    for i in range(n_cols):
        out.append(TableColumn(id=uuid.uuid4(), element_table_id=table.id, position=i, width_in=(el.w_in or 6) / n_cols))
    for r, row in enumerate(rows):
        out.append(TableRow(id=uuid.uuid4(), element_table_id=table.id, position=r, kind="header" if r == 0 else "data"))
        for c in range(n_cols):
            tc = TableCell(id=uuid.uuid4(), element_table_id=table.id, row=r, col=c, row_span=1, col_span=1,
                           kind="fixed" if r == 0 else "text")
            para = ElementParagraph(id=uuid.uuid4(), table_cell_id=tc.id, element_id=None, position=0, level=0)
            text = row[c] if c < len(row) else ""
            out.extend([tc, para, Segment(id=uuid.uuid4(), paragraph_id=para.id, position=0, format_item=None, kind="text", text=text,
                                          concept_name=None, record_path=None, value_snapshot=None)])
    return out


def build_literal_chart(el: Element, chart_type: str, categories: list, series: list[dict]) -> list:
    """A chart as it came: its categories and series with their values. Not bound to the record: a pending says
    so until it is rebuilt from the record or accepted."""
    if not series or not categories:
        raise ValueError("the chart has no data")
    chart = ElementChart(id=uuid.uuid4(), element_id=el.id, chart_type=chart_type if chart_type in ("column", "bar", "line") else "column")
    out: list = [chart]
    for i, s in enumerate(series[:6]):
        cs = ChartSeries(id=uuid.uuid4(), element_chart_id=chart.id, position=i, name=str(s.get("name") or f"Series {i + 1}"),
                         concept_name=None, record_path=None, color_ref=None)
        out.append(cs)
        for j, (cat, v) in enumerate(zip(categories[:24], s.get("values") or [])):
            out.append(ChartPoint(id=uuid.uuid4(), chart_series_id=cs.id, position=j, category=str(cat),
                                  value=float(v) if v is not None else None, is_forecast=False))
    return out


def written(d, el) -> dict:
    """What a person wrote on a box of theirs, by record row and column (MemoData d): {"headers": {column: text,
    "": corner}, "labels": {row: text}} for a table, {"names": {family: name}} for a chart."""
    table, chart = d.table_of.get(el.id), d.chart_of.get(el.id)
    if chart is not None:
        return {"names": {x.record_path: x.name for x in d.series_of.get(chart.id, []) if x.record_path and x.name}}
    if table is None:
        return {}
    text = lambda c: "".join(x.text or "" for _, segs in d.paragraphs(cell_id=c.id) for x in segs)
    cells = d.cells_of.get(table.id, [])
    col_key, row_key = {}, {}
    for c in cells:
        for _, segs in d.paragraphs(cell_id=c.id):
            for x in segs:
                m = CELL.match(x.record_path or "")
                if m:
                    col_key[c.col], row_key[c.row] = m.group("col"), m.group("row")
    headers = {col_key[c.col]: text(c) for c in cells if c.row == 0 and c.col in col_key}
    headers[""] = next((text(c) for c in cells if c.row == 0 and c.col == 0), "")
    labels = {row_key[c.row]: text(c) for c in cells if c.col == 0 and c.row in row_key}
    return {"headers": headers, "labels": labels}


def categories(periods: list[str]) -> list[str]:
    """Periods as a chart's categories: dates that all end on the same day of the year read as years (fiscal
    year ends), other dates as months; labels such as FY26 stay as they are."""
    dates = [re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", p) for p in periods]
    if periods and all(dates):
        if len({(m.group(2), m.group(3)) for m in dates}) == 1:
            return [m.group(1) for m in dates]
        return [f"{m.group(1)}-{m.group(2)}" for m in dates]
    return periods
