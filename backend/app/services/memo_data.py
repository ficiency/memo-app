"""A memo read once from the database, for everything that shows it: the PPTX painter, the canvas (through the
API) and the door's answers. One element shape for all of them, so the screen and the file read the same data.

The canvas paints what this gives: frames in inches, styles as stored (role names and palette names, which
the canvas resolves with the spec it receives), text by paragraph with chips and slots marked, tables by
cell, charts by series. Nothing here knows any firm.
"""
from __future__ import annotations

from sqlalchemy import select
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


class ElementData:
    """Elements (of a memo's slides or of a spec's layouts) with their text, tables and charts, read in a
    handful of queries: a remote database answers each round trip slowly."""

    def __init__(self, session: Session, elements: list):
        self.s = session
        q = session.scalars
        self.elements = elements
        self.elements_of = group(elements, "slide_id")
        tables = q(select(ElementTable).where(ElementTable.element_id.in_([e.id for e in elements]))).all()
        self.table_of = {t.element_id: t for t in tables}
        tids = [t.id for t in tables]
        self.cols_of = group(q(select(TableColumn).where(TableColumn.element_table_id.in_(tids))).all(), "element_table_id")
        self.rows_of = group(q(select(TableRow).where(TableRow.element_table_id.in_(tids))).all(), "element_table_id")
        cells = q(select(TableCell).where(TableCell.element_table_id.in_(tids))).all()
        self.cells_of = group(cells, "element_table_id")
        paragraphs = list(q(select(ElementParagraph).where(ElementParagraph.element_id.in_([e.id for e in elements])))) +             list(q(select(ElementParagraph).where(ElementParagraph.table_cell_id.in_([c.id for c in cells]))))
        self.paras_of_element = group([p for p in paragraphs if p.element_id], "element_id")
        self.paras_of_cell = group([p for p in paragraphs if p.table_cell_id], "table_cell_id")
        self.segments_of = group(q(select(Segment).where(Segment.paragraph_id.in_([p.id for p in paragraphs]))).all(), "paragraph_id")
        charts = q(select(ElementChart).where(ElementChart.element_id.in_([e.id for e in elements]))).all()
        self.chart_of = {c.element_id: c for c in charts}
        series = q(select(ChartSeries).where(ChartSeries.element_chart_id.in_([c.id for c in charts]))).all()
        self.series_of = group(series, "element_chart_id")
        self.points_of = group(q(select(ChartPoint).where(ChartPoint.chart_series_id.in_([x.id for x in series]))).all(), "chart_series_id")
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
    """The whole memo (or only some of its elements) and, for the whole memo, the spec's master."""

    def __init__(self, session: Session, memo_id, element_ids: list | None = None):
        q = session.scalars
        self.memo = session.get(Memo, memo_id)
        self.version = session.get(S.SpecVersion, self.memo.spec_version_id)
        self.slides = q(select(Slide).where(Slide.memo_id == self.memo.id, Slide.deleted_at.is_(None))
                        .order_by(Slide.position)).all()
        if element_ids is None:
            elements = q(select(Element).where(Element.slide_id.in_([x.id for x in self.slides]),
                                               Element.deleted_at.is_(None))).all()
        else:
            elements = q(select(Element).where(Element.id.in_(element_ids))).all()
        super().__init__(session, elements)
        if element_ids is None:
            self.load_master()

    def load_master(self) -> None:
        """The spec's master boxes (logo, rules, footer) and what their text is made of."""
        q = self.s.scalars
        v = self.version.id
        master = self.s.scalar(select(S.Layout).where(S.Layout.spec_version_id == v, S.Layout.is_master))
        self.master_elements = q(select(Element).where(Element.layout_id == master.id)).all() if master else []
        master_paras = q(select(ElementParagraph).where(ElementParagraph.element_id.in_([e.id for e in self.master_elements]))).all()
        for p in master_paras:
            self.paras_of_element.setdefault(p.element_id, []).append(p)
        for seg in q(select(Segment).where(Segment.paragraph_id.in_([p.id for p in master_paras]))):
            self.segments_of.setdefault(seg.paragraph_id, []).append(seg)
        self.master_recipe = group(q(select(S.RecipeSegment).where(S.RecipeSegment.element_id.in_([e.id for e in self.master_elements]))).all(), "element_id")
        self.concept_names = {c.id: c.name for c in q(select(S.Concept).where(S.Concept.spec_version_id == v))}
        self.stage_name = self.s.get(S.Stage, self.memo.stage_id).name if self.memo.stage_id else ""
        self.layout_rows = {l.id: l for l in q(select(S.Layout).where(S.Layout.spec_version_id == v))}
        dates = [f for f in q(select(S.Format).where(S.Format.spec_version_id == v, S.Format.item == "date")) if f.pattern]
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
