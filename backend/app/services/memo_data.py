"""A memo read once from the database, for everything that shows it: the PPTX painter, the canvas (through the
API) and the door's answers. One element shape for all of them, so the screen and the file read the same data.

The canvas paints what this gives: frames in inches, styles as stored (role names and palette names, which
the canvas resolves with the spec it receives), text by paragraph with chips and slots marked, tables by
cell, charts by series. Nothing here knows any firm.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, Uuid, func, inspect, literal_column, or_, select
from sqlalchemy.orm import Session

from app.db import spec as S
from app.db.memo import (ChartPoint, ChartSeries, Element, ElementChart, ElementParagraph, ElementTable, Memo,
                         Segment, Slide, TableCell, TableColumn, TableRow)
from app.services.formats import format_date

FRAME = ("x_in", "y_in", "w_in", "h_in", "rotation_deg", "z_index")
PARAGRAPH_STYLE = ("font", "size_pt", "bold", "italic", "caps", "color_ref", "align", "anchor", "line_spacing",
                   "space_before_pt", "space_after_pt", "bullet_char", "bullet_color_ref", "indent_in")


def group(rows, key) -> dict:
    out: dict = {}
    for r in rows:
        out.setdefault(getattr(r, key), []).append(r)
    return out


# --- several reads in one round trip -----------------------------------------------------------------------
def fetch(session: Session, queries: dict) -> dict[str, list]:
    """Run several selects as one statement: Postgres returns each one's rows as JSON, and they come back as
    model objects (read-only copies, not in the session). A remote database answers each round trip slowly,
    so a whole memo is read in one. queries: {name: (Model, select(Model)...)}."""
    columns = []
    for name, (_, stmt) in queries.items():
        sub = stmt.subquery(name)
        rows = select(func.coalesce(func.json_agg(literal_column(f'"{name}"')), literal_column("'[]'::json"))) \
            .select_from(sub).scalar_subquery()
        columns.append(rows.label(name))
    row = session.execute(select(*columns)).one()
    return {name: [_model(model, r) for r in (getattr(row, name) or [])] for name, (model, _) in queries.items()}


def _model(model, data: dict):
    values = {}
    for prop in inspect(model).column_attrs:
        column = prop.columns[0]
        v = data.get(column.name)
        if v is not None:
            if isinstance(column.type, Uuid):
                v = uuid.UUID(v)
            elif isinstance(column.type, DateTime):
                v = datetime.fromisoformat(v)
            elif isinstance(column.type, Date):
                v = date.fromisoformat(v)
            elif isinstance(column.type, Float):             # JSON writes 3.0 as 3
                v = float(v)
        values[prop.key] = v
    return model(**values)


def element_queries(element_ids) -> dict:
    """What an element is made of, for the elements a subquery names: tables, cells, text, charts."""
    tables = select(ElementTable.id).where(ElementTable.element_id.in_(element_ids))
    cells = select(TableCell.id).where(TableCell.element_table_id.in_(tables))
    in_text = or_(ElementParagraph.element_id.in_(element_ids), ElementParagraph.table_cell_id.in_(cells))
    charts = select(ElementChart.id).where(ElementChart.element_id.in_(element_ids))
    series = select(ChartSeries.id).where(ChartSeries.element_chart_id.in_(charts))
    return {
        "elements": (Element, select(Element).where(Element.id.in_(element_ids))),
        "tables": (ElementTable, select(ElementTable).where(ElementTable.element_id.in_(element_ids))),
        "columns": (TableColumn, select(TableColumn).where(TableColumn.element_table_id.in_(tables))),
        "rows": (TableRow, select(TableRow).where(TableRow.element_table_id.in_(tables))),
        "cells": (TableCell, select(TableCell).where(TableCell.element_table_id.in_(tables))),
        "paragraphs": (ElementParagraph, select(ElementParagraph).where(in_text)),
        "segments": (Segment, select(Segment).where(Segment.paragraph_id.in_(select(ElementParagraph.id).where(in_text)))),
        "charts": (ElementChart, select(ElementChart).where(ElementChart.element_id.in_(element_ids))),
        "series": (ChartSeries, select(ChartSeries).where(ChartSeries.element_chart_id.in_(charts))),
        "points": (ChartPoint, select(ChartPoint).where(ChartPoint.chart_series_id.in_(series))),
    }


class ElementData:
    """Elements (of a memo's slides or of a spec's layouts) with their text, tables and charts, read in one
    round trip with whatever else the caller needs (extra queries, kept in self.rows)."""

    def __init__(self, session: Session, element_ids, extra: dict | None = None):
        self.s = session
        self.rows = fetch(session, {**element_queries(element_ids), **(extra or {})})
        r = self.rows
        self.elements = r["elements"]
        self.elements_of = group(self.elements, "slide_id")
        self.table_of = {t.element_id: t for t in r["tables"]}
        self.cols_of = group(r["columns"], "element_table_id")
        self.rows_of = group(r["rows"], "element_table_id")
        self.cells_of = group(r["cells"], "element_table_id")
        self.paras_of_element = group([p for p in r["paragraphs"] if p.element_id], "element_id")
        self.paras_of_cell = group([p for p in r["paragraphs"] if p.table_cell_id], "table_cell_id")
        self.segments_of = group(r["segments"], "paragraph_id")
        self.chart_of = {c.element_id: c for c in r["charts"]}
        self.series_of = group(r["series"], "element_chart_id")
        self.points_of = group(r["points"], "chart_series_id")
        self.master_elements: list = []

    # --- reading -------------------------------------------------------------------------------
    def paragraphs(self, element_id=None, cell_id=None) -> list[tuple[ElementParagraph, list[Segment]]]:
        paras = self.paras_of_element.get(element_id, []) if element_id else self.paras_of_cell.get(cell_id, [])
        return [(p, sorted(self.segments_of.get(p.id, []), key=lambda x: x.position))
                for p in sorted(paras, key=lambda p: p.position)]

    def master_text(self, el: Element, page: int | None):
        return []

    # --- the shape clients receive --------------------------------------------------------------
    @staticmethod
    def paragraphs_json(paras) -> list[dict]:
        return [{"level": p.level or 0, "role": p.role, "style": {k: getattr(p, k) for k in PARAGRAPH_STYLE},
                 "segments": [{"kind": s.kind, "text": s.text or "", "concept": s.concept_name, "path": s.record_path}
                              for s in segs]}
                for p, segs in paras]

    def element_json(self, el: Element, master_box: bool = False) -> dict:
        paras = self.paragraphs(element_id=el.id)
        if master_box and not paras:
            paras = self.master_text(el, None)
        out = {
            "id": str(el.id), "slide_id": str(el.slide_id) if el.slide_id else None, "type": el.type,
            "shape_kind": el.shape_kind, "name": el.name, "role": el.role, "fill_kind": el.fill_kind,
            "firm_lock": "fixed" if master_box else el.firm_lock, "master": master_box,
            "user_lock": str(el.user_lock_by) if el.user_lock_by else None, "description": el.description,
            "placeholder": el.placeholder_idx is not None, "fill": el.fill_ref, "line": el.line_ref,
            "line_width_pt": el.line_width_pt, "revision": el.revision, "deleted": el.deleted_at is not None,
            "frame": {k: getattr(el, k) for k in FRAME}, "paragraphs": self.paragraphs_json(paras),
            "table": None, "chart": None,
        }
        table = self.table_of.get(el.id)
        if table is not None:
            out["table"] = {
                "columns": [c.width_in for c in sorted(self.cols_of.get(table.id, []), key=lambda c: c.position)],
                "rows": [{"kind": r.kind, "height_in": r.height_in} for r in sorted(self.rows_of.get(table.id, []), key=lambda r: r.position)],
                "cells": [{"row": c.row, "col": c.col, "row_span": c.row_span, "col_span": c.col_span, "kind": c.kind,
                           "fill": c.fill_ref, "paragraphs": self.paragraphs_json(self.paragraphs(cell_id=c.id))}
                          for c in sorted(self.cells_of.get(table.id, []), key=lambda c: (c.row, c.col))],
            }
        chart = self.chart_of.get(el.id)
        if chart is not None:
            out["chart"] = {"type": chart.chart_type, "series": [
                {"name": x.name, "concept": x.concept_name, "path": x.record_path, "color": x.color_ref,
                 "points": [{"category": p.category, "value": p.value, "forecast": p.is_forecast}
                            for p in sorted(self.points_of.get(x.id, []), key=lambda p: p.position)]}
                for x in sorted(self.series_of.get(chart.id, []), key=lambda x: x.position)]}
        return out


class MemoData(ElementData):
    """The whole memo (or only some of its elements) and, for the whole memo, the spec's master: one round trip."""

    def __init__(self, session: Session, memo_id, element_ids: list | None = None):
        memo_id = uuid.UUID(str(memo_id))
        version = select(Memo.spec_version_id).where(Memo.id == memo_id).scalar_subquery()
        slides = select(Slide.id).where(Slide.memo_id == memo_id, Slide.deleted_at.is_(None))
        ids = select(Element.id).where(Element.slide_id.in_(slides), Element.deleted_at.is_(None)) if element_ids is None \
            else select(Element.id).where(Element.id.in_(element_ids))
        extra = {"memo": (Memo, select(Memo).where(Memo.id == memo_id)),
                 "version": (S.SpecVersion, select(S.SpecVersion).where(S.SpecVersion.id == version)),
                 "slides": (Slide, select(Slide).where(Slide.memo_id == memo_id, Slide.deleted_at.is_(None)))}
        whole = element_ids is None
        if whole:
            master = select(Element.id).where(Element.layout_id.in_(
                select(S.Layout.id).where(S.Layout.spec_version_id == version, S.Layout.is_master)))
            ids = ids.union(master)                              # the master's text is read with the memo's
            extra |= {
                "layouts": (S.Layout, select(S.Layout).where(S.Layout.spec_version_id == version)),
                "recipe": (S.RecipeSegment, select(S.RecipeSegment).where(S.RecipeSegment.element_id.in_(master))),
                "concepts": (S.Concept, select(S.Concept).where(S.Concept.spec_version_id == version)),
                "stage": (S.Stage, select(S.Stage).where(
                    S.Stage.id == select(Memo.stage_id).where(Memo.id == memo_id).scalar_subquery())),
                "dates": (S.Format, select(S.Format).where(S.Format.spec_version_id == version, S.Format.item == "date")),
            }
        super().__init__(session, ids, extra)
        r = self.rows
        self.memo, self.version = r["memo"][0], r["version"][0]
        self.slides = sorted(r["slides"], key=lambda x: x.position)
        if whole:
            master_ids = {l.id for l in r["layouts"] if l.is_master}
            self.master_elements = [e for e in self.elements if e.layout_id in master_ids]
            self.elements = [e for e in self.elements if e.slide_id is not None]
            self.elements_of.pop(None, None)
            self.master_recipe = group(r["recipe"], "element_id")
            self.concept_names = {c.id: c.name for c in r["concepts"]}
            self.stage_name = r["stage"][0].name if r["stage"] else ""
            self.layout_rows = {l.id: l for l in r["layouts"]}
            dates = [f for f in r["dates"] if f.pattern]
            general = [f for f in dates if f.context == "any"] or dates
            self.date_format = {"pattern": general[0].pattern} if general else None

    def shows_master(self, slide: Slide) -> bool:
        layout = self.layout_rows.get(slide.layout_id)
        return bool(self.master_elements) and (layout is None or layout.shows_master)

    def master_text(self, el: Element, page: int | None):
        """Text of a master box from its recipe: fixed words and memo data (stage, date, page number). With no
        page, the page number is left as a marked segment for the screen to fill per slide."""
        recipe = sorted(self.master_recipe.get(el.id, []), key=lambda r: (r.paragraph or 0, r.position))
        if not recipe:
            return []
        style = ElementParagraph(position=0, level=0)
        segs = []
        for r in recipe:
            name = (self.concept_names.get(r.concept_id) or "").lower()
            text, kind = r.text or "", "text"
            if r.kind == "memo_data":
                if "stage" in name:
                    text = self.stage_name
                elif "date" in name:                            # the firm's date format, not ISO (18)
                    text = format_date(self.memo.memo_date, self.date_format) if self.memo.memo_date and self.date_format \
                        else self.memo.memo_date or ""
                elif any(w in name for w in ("page", "number")):
                    text, kind = (str(page), "text") if page is not None else ("", "page_number")
                else:
                    text = ""
            segs.append(Segment(position=len(segs), kind=kind, text=text))
        return [(style, segs)]

    def slide_json(self, slide: Slide) -> dict:
        layout = self.layout_rows.get(slide.layout_id)
        elements = sorted(self.elements_of.get(slide.id, []), key=lambda e: (e.z_index or 0, e.y_in or 0, e.x_in or 0))
        return {"id": str(slide.id), "position": slide.position, "kind": slide.kind, "revision": slide.revision,
                "section_id": str(slide.section_id) if slide.section_id else None,
                "layout": layout.name if layout else None, "background": slide.background_ref,
                "shows_master": self.shows_master(slide), "elements": [self.element_json(e) for e in elements]}
