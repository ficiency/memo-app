"""Paint a memo from the database as a native PowerPoint file.

A firm with a template: the template's own masters and layouts are used. Each memo slide is added on its
layout, placeholders are filled by number (text, native tables in the firm's table style, native charts
with their data); the layout's fixed shapes come from the layout itself. A firm without a template: a
presentation of the spec's page size, with the spec's master and the slide's elements drawn as shapes.

Every shape is named memo:<element id>@<revision> and every slide memo-slide:<slide id>, so an edited file
can be matched back to the memo. Nothing here knows any firm.

Usage (from backend/):
    python -m app.services.painters.pptx <memo id> <out.pptx>
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Inches, Pt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import REPO
from app.db import spec as S
from app.db.memo import (ChartPoint, ChartSeries, Element, ElementChart, ElementParagraph, ElementTable, Memo,
                         Segment, Slide, TableCell, TableColumn, TableRow)
from app.db.session import SessionLocal

SLOT = "#8A8A8A"                       # marked slots (model, person, conditional): grey italic, never final text
NO_DATA = "#B26A00"                    # a chip without a record value
ALIGN = {"left": PP_ALIGN.LEFT, "center": PP_ALIGN.CENTER, "right": PP_ALIGN.RIGHT, "justify": PP_ALIGN.JUSTIFY}
ANCHOR = {"top": MSO_ANCHOR.TOP, "middle": MSO_ANCHOR.MIDDLE, "bottom": MSO_ANCHOR.BOTTOM}
CHART = {"column": XL_CHART_TYPE.COLUMN_CLUSTERED, "stacked_column": XL_CHART_TYPE.COLUMN_STACKED,
         "bar": XL_CHART_TYPE.BAR_CLUSTERED, "stacked_bar": XL_CHART_TYPE.BAR_STACKED, "line": XL_CHART_TYPE.LINE_MARKERS,
         "area": XL_CHART_TYPE.AREA, "pie": XL_CHART_TYPE.PIE, "scatter": XL_CHART_TYPE.XY_SCATTER,
         "combo": XL_CHART_TYPE.COLUMN_CLUSTERED, "other": XL_CHART_TYPE.COLUMN_CLUSTERED}


def group(rows, key) -> dict:
    out: dict = {}
    for r in rows:
        out.setdefault(getattr(r, key), []).append(r)
    return out


def materials(firm_name: str) -> dict[str, list[Path]]:
    manifest = REPO / "backend" / "onboarding_firms" / f"{firm_name}.json"
    out: dict[str, list[Path]] = {}
    if manifest.exists():
        for m in json.loads(manifest.read_text(encoding="utf-8"))["materials"]:
            out.setdefault(m["kind"], []).append(REPO / m["file"])
    return out


class Painter:
    def __init__(self, session: Session, memo_id):
        self.s = session
        self.memo = session.get(Memo, memo_id)
        self.firm = session.get(S.Firm, self.memo.firm_id)
        self.version = session.get(S.SpecVersion, self.memo.spec_version_id)
        self.palette = {p.name.lower(): p.hex for p in session.scalars(
            select(S.PaletteColor).where(S.PaletteColor.spec_version_id == self.version.id))}
        self.table_style = session.scalar(select(S.TableStyle).where(S.TableStyle.spec_version_id == self.version.id))
        roles = session.scalars(select(S.TextRole).where(S.TextRole.spec_version_id == self.version.id)).all()
        table_role = next((r for r in roles if "table" in r.name), None)
        self.table_font = (table_role.font if table_role else None), (table_role.size_pt if table_role else 10)
        self.files = materials(self.firm.name)
        self.slides = session.scalars(select(Slide).where(Slide.memo_id == self.memo.id, Slide.deleted_at.is_(None))
                                      .order_by(Slide.position)).all()
        # the whole memo, read once: a remote database answers each round trip slowly
        q = session.scalars
        elements = q(select(Element).where(Element.slide_id.in_([x.id for x in self.slides]), Element.deleted_at.is_(None))).all()
        self.elements_of = group(elements, "slide_id")
        tables = q(select(ElementTable).where(ElementTable.element_id.in_([e.id for e in elements]))).all()
        self.table_of = {t.element_id: t for t in tables}
        tids = [t.id for t in tables]
        self.cols_of = group(q(select(TableColumn).where(TableColumn.element_table_id.in_(tids))).all(), "element_table_id")
        self.rows_of = group(q(select(TableRow).where(TableRow.element_table_id.in_(tids))).all(), "element_table_id")
        cells = q(select(TableCell).where(TableCell.element_table_id.in_(tids))).all()
        self.cells_of = group(cells, "element_table_id")
        paragraphs = list(q(select(ElementParagraph).where(ElementParagraph.element_id.in_([e.id for e in elements])))) + \
            list(q(select(ElementParagraph).where(ElementParagraph.table_cell_id.in_([c.id for c in cells]))))
        self.paras_of_element = group([p for p in paragraphs if p.element_id], "element_id")
        self.paras_of_cell = group([p for p in paragraphs if p.table_cell_id], "table_cell_id")
        self.segments_of = group(q(select(Segment).where(Segment.paragraph_id.in_([p.id for p in paragraphs]))).all(), "paragraph_id")
        charts = q(select(ElementChart).where(ElementChart.element_id.in_([e.id for e in elements]))).all()
        self.chart_of = {c.element_id: c for c in charts}
        series = q(select(ChartSeries).where(ChartSeries.element_chart_id.in_([c.id for c in charts]))).all()
        self.series_of = group(series, "element_chart_id")
        self.points_of = group(q(select(ChartPoint).where(ChartPoint.chart_series_id.in_([x.id for x in series]))).all(), "chart_series_id")
        master = session.scalar(select(S.Layout).where(S.Layout.spec_version_id == self.version.id, S.Layout.is_master))
        self.master_elements = q(select(Element).where(Element.layout_id == master.id)).all() if master else []
        self.master_paras = group(q(select(ElementParagraph).where(ElementParagraph.element_id.in_([e.id for e in self.master_elements]))).all(), "element_id")
        for p in [p for ps in self.master_paras.values() for p in ps]:
            self.paras_of_element.setdefault(p.element_id, []).append(p)
        for seg in q(select(Segment).where(Segment.paragraph_id.in_([p.id for ps in self.master_paras.values() for p in ps]))):
            self.segments_of.setdefault(seg.paragraph_id, []).append(seg)
        self.master_recipe = group(q(select(S.RecipeSegment).where(S.RecipeSegment.element_id.in_([e.id for e in self.master_elements]))).all(), "element_id")
        self.concept_names = {c.id: c.name for c in q(select(S.Concept).where(S.Concept.spec_version_id == self.version.id))}
        self.stage_name = session.get(S.Stage, self.memo.stage_id).name if self.memo.stage_id else ""
        self.layout_rows = {l.id: l for l in q(select(S.Layout).where(S.Layout.spec_version_id == self.version.id))}

    # --- helpers --------------------------------------------------------------------------------
    def rgb(self, ref: str | None):
        if not ref:
            return None
        h = ref if ref.startswith("#") else self.palette.get(ref.lower())
        return RGBColor.from_string(h.lstrip("#").upper()) if h else None

    def paragraphs(self, element_id=None, cell_id=None) -> list[tuple[ElementParagraph, list[Segment]]]:
        paras = self.paras_of_element.get(element_id, []) if element_id else self.paras_of_cell.get(cell_id, [])
        return [(p, sorted(self.segments_of.get(p.id, []), key=lambda x: x.position))
                for p in sorted(paras, key=lambda p: p.position)]

    def write_text(self, text_frame, paras, keep_layout_style: bool, page: int) -> None:
        """Paragraphs and runs; placeholders keep their layout's look unless a paragraph overrides it."""
        text_frame.clear()
        for i, (p, segs) in enumerate(paras):
            para = text_frame.paragraphs[0] if i == 0 else text_frame.add_paragraph()
            para.level = max(0, min(p.level or 0, 8))
            if p.align in ALIGN:
                para.alignment = ALIGN[p.align]
            for seg in segs:
                run = para.add_run()
                text = seg.text or ""
                run.text = text.upper() if p.caps and seg.kind == "text" else text
                f = run.font
                if p.size_pt and not keep_layout_style or (p.size_pt and keep_layout_style and len(paras) > 1):
                    f.size = Pt(p.size_pt)
                if p.font and not keep_layout_style:
                    f.name = p.font
                if p.bold is not None and not keep_layout_style:
                    f.bold = p.bold
                colour = self.rgb(p.color_ref)
                if seg.kind in ("model_draft", "person_slot", "conditional"):
                    f.italic, colour = True, RGBColor.from_string(SLOT.lstrip("#"))
                elif seg.kind == "chip" and text.startswith("[no data"):
                    colour = RGBColor.from_string(NO_DATA.lstrip("#"))
                if colour is not None and (not keep_layout_style or seg.kind != "text" or len(paras) > 1):
                    f.color.rgb = colour

    # --- tables and charts ------------------------------------------------------------------------
    def fill_table(self, graphic_frame, table: ElementTable) -> None:
        tbl = graphic_frame.table
        cols = sorted(self.cols_of.get(table.id, []), key=lambda c: c.position)
        rows = sorted(self.rows_of.get(table.id, []), key=lambda r: r.position)
        for i, c in enumerate(cols[: len(tbl.columns)]):
            if c.width_in:
                tbl.columns[i].width = Inches(c.width_in)
        for i, r in enumerate(rows[: len(tbl.rows)]):
            if r.height_in:
                tbl.rows[i].height = Inches(r.height_in)       # each row its own height (no stretching)
        st = self.table_style
        cells = {(c.row, c.col): c for c in self.cells_of.get(table.id, [])}
        font, size = self.table_font
        for r in range(len(tbl.rows)):
            kind = rows[r].kind if r < len(rows) else "data"
            for c in range(len(tbl.columns)):
                cell = tbl.cell(r, c)
                data = cells.get((r, c))
                paras = self.paragraphs(cell_id=data.id) if data else []
                text = "".join(s.text or "" for _, segs in paras for s in segs)
                cell.text = text
                header = kind == "header"
                fill = (data.fill_ref if data and data.fill_ref else None) or \
                    ((st.header_fill_ref if header else None) if st else None) or \
                    ((st.banding_ref if st and kind == "data" and r % 2 == 0 else None))
                if self.rgb(fill) is not None:
                    cell.fill.solid()
                    cell.fill.fore_color.rgb = self.rgb(fill)
                else:
                    cell.fill.background()
                for p in cell.text_frame.paragraphs:
                    if c > 0 and not header and st and st.number_align in ALIGN:
                        p.alignment = ALIGN[st.number_align]
                    for run in p.runs:
                        run.font.size = Pt(size or 10)
                        if font:
                            run.font.name = font
                        run.font.bold = bool((header and st and st.header_bold) or kind in ("group", "total"))
                        colour = self.rgb((st.header_text_ref if header else st.body_text_ref) if st else None)
                        if colour is not None:
                            run.font.color.rgb = colour

    def chart_data(self, chart: ElementChart):
        series = sorted(self.series_of.get(chart.id, []), key=lambda x: x.position)
        points = {x.id: sorted(self.points_of.get(x.id, []), key=lambda p: p.position) for x in series}
        categories = next((([p.category for p in pts]) for pts in points.values() if pts), [])
        data = CategoryChartData()
        data.categories = categories or ["-"]
        for x in series:
            values = [p.value for p in points[x.id]][: len(categories)] or [0]
            data.add_series(x.name or x.concept_name or "Series", values + [None] * (len(categories) - len(values)))
        return data, series

    def style_chart(self, chart, series) -> None:
        for plot in chart.plots:
            for i, ser in enumerate(plot.series):
                colour = self.rgb(series[i].color_ref) if i < len(series) else None
                if colour is not None:
                    ser.format.fill.solid()
                    ser.format.fill.fore_color.rgb = colour
                    ser.format.line.color.rgb = colour
        chart.has_legend = len(series) > 1
        if chart.has_legend:
            chart.legend.position = XL_LEGEND_POSITION.BOTTOM
            chart.legend.include_in_layout = False

    # --- run -----------------------------------------------------------------------------------
    def paint(self, out: Path) -> dict:
        started = time.perf_counter()
        template = (self.files.get("template") or [None])[0]
        prs = Presentation(str(template)) if template else Presentation()
        if template:
            ids = prs.slides._sldIdLst                          # drop the template's own sample slides
            for sid in list(ids):
                prs.part.drop_rel(sid.rId)
                ids.remove(sid)
            layouts = {l.name: l for m in prs.slide_masters for l in m.slide_layouts}
        else:
            prs.slide_width, prs.slide_height = Inches(self.version.page_w_in), Inches(self.version.page_h_in)
            layouts = {}
        blank = prs.slide_layouts[6] if not template else None
        master = bool(self.master_elements)
        counts = {"slides": 0, "shapes": 0, "tables": 0, "charts": 0, "unfilled_placeholders": 0}
        for slide_row in self.slides:
            layout = self.layout_rows.get(slide_row.layout_id)
            pptx_layout = layouts.get(layout.name) if layout and template else None
            slide = prs.slides.add_slide(pptx_layout or blank or prs.slide_layouts[0])
            slide._element.cSld.set("name", f"memo-slide:{slide_row.id}")
            counts["slides"] += 1
            page = slide_row.position + 1
            if slide_row.background_ref and self.rgb(slide_row.background_ref) is not None and not pptx_layout:
                slide.background.fill.solid()
                slide.background.fill.fore_color.rgb = self.rgb(slide_row.background_ref)
            if not template and master and (layout is None or layout.shows_master):
                for m_el in self.master_elements:
                    self.draw(slide, m_el, page, master_box=True)
            placeholders = {ph.placeholder_format.idx: ph for ph in slide.placeholders} if pptx_layout else {}
            used = set()
            for el in sorted(self.elements_of.get(slide_row.id, []), key=lambda e: (e.z_index or 0, e.y_in or 0, e.x_in or 0)):
                if pptx_layout and el.placeholder_idx is not None and el.placeholder_idx in placeholders:
                    self.fill_placeholder(slide, placeholders[el.placeholder_idx], el, page, counts)
                    used.add(el.placeholder_idx)
                elif pptx_layout and el.origin_element_id is not None:
                    continue                                    # a fixed shape of the layout: the layout draws it
                else:
                    self.draw(slide, el, page)
                    counts["shapes"] += 1
            for idx, ph in placeholders.items():                # empty placeholders would show their prompt
                if idx not in used:
                    ph._element.getparent().remove(ph._element)
                    counts["unfilled_placeholders"] += 1
        out.parent.mkdir(parents=True, exist_ok=True)
        prs.save(str(out))
        return {"file": str(out), "seconds": round(time.perf_counter() - started, 2), **counts}

    def fill_placeholder(self, slide, ph, el: Element, page: int, counts: dict) -> None:
        if el.type == "table":
            table = self.table_of.get(el.id)
            if table:
                n_rows = len(self.rows_of.get(table.id, []))
                n_cols = len(self.cols_of.get(table.id, []))
                frame = ph.insert_table(max(n_rows, 1), max(n_cols, 1))
                self.fill_table(frame, table)
                frame.name = f"memo:{el.id}@{el.revision}"
                counts["tables"] += 1
            return
        if el.type == "chart":
            chart_row = self.chart_of.get(el.id)
            if chart_row:
                data, series = self.chart_data(chart_row)
                frame = ph.insert_chart(CHART.get(chart_row.chart_type, XL_CHART_TYPE.COLUMN_CLUSTERED), data)
                self.style_chart(frame.chart, series)
                frame.name = f"memo:{el.id}@{el.revision}"
                counts["charts"] += 1
            return
        if not ph.has_text_frame:
            return                                              # picture placeholders stay empty (no logo fallback)
        paras = self.paragraphs(element_id=el.id)
        if paras:
            self.write_text(ph.text_frame, paras, keep_layout_style=True, page=page)
        ph.name = f"memo:{el.id}@{el.revision}"

    def draw(self, slide, el: Element, page: int, master_box: bool = False) -> None:
        x, y = Inches(el.x_in or 0), Inches(el.y_in or 0)
        w, h = Inches(max(el.w_in or 0, 0.01)), Inches(max(el.h_in or 0, 0.01))
        if el.type == "line":
            shape = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, x, y, x + w, y + Inches(el.h_in or 0))
            if self.rgb(el.line_ref) is not None:
                shape.line.color.rgb = self.rgb(el.line_ref)
            shape.line.width = Pt(el.line_width_pt or 0.75)
        elif el.type == "image":
            logos = self.files.get("logo") or []
            if not (logos and (el.role or "").find("logo") >= 0):
                return
            shape = slide.shapes.add_picture(str(logos[0]), x, y, height=h)
        elif el.type == "table":
            table = self.table_of.get(el.id)
            if not table:
                return
            n_rows = len(self.rows_of.get(table.id, [])) or 1
            n_cols = len(self.cols_of.get(table.id, [])) or 1
            shape = slide.shapes.add_table(n_rows, n_cols, x, y, w, h)
            self.fill_table(shape, table)
        elif el.type == "chart":
            chart_row = self.chart_of.get(el.id)
            if not chart_row:
                return
            data, series = self.chart_data(chart_row)
            shape = slide.shapes.add_chart(CHART.get(chart_row.chart_type, XL_CHART_TYPE.COLUMN_CLUSTERED), x, y, w, h, data)
            self.style_chart(shape.chart, series)
        else:
            shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, x, y, w, h) if el.type == "shape" else slide.shapes.add_textbox(x, y, w, h)
            if self.rgb(el.fill_ref) is not None:
                shape.fill.solid()
                shape.fill.fore_color.rgb = self.rgb(el.fill_ref)
            elif el.type == "shape":
                shape.fill.background()
            if self.rgb(el.line_ref) is not None:
                shape.line.color.rgb = self.rgb(el.line_ref)  # any element with a line draws it (text boxes too)
                shape.line.width = Pt(el.line_width_pt or 0.75)
            elif el.type == "shape":
                shape.line.fill.background()
            if el.type == "shape":
                shape.shadow.inherit = False                    # no default shadow
            paras = self.paragraphs(element_id=el.id)
            if master_box and not paras:
                paras = self.master_text(el, page)
            if paras:
                tf = shape.text_frame
                tf.word_wrap = True
                for m in ("margin_left", "margin_right", "margin_top", "margin_bottom"):
                    setattr(tf, m, Emu(0))
                anchor = paras[0][0].anchor if paras and paras[0][0] is not None else None
                if anchor in ANCHOR:
                    tf.vertical_anchor = ANCHOR[anchor]
                self.write_text(tf, paras, keep_layout_style=False, page=page)
        shape.name = f"memo:{el.id}@{el.revision}"

    def master_text(self, el: Element, page: int):
        """Text of a master box from its recipe: fixed words and memo data (stage, date, page number)."""
        recipe = sorted(self.master_recipe.get(el.id, []), key=lambda r: (r.paragraph or 0, r.position))
        if not recipe:
            return []
        stage = self.stage_name
        concepts = self.concept_names
        style = ElementParagraph(position=0, level=0)
        segs = []
        for r in recipe:
            name = (concepts.get(r.concept_id) or "").lower()
            text = r.text or ""
            if r.kind == "memo_data":
                text = stage if "stage" in name else self.memo.memo_date or "" if "date" in name else \
                    str(page) if any(w in name for w in ("page", "number")) else ""
            segs.append(Segment(position=len(segs), kind="text", text=text))
        return [(style, segs)]


def paint(memo_id, out: Path) -> dict:
    with SessionLocal() as session:
        return Painter(session, memo_id).paint(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("memo_id")
    parser.add_argument("out", type=Path)
    a = parser.parse_args()
    r = paint(a.memo_id, a.out)
    sys.stdout.write(", ".join(f"{k} {v}" for k, v in r.items()) + "\n")


if __name__ == "__main__":
    main()
