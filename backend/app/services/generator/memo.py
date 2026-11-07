"""Generate a new memo for a firm, a stage and the latest deal record, through operations.

Reads only data: the firm's latest spec version (sections per stage, layouts, recipes, table and chart
templates, formats), its binding and the latest record revision. Every slide and element is added as an
operation by the generator, so the memo's history starts with its creation. Nothing here knows any firm.

Simple on purpose (first version): one format per kind of value and context; conditional text, text the
model writes and text a person writes are left as marked slots; optional sections are left out; when a
section has several layouts, the first is used. What this gets wrong goes to the "where it breaks" list.

Usage (from backend/):
    python -m app.services.generator.memo <firm> <stage> [--date YYYY-MM-DD] [--team "A (Partner); B"]
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import spec as S
from app.db.history import Operation
from app.db.memo import (ChartPoint, ChartSeries, Element, ElementChart, ElementParagraph, ElementTable, Memo,
                         Segment, Slide, TableCell, TableColumn, TableRow)
from app.db.record import RecordRevision
from app.db.session import SessionLocal
from app.services.formats import format_date, format_number
from app.services.records import RELATIVE, Record

ITEM = {"money": "money", "percent": "percent", "multiple": "multiple", "count": "count", "ratio": "multiple",
        "date": "date", "per_share": "per_share", "currency": "currency"}
STYLE = ("font", "size_pt", "bold", "italic", "caps", "color_ref", "align", "anchor", "line_spacing",
         "space_before_pt", "space_after_pt", "bullet_char", "bullet_color_ref", "indent_in")
FRAME = ("x_in", "y_in", "w_in", "h_in", "rotation_deg", "z_index")
COPY = FRAME + ("name", "placeholder_idx", "type", "shape_kind", "role", "fill_kind", "firm_lock", "description",
                "fill_ref", "line_ref", "line_width_pt")


class Generator:
    def __init__(self, session: Session, firm_name: str, stage_name: str, memo_date: str | None, team: str | None):
        self.s = session
        self.firm = session.scalar(select(S.Firm).where(S.Firm.name == firm_name))
        self.spec = session.scalar(select(S.Spec).where(S.Spec.firm_id == self.firm.id))
        self.version = session.scalar(select(S.SpecVersion).where(S.SpecVersion.spec_id == self.spec.id)
                                      .order_by(S.SpecVersion.number.desc()))
        self.revision = session.scalar(select(RecordRevision).where(RecordRevision.firm_id == self.firm.id)
                                       .order_by(RecordRevision.number.desc()))
        self.record = Record(session, self.revision.id)
        self.stage = session.scalar(select(S.Stage).where(S.Stage.spec_version_id == self.version.id,
                                                         S.Stage.name == stage_name))
        if not self.stage:
            names = session.scalars(select(S.Stage.name).where(S.Stage.spec_version_id == self.version.id)).all()
            raise ValueError(f"stage {stage_name!r} is not one of {names}")
        self.memo_data = {"stage": stage_name, "date": memo_date or date.today().isoformat(), "team": team or ""}
        self.counts: Counter = Counter()
        self.sequence = 0
        self.formats = self.load_formats()
        self.bindings = {b.concept_name: b for b in session.scalars(select(S.Binding).where(
            S.Binding.firm_id == self.firm.id, S.Binding.record_schema == self.revision.record_schema))}
        self.concepts = {c.id: c for c in session.scalars(select(S.Concept).where(S.Concept.spec_version_id == self.version.id))}

    # --- formats ---------------------------------------------------------------------------------
    def load_formats(self) -> dict[tuple[str, str], list[dict]]:
        out = defaultdict(list)
        for f in self.s.scalars(select(S.Format).where(S.Format.spec_version_id == self.version.id)):
            scales = [{"scale": x.scale, "from_value": x.from_value, "decimals": x.decimals}
                      for x in self.s.scalars(select(S.FormatScale).where(S.FormatScale.format_id == f.id))]
            out[(f.item, f.context)].append({c: getattr(f, c) for c in (
                "item", "context", "applies_to", "decimals", "symbol", "symbol_position", "unit_display",
                "negative_style", "thousands_separator", "decimal_separator", "suffix", "pattern", "example")}
                | {"scales": scales})
        return out

    def format_for(self, item: str, context: str) -> dict | None:
        """One format per kind of value and context: the context's own, else 'any'; the general one first."""
        for ctx in (context, "any"):
            options = self.formats.get((item, ctx)) or []
            if options:
                general = [f for f in options if not f.get("applies_to")]
                return (general or options)[0]
        return None

    def write_value(self, row_value, value_kind: str | None, context: str) -> str:
        """A record value as the firm writes it."""
        if row_value is None:
            return ""
        if isinstance(row_value, str):
            item = ITEM.get(value_kind or "")
            if item == "date" and re.match(r"\d{4}-\d{2}-\d{2}", row_value):
                fmt = self.format_for("date", context)
                return format_date(row_value, fmt) if fmt else row_value
            return row_value
        item = ITEM.get(value_kind or "", "count" if float(row_value).is_integer() else None)
        fmt = self.format_for(item, context) if item else None
        if not fmt:
            return f"{row_value:,.0f}" if float(row_value).is_integer() else f"{row_value:,.2f}"
        if row_value < 0:                                         # negatives follow the firm's negative style
            neg = self.format_for("negative", context)
            if neg and neg.get("negative_style"):
                fmt = {**fmt, "negative_style": neg["negative_style"]}
        return format_number(float(row_value), fmt)

    # --- record values for concepts ---------------------------------------------------------------
    def concept_value(self, name: str | None):
        """(record row or None, number/text value, path) for a concept through the firm's binding."""
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
        """The periods (or rows) of a concept's family, oldest first: for period columns and chart series."""
        b = self.bindings.get(name or "")
        if not b or b.kind != "path" or not b.path:
            return []
        m = RELATIVE.match(b.path)
        family = m.group("family") if m else (self.record.by_path[b.path].family if b.path in self.record.by_path else b.path)
        return self.record.families.get(family) or self.record.rows(family)

    # --- operations ----------------------------------------------------------------------------
    def op(self, op_type: str, target_id, payload: dict) -> None:
        self.sequence += 1
        self.s.add(Operation(memo_id=self.memo.id, sequence=self.sequence, actor_type="generator", op_type=op_type,
                             target_id=target_id, payload=payload, status="applied"))

    def flush_add(self, row):
        self.s.add(row)
        self.s.flush()
        return row

    # --- run -----------------------------------------------------------------------------------
    def run(self) -> dict:
        started = time.perf_counter()
        deal_name = self.record.text("company:name") or "Deal"
        self.memo = self.flush_add(Memo(firm_id=self.firm.id, deal_id=self.revision.deal_id, spec_version_id=self.version.id,
                                        record_revision_id=self.revision.id, stage_id=self.stage.id,
                                        title=f"{deal_name} · {self.spec.name} · {self.stage.name}",
                                        memo_date=self.memo_data["date"], deal_team=self.memo_data["team"] or None))
        sections = self.s.execute(select(S.Section).join(S.StageSection, S.StageSection.section_id == S.Section.id)
                                  .where(S.StageSection.stage_id == self.stage.id, S.StageSection.required)
                                  .order_by(S.Section.position)).scalars().all()
        for position, section in enumerate(sections):
            layout = self.s.execute(select(S.Layout).join(S.SectionLayout, S.SectionLayout.layout_id == S.Layout.id)
                                    .where(S.SectionLayout.section_id == section.id).order_by(S.Layout.position)).scalars().first()
            if layout is None:
                self.counts["section_without_layout"] += 1
                continue
            slide = self.flush_add(Slide(memo_id=self.memo.id, position=position, section_id=section.id,
                                         layout_id=layout.id, kind="template", background_ref=layout.background_ref))
            self.op("add_slide", slide.id, {"section": section.name, "layout": layout.name, "position": position})
            self.counts["slides"] += 1
            self.memo_data["page"] = str(position + 1)          # page number is memo data the slide position gives
            for el in self.s.scalars(select(Element).where(Element.layout_id == layout.id)
                                     .order_by(Element.z_index, Element.y_in, Element.x_in)):
                self.add_element(slide, el)
        self.memo.sequence = self.sequence
        self.s.commit()
        return {"memo_id": str(self.memo.id), "title": self.memo.title, "seconds": round(time.perf_counter() - started, 2),
                **dict(self.counts)}

    def add_element(self, slide: Slide, src: Element) -> None:
        el = self.flush_add(Element(slide_id=slide.id, origin_element_id=src.id, **{c: getattr(src, c) for c in COPY}))
        self.op("add_element", el.id, {"from": str(src.id), "type": src.type, "role": src.role})
        self.counts["elements"] += 1
        styles = self.s.scalars(select(ElementParagraph).where(ElementParagraph.element_id == src.id)
                                .order_by(ElementParagraph.position)).all()
        recipe = self.s.scalars(select(S.RecipeSegment).where(S.RecipeSegment.element_id == src.id)
                                .order_by(S.RecipeSegment.paragraph, S.RecipeSegment.position)).all()
        table_seg = next((r for r in recipe if r.kind == "table"), None)
        chart_seg = next((r for r in recipe if r.kind == "chart"), None)
        if src.type == "table" or table_seg:
            self.add_table(el, src)
        elif src.type == "chart" or chart_seg:
            self.add_chart(el, src)
        elif recipe:
            self.add_text(el, styles, recipe)
        else:                                                     # fixed text the layout already holds
            for p in styles:
                para = self.flush_add(ElementParagraph(element_id=el.id, position=p.position, level=p.level,
                                                       **{k: getattr(p, k) for k in STYLE}))
                for seg in self.s.scalars(select(Segment).where(Segment.paragraph_id == p.id).order_by(Segment.position)):
                    self.s.add(Segment(paragraph_id=para.id, position=seg.position, kind="text", text=seg.text))

    # --- text ----------------------------------------------------------------------------------
    def add_text(self, el: Element, styles: list, recipe: list) -> None:
        by_paragraph = defaultdict(list)
        for r in recipe:
            by_paragraph[r.paragraph or 0].append(r)
        for index in sorted(by_paragraph):
            style = styles[min(index, len(styles) - 1)] if styles else None
            para = self.flush_add(ElementParagraph(element_id=el.id, position=index, level=style.level if style else 0,
                                                   **({k: getattr(style, k) for k in STYLE} if style else {})))
            for pos, r in enumerate(by_paragraph[index]):
                self.s.add(self.segment(para.id, pos, r, "prose"))

    def segment(self, paragraph_id, position: int, r, context: str) -> Segment:
        concept = self.concepts.get(r.concept_id)
        if r.kind == "fixed":
            return Segment(paragraph_id=paragraph_id, position=position, kind="text", text=r.text or "")
        if r.kind in ("figure", "fact"):
            row, value, path = self.concept_value(concept.name if concept else None)
            self.counts["chips"] += 1
            if value is None:
                self.counts["chips_without_value"] += 1
                return Segment(paragraph_id=paragraph_id, position=position, kind="chip", text=f"[no data: {concept.name if concept else '?'}]",
                               concept_name=concept.name if concept else None)
            text = self.write_value(value, (concept.value_kind if concept else None) or (row.value_kind if row else None), context)
            return Segment(paragraph_id=paragraph_id, position=position, kind="chip", text=text, concept_name=concept.name,
                           record_path=path, value_snapshot=str(value))
        if r.kind == "memo_data":
            name = (concept.name if concept else "").lower()
            key = next((k for k in ("stage", "date", "team") if k in name), None) or \
                ("page" if any(w in name for w in ("page", "slide_number", "number")) else None)
            value = self.memo_data.get(key) if key else None
            if key == "team" and value:                           # one member per paragraph of the box
                members = [m.strip() for m in re.split(r"[;\n]", value) if m.strip()]
                value = members[min(r.paragraph or 0, len(members) - 1)] if members else value
            if key == "date" and value:
                fmt = self.format_for("date", context)
                value = format_date(value, fmt) if fmt else value
            self.counts["memo_data"] += 1
            return Segment(paragraph_id=paragraph_id, position=position, kind="text" if value else "person_slot",
                           text=value or f"‹person: {name}›", concept_name=concept.name if concept else None)
        if r.kind == "conditional":
            self.counts["conditionals"] += 1
            return Segment(paragraph_id=paragraph_id, position=position, kind="conditional",
                           text=f"‹if {r.when_rule}: {r.text}›")
        if r.kind == "model_writes":
            self.counts["model_drafts"] += 1
            return Segment(paragraph_id=paragraph_id, position=position, kind="model_draft", text=f"‹model: {r.what}›")
        self.counts["person_slots"] += 1
        return Segment(paragraph_id=paragraph_id, position=position, kind="person_slot", text=f"‹person: {r.what or ''}›")

    def cell(self, table_id, row: int, col: int, text: str, kind: str = "data", fill_ref=None, chip: dict | None = None):
        c = self.flush_add(TableCell(element_table_id=table_id, row=row, col=col, kind=kind, fill_ref=fill_ref))
        para = self.flush_add(ElementParagraph(table_cell_id=c.id, position=0))
        self.s.add(Segment(paragraph_id=para.id, position=0, kind="chip" if chip else "text", text=text, **(chip or {})))

    # --- tables --------------------------------------------------------------------------------
    def add_table(self, el: Element, src: Element) -> None:
        template = self.s.scalar(select(S.TableTemplate).where(S.TableTemplate.element_id == src.id))
        if not template:
            self.counts["tables_without_template"] += 1
            return
        self.counts["tables"] += 1
        table = self.flush_add(ElementTable(element_id=el.id, table_template_id=template.id, table_style_id=self.s.scalar(
            select(S.TableStyle.id).where(S.TableStyle.spec_version_id == self.version.id))))
        cols = self.s.scalars(select(S.TableTemplateColumn).where(S.TableTemplateColumn.table_template_id == template.id)
                              .order_by(S.TableTemplateColumn.position)).all()
        rows = self.s.scalars(select(S.TableTemplateRow).where(S.TableTemplateRow.table_template_id == template.id)
                              .order_by(S.TableTemplateRow.position)).all()
        fixed = {(c.row, c.col): c for c in self.s.scalars(select(S.TableTemplateCell).where(
            S.TableTemplateCell.table_template_id == template.id))}
        rule = self.s.scalar(select(S.TableColumnRule).where(S.TableColumnRule.table_template_id == template.id))
        periods = self.period_columns(rule, rows) if rule else []
        # columns: the label column(s), then one per period when the table has a period rule
        layout_cols = [c for c in cols if c.kind != "period"] if periods else cols
        widths = [c.width_in for c in layout_cols]
        if periods:
            rest = sum(c.width_in or 0 for c in cols if c.kind == "period") or None
            widths += [rest / len(periods) if rest else None] * len(periods)
        for i, w in enumerate(widths):
            self.s.add(TableColumn(element_table_id=table.id, position=i, width_in=w))
        for r_index, row in enumerate(rows):
            self.s.add(TableRow(element_table_id=table.id, position=r_index, kind=row.kind, height_in=row.height_in))
            concept = self.concepts.get(row.concept_id)
            if row.kind == "header":
                headers = [c.header_text or "" for c in layout_cols] + [self.period_label(p) for p in periods]
                for c_index, text in enumerate(headers):
                    f = fixed.get((r_index, c_index))
                    self.cell(table.id, r_index, c_index, text, "fixed", f.fill_ref if f else None)
                continue
            self.cell(table.id, r_index, 0, row.label or "", "fixed", (fixed.get((r_index, 0)).fill_ref if fixed.get((r_index, 0)) else None))
            if row.kind == "group" or not concept:
                for c_index in range(1, len(widths)):
                    f = fixed.get((r_index, c_index))
                    if f and (f.fixed_text or f.fill_ref):
                        self.cell(table.id, r_index, c_index, f.fixed_text or "", "fixed", f.fill_ref)
                continue
            if periods:
                family = {p.period: p for p in self.family_of(concept.name)}
                for k, p in enumerate(periods):
                    v = family.get(p.period)
                    value = v.number_value if v else None
                    self.counts["chips"] += 1
                    if value is None:
                        self.counts["chips_without_value"] += 1
                    self.cell(table.id, r_index, len(layout_cols) + k, self.write_value(value, concept.value_kind or (v.value_kind if v else None), "table") if value is not None else "",
                              chip={"concept_name": concept.name, "record_path": v.path if v else None,
                                    "value_snapshot": None if value is None else str(value)})
            else:
                row_value, value, path = self.concept_value(concept.name)
                self.counts["chips"] += 1
                if value is None:
                    self.counts["chips_without_value"] += 1
                text = self.write_value(value, concept.value_kind or (row_value.value_kind if row_value else None), "table") if value is not None else f"[no data: {concept.name}]"
                self.cell(table.id, r_index, 1, text, chip={"concept_name": concept.name, "record_path": path,
                                                             "value_snapshot": None if value is None else str(value)})

    def period_columns(self, rule, rows) -> list:
        """The periods a table shows: the last N actual and first M forecast of its first period concept."""
        for row in rows:
            concept = self.concepts.get(row.concept_id)
            family = self.family_of(concept.name) if concept else []
            dated = [r for r in family if r.period]
            if dated:
                actual = [r for r in dated if not r.is_forecast][-(rule.actual_periods or 0):] if rule.actual_periods else []
                forecast = [r for r in dated if r.is_forecast][: rule.forecast_periods or 0]
                return actual + forecast
        return []

    def period_label(self, row) -> str:
        """FY26 → FY26A / FY27F when the firm marks actual and forecast that way (its period examples say)."""
        label = str(row.period)
        examples = " ".join(f.get("example") or "" for f in self.formats.get(("period", "any"), []))
        m = re.fullmatch(r"FY(\d{2,4})", label)
        if m and re.search(r"FY\d{2}[AF]\b", examples):
            return f"FY{m.group(1)[-2:]}{'F' if row.is_forecast else 'A'}"
        return label

    # --- charts --------------------------------------------------------------------------------
    def add_chart(self, el: Element, src: Element) -> None:
        template = self.s.scalar(select(S.ChartTemplate).where(S.ChartTemplate.element_id == src.id))
        if not template:
            self.counts["charts_without_template"] += 1
            return
        self.counts["charts"] += 1
        chart = self.flush_add(ElementChart(element_id=el.id, chart_template_id=template.id, chart_type=template.chart_type))
        n = template.category_count or 6
        for series in self.s.scalars(select(S.ChartTemplateSeries).where(S.ChartTemplateSeries.chart_template_id == template.id)
                                     .order_by(S.ChartTemplateSeries.position)):
            concept = self.concepts.get(series.concept_id)
            family = [r for r in self.family_of(concept.name if concept else None) if r.number_value is not None]
            cs = self.flush_add(ChartSeries(element_chart_id=chart.id, position=series.position, name=series.label_rule,
                                            concept_name=concept.name if concept else None, color_ref=series.color_ref))
            if not family:
                self.counts["chart_series_without_data"] += 1
            for i, r in enumerate(family[-n:]):
                self.s.add(ChartPoint(chart_series_id=cs.id, position=i, category=self.period_label(r) if r.period else r.path,
                                      value=r.number_value, is_forecast=r.is_forecast))


def generate(firm: str, stage: str, memo_date: str | None = None, team: str | None = None) -> dict:
    with SessionLocal() as session:
        return Generator(session, firm, stage, memo_date, team).run()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("firm")
    parser.add_argument("stage")
    parser.add_argument("--date")
    parser.add_argument("--team")
    a = parser.parse_args()
    r = generate(a.firm, a.stage, a.date, a.team)
    sys.stdout.write(f"{r.pop('title')} ({r.pop('memo_id')}) in {r.pop('seconds')}s: "
                     + ", ".join(f"{k} {v}" for k, v in sorted(r.items())) + "\n")


if __name__ == "__main__":
    main()
