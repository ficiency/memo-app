"""Generate a new memo for a firm, a stage and the latest deal record, through operations.

Reads only data: the firm's latest spec version (sections per stage, layouts, recipes, table and chart
templates, formats), its binding and the latest record revision. Every slide and element is added as an
operation by the generator, so the memo's history starts with its creation. Nothing here knows any firm.

The spec is read once, the memo is built in memory with ids made here, and it is saved in one batch: a
remote database answers each round trip slowly, so the number of round trips is what decides the time.

Simple on purpose: conditional text, text the model writes and text a person writes are left as marked
slots; optional sections are left out; when a section has several layouts, the first is used. What this
gets wrong goes to evals/WHERE_IT_BREAKS.md.

Usage (from backend/):
    python -m app.services.generator.memo <firm> <stage> [--date YYYY-MM-DD] [--team "A (Partner); B"]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import uuid
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
        "date": "date", "per_share": "per_share", "currency": "currency", "number": "count"}
CURRENCY = {"£": "GBP", "€": "EUR", "$": "USD", "CHF": "CHF", "¥": "JPY"}
STYLE = ("font", "size_pt", "bold", "italic", "caps", "color_ref", "align", "anchor", "line_spacing",
         "space_before_pt", "space_after_pt", "bullet_char", "bullet_color_ref", "indent_in")
COPY = ("x_in", "y_in", "w_in", "h_in", "rotation_deg", "z_index", "name", "placeholder_idx", "type", "shape_kind",
        "role", "fill_kind", "firm_lock", "description", "fill_ref", "line_ref", "line_width_pt")


def by(rows, key) -> dict:
    out = defaultdict(list)
    for r in rows:
        out[getattr(r, key)].append(r)
    return out


class Generator:
    def __init__(self, session: Session, firm_name: str, stage_name: str, memo_date: str | None, team: str | None,
                 spec_version_id=None, revision_id=None, created_by=None):
        """Without a spec version or a record revision, the firm's latest ones."""
        self.s = session
        q = session.scalars
        self.firm = session.scalar(select(S.Firm).where(S.Firm.name == firm_name))
        if spec_version_id:
            self.version = session.get(S.SpecVersion, spec_version_id)
            self.spec = session.get(S.Spec, self.version.spec_id)
        else:
            self.spec = session.scalar(select(S.Spec).where(S.Spec.firm_id == self.firm.id))
            self.version = session.scalar(select(S.SpecVersion).where(S.SpecVersion.spec_id == self.spec.id)
                                          .order_by(S.SpecVersion.number.desc()))
        if self.spec.firm_id != self.firm.id:
            raise ValueError("that spec belongs to another firm")
        v = self.version.id
        self.revision = session.get(RecordRevision, revision_id) if revision_id else             session.scalar(select(RecordRevision).where(RecordRevision.firm_id == self.firm.id)
                           .order_by(RecordRevision.number.desc()))
        if self.revision is None or self.revision.firm_id != self.firm.id:
            raise ValueError("no deal record for this firm")
        self.created_by = created_by
        self.record = Record(session, self.revision.id)
        self.stage = session.scalar(select(S.Stage).where(S.Stage.spec_version_id == v, S.Stage.name == stage_name))
        if not self.stage:
            raise ValueError(f"stage {stage_name!r} is not one of "
                             f"{q(select(S.Stage.name).where(S.Stage.spec_version_id == v)).all()}")
        # the whole spec, read once
        self.layouts = {l.id: l for l in q(select(S.Layout).where(S.Layout.spec_version_id == v))}
        layout_elements = q(select(Element).where(Element.layout_id.in_(list(self.layouts)))).all()
        self.elements_of = by(layout_elements, "layout_id")
        element_ids = [e.id for e in layout_elements]
        paragraphs = q(select(ElementParagraph).where(ElementParagraph.element_id.in_(element_ids))).all()
        self.paragraphs_of = by(paragraphs, "element_id")
        self.segments_of = by(q(select(Segment).where(Segment.paragraph_id.in_([p.id for p in paragraphs]))).all(), "paragraph_id")
        self.recipe_of = by(q(select(S.RecipeSegment).where(S.RecipeSegment.spec_version_id == v)).all(), "element_id")
        templates = q(select(S.TableTemplate).where(S.TableTemplate.spec_version_id == v)).all()
        self.table_of = {t.element_id: t for t in templates}
        tids = [t.id for t in templates]
        self.t_cols = by(q(select(S.TableTemplateColumn).where(S.TableTemplateColumn.table_template_id.in_(tids))).all(), "table_template_id")
        self.t_rows = by(q(select(S.TableTemplateRow).where(S.TableTemplateRow.table_template_id.in_(tids))).all(), "table_template_id")
        self.t_cells = by(q(select(S.TableTemplateCell).where(S.TableTemplateCell.table_template_id.in_(tids))).all(), "table_template_id")
        self.t_rule = {r.table_template_id: r for r in q(select(S.TableColumnRule).where(S.TableColumnRule.table_template_id.in_(tids)))}
        charts = q(select(S.ChartTemplate).where(S.ChartTemplate.spec_version_id == v)).all()
        self.chart_of = {c.element_id: c for c in charts}
        self.c_series = by(q(select(S.ChartTemplateSeries).where(S.ChartTemplateSeries.chart_template_id.in_([c.id for c in charts]))).all(),
                           "chart_template_id")
        self.table_style_id = session.scalar(select(S.TableStyle.id).where(S.TableStyle.spec_version_id == v))
        self.concepts = {c.id: c for c in q(select(S.Concept).where(S.Concept.spec_version_id == v))}
        self.bindings = {b.concept_name: b for b in q(select(S.Binding).where(
            S.Binding.firm_id == self.firm.id, S.Binding.record_schema == self.revision.record_schema))}
        self.formats = self.load_formats()
        self.memo_data = {"stage": stage_name, "date": memo_date or date.today().isoformat(), "team": team or ""}
        self.counts: Counter = Counter()
        self.rows: list = []                                       # everything this memo adds, saved at the end
        self.sequence = 0

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

    # --- building ------------------------------------------------------------------------------
    def new(self, cls, **kw):
        row = cls(id=uuid.uuid4(), **kw)
        self.rows.append(row)
        return row

    def op(self, op_type: str, target_id, payload: dict) -> None:
        self.sequence += 1
        self.new(Operation, memo_id=self.memo.id, sequence=self.sequence, actor_type="generator", op_type=op_type,
                 target_id=target_id, payload=payload, status="applied")

    def run(self) -> dict:
        started = time.perf_counter()
        deal_name = self.record.text("company:name") or "Deal"
        self.memo = self.new(Memo, firm_id=self.firm.id, deal_id=self.revision.deal_id, spec_version_id=self.version.id,
                             record_revision_id=self.revision.id, stage_id=self.stage.id,
                             title=f"{deal_name} · {self.spec.name} · {self.stage.name}",
                             memo_date=self.memo_data["date"], deal_team=self.memo_data["team"] or None,
                             created_by=self.created_by)
        sections = self.s.execute(select(S.Section).join(S.StageSection, S.StageSection.section_id == S.Section.id)
                                  .where(S.StageSection.stage_id == self.stage.id, S.StageSection.required)
                                  .order_by(S.Section.position)).scalars().all()
        section_layouts = by(self.s.scalars(select(S.SectionLayout).where(
            S.SectionLayout.section_id.in_([x.id for x in sections]))).all(), "section_id")
        position = 0
        for section in sections:
            options = sorted((self.layouts[x.layout_id] for x in section_layouts[section.id]), key=lambda l: l.position)
            if not options:
                self.counts["section_without_layout"] += 1
                continue
            layout = options[0]
            slide = self.new(Slide, memo_id=self.memo.id, position=position, section_id=section.id, layout_id=layout.id,
                             kind="template", background_ref=layout.background_ref, revision=1)
            self.op("add_slide", slide.id, {"section": section.name, "layout": layout.name, "position": position})
            self.counts["slides"] += 1
            self.memo_data["page"] = str(position + 1)            # page number is memo data the slide position gives
            for el in sorted(self.elements_of[layout.id], key=lambda e: (e.z_index or 0, e.y_in or 0, e.x_in or 0)):
                self.add_element(slide, el)
            position += 1
        self.memo.sequence = self.sequence
        # one batch per table, parents before children (the models declare foreign keys, not relationships)
        for cls in (Memo, Slide, Element, ElementTable, TableColumn, TableRow, TableCell, ElementParagraph, Segment,
                    ElementChart, ChartSeries, ChartPoint, Operation):
            self.s.add_all([r for r in self.rows if type(r) is cls])
            self.s.flush()
        self.s.commit()
        return {"memo_id": str(self.memo.id), "title": self.memo.title, "seconds": round(time.perf_counter() - started, 2),
                **dict(self.counts)}

    def add_element(self, slide: Slide, src: Element) -> None:
        el = self.new(Element, slide_id=slide.id, origin_element_id=src.id, revision=1, **{c: getattr(src, c) for c in COPY})
        self.op("add_element", el.id, {"from": str(src.id), "type": src.type, "role": src.role})
        self.counts["elements"] += 1
        recipe = sorted(self.recipe_of[src.id], key=lambda r: (r.paragraph or 0, r.position))
        styles = sorted(self.paragraphs_of[src.id], key=lambda p: p.position)
        if src.id in self.table_of:
            self.add_table(el, self.table_of[src.id])
        elif src.id in self.chart_of:
            self.add_chart(el, self.chart_of[src.id])
        elif recipe:
            self.add_text(el, styles, recipe)
        else:                                                      # fixed text the layout already holds
            for p in styles:
                para = self.new(ElementParagraph, element_id=el.id, position=p.position, level=p.level,
                                **{k: getattr(p, k) for k in STYLE})
                for seg in sorted(self.segments_of[p.id], key=lambda x: x.position):
                    self.new(Segment, paragraph_id=para.id, position=seg.position, kind="text", text=seg.text)

    # --- text ----------------------------------------------------------------------------------
    def add_text(self, el: Element, styles: list, recipe: list) -> None:
        by_paragraph = defaultdict(list)
        for r in recipe:
            by_paragraph[r.paragraph or 0].append(r)
        for index in sorted(by_paragraph):
            style = styles[min(index, len(styles) - 1)] if styles else None
            para = self.new(ElementParagraph, element_id=el.id, position=index, level=style.level if style else 0,
                            **({k: getattr(style, k) for k in STYLE} if style else {}))
            for pos, r in enumerate(by_paragraph[index]):
                self.segment(para.id, pos, r, "prose")

    def segment(self, paragraph_id, position: int, r, context: str) -> None:
        concept = self.concepts.get(r.concept_id)
        name = concept.name if concept else None
        add = lambda kind, text, **kw: self.new(Segment, paragraph_id=paragraph_id, position=position, kind=kind, text=text, **kw)
        if r.kind == "fixed":
            add("text", r.text or "")
        elif r.kind in ("figure", "fact"):
            row, value, path = self.concept_value(name)
            self.counts["chips"] += 1
            if value is None:
                self.counts["chips_without_value"] += 1
                add("chip", f"[no data: {name or '?'}]", concept_name=name)
                return
            text = self.write_value(value, self.kind_of(concept, row), context, row.unit if row is not None else None)
            add("chip", text, concept_name=name, record_path=path, value_snapshot=str(value))
        elif r.kind == "memo_data":
            lname = (name or "").lower()
            key = next((k for k in ("stage", "date", "team") if k in lname), None) or \
                ("page" if any(w in lname for w in ("page", "slide_number", "number")) else None)
            value = self.memo_data.get(key) if key else None
            if key == "team" and value:                            # one member per paragraph of the box
                members = [m.strip() for m in re.split(r"[;\n]", value) if m.strip()]
                value = members[min(r.paragraph or 0, len(members) - 1)] if members else value
            if key == "date" and value:
                fmt = self.format_for("date", context)
                value = format_date(value, fmt) if fmt else value
            self.counts["memo_data"] += 1
            add("text" if value else "person_slot", value or f"‹person: {lname}›", concept_name=name)
        elif r.kind == "conditional":
            self.counts["conditionals"] += 1
            add("conditional", f"‹if {r.when_rule}: {r.text}›")
        elif r.kind == "model_writes":
            self.counts["model_drafts"] += 1
            add("model_draft", f"‹model: {r.what}›")
        else:
            self.counts["person_slots"] += 1
            add("person_slot", f"‹person: {r.what or ''}›")

    def cell(self, table_id, row: int, col: int, text: str, kind: str = "data", fill_ref=None, chip: dict | None = None,
             seg_kind: str | None = None) -> None:
        c = self.new(TableCell, element_table_id=table_id, row=row, col=col, kind=kind, fill_ref=fill_ref)
        para = self.new(ElementParagraph, table_cell_id=c.id, position=0)
        self.new(Segment, paragraph_id=para.id, position=0, kind=seg_kind or ("chip" if chip else "text"), text=text, **(chip or {}))

    def chip_text(self, concept, row, value, context=None) -> tuple[str, dict]:
        context = context or getattr(self, "cell_context", "table")
        self.counts["chips"] += 1
        if value is None:
            self.counts["chips_without_value"] += 1
        text = self.write_value(value, self.kind_of(concept, row), context, row.unit if row is not None else None) \
            if value is not None else ""
        return text, {"concept_name": concept.name, "record_path": row.path if row is not None else None,
                      "value_snapshot": None if value is None else str(value)}

    # --- tables --------------------------------------------------------------------------------
    def add_table(self, el: Element, template) -> None:
        self.counts["tables"] += 1
        table = self.new(ElementTable, element_id=el.id, table_template_id=template.id, table_style_id=self.table_style_id)
        cols = sorted(self.t_cols[template.id], key=lambda c: c.position)
        rows = sorted(self.t_rows[template.id], key=lambda r: r.position)
        fixed = {(c.row, c.col): c for c in self.t_cells[template.id]}
        rule = self.t_rule.get(template.id)
        periods = self.period_columns(rule, rows) if rule else []
        layout_cols = [c for c in cols if c.kind != "period"] if periods else cols
        widths = [c.width_in for c in layout_cols]
        if periods:
            rest = sum(c.width_in or 0 for c in cols if c.kind == "period") or None
            widths += [rest / len(periods) if rest else None] * len(periods)
        n_cols = max(len(widths), 1)
        for i, w in enumerate(widths):
            self.new(TableColumn, element_table_id=table.id, position=i, width_in=w)
        headers = [(c.header_text or "").strip().lower() for c in layout_cols]
        # a table leaves the unit out of its cells only when a header carries it ("£k", "€m") (21)
        self.cell_context = "table" if any(re.search(r"[£€$]|(?<![a-z])(k|m|bn)(?![a-z])", h) for h in headers) or periods else "prose"
        for r_index, row in enumerate(rows):
            self.new(TableRow, element_table_id=table.id, position=r_index, kind="data" if row.kind == "variable" else row.kind,
                     height_in=row.height_in)
            concept = self.concepts.get(row.concept_id)
            fill0 = fixed.get((r_index, 0))
            if row.kind == "header":
                labels = [c.header_text or "" for c in layout_cols] + [self.period_label(p) for p in periods]
                for c_index, text in enumerate(labels):
                    f = fixed.get((r_index, c_index))
                    self.cell(table.id, r_index, c_index, text, "fixed", f.fill_ref if f else None)
                continue
            if row.kind == "variable":                             # rows that change per deal: one marked slot
                self.counts["model_drafts"] += 1
                self.cell(table.id, r_index, 0, f"‹model: {row.label or 'rows'}›", seg_kind="model_draft")
                continue
            self.cell(table.id, r_index, 0, row.label or "", "fixed", fill0.fill_ref if fill0 else None)
            if row.kind == "group" or not concept:
                for c_index in range(1, n_cols):
                    f = fixed.get((r_index, c_index))
                    if f and (f.fixed_text or f.fill_ref):
                        self.cell(table.id, r_index, c_index, f.fixed_text or "", "fixed", f.fill_ref)
                continue
            if periods:
                family = {p.period: p for p in self.family_of(concept.name)}
                for k, p in enumerate(periods):
                    v = family.get(p.period)
                    text, chip = self.chip_text(concept, v, v.number_value if v is not None else None)
                    self.cell(table.id, r_index, len(layout_cols) + k, text, chip=chip)
                continue
            family = self.family_of(concept.name)
            keyed = {r.path.split("[", 1)[-1].split("]", 1)[0].strip().lower(): r for r in family if "[" in r.path}
            case_cols = [i for i, h in enumerate(headers) if i > 0 and h and h in keyed]
            if case_cols:                                          # one column per case (Bear, Base, Bull) (12)
                for i in case_cols:
                    v = keyed[headers[i]]
                    value = v.number_value if v.number_value is not None else v.text_value
                    text, chip = self.chip_text(concept, v, value)
                    self.cell(table.id, r_index, i, text, chip=chip)
                continue
            row_value, value, path = self.concept_value(concept.name)
            text, chip = self.chip_text(concept, row_value, value)
            self.cell(table.id, r_index, 1, text or f"[no data: {concept.name}]", chip=chip)

    def period_columns(self, rule, rows) -> list:
        """The periods a table shows: the last N actual and first M forecast of its first period concept."""
        for row in rows:
            concept = self.concepts.get(row.concept_id)
            dated = [r for r in (self.family_of(concept.name) if concept else []) if r.period]
            if dated:
                actual = [r for r in dated if not r.is_forecast][-(rule.actual_periods or 0):] if rule.actual_periods else []
                forecast = [r for r in dated if r.is_forecast][: rule.forecast_periods or 0]
                return actual + forecast
        return []

    def period_label(self, row) -> str:
        """FY26 → FY26A / FY27F when the firm marks actual and forecast that way (its period examples say)."""
        label = str(row.period)
        examples = " ".join(f.get("example") or "" for key, fs in self.formats.items() if key[0] == "period" for f in fs)
        m = re.fullmatch(r"FY(\d{2,4})", label)
        if m and re.search(r"FY\d{2}[AF]\b", examples):
            return f"FY{m.group(1)[-2:]}{'F' if row.is_forecast else 'A'}"
        return label

    # --- charts --------------------------------------------------------------------------------
    def add_chart(self, el: Element, template) -> None:
        self.counts["charts"] += 1
        chart = self.new(ElementChart, element_id=el.id, chart_template_id=template.id, chart_type=template.chart_type)
        n = template.category_count or 6
        m = re.match(r"periods: (\d+) actual \+ (\d+) forecast", template.category_rule or "")
        actual, forecast = (int(m.group(1)), int(m.group(2))) if m else (None, None)
        for series in sorted(self.c_series[template.id], key=lambda x: x.position):
            concept = self.concepts.get(series.concept_id)
            family = [r for r in self.family_of(concept.name if concept else None) if r.number_value is not None]
            cs = self.new(ChartSeries, element_chart_id=chart.id, position=series.position, name=series.label_rule,
                          concept_name=concept.name if concept else None, color_ref=series.color_ref)
            if not family:
                self.counts["chart_series_without_data"] += 1
            dated = [r for r in family if r.period]
            if actual is not None and dated:                       # same periods as the firm's period rule (15)
                chosen = [r for r in dated if not r.is_forecast][-actual:] if actual else []
                chosen += [r for r in dated if r.is_forecast][:forecast]
            else:
                chosen = family[-n:]
            for i, r in enumerate(chosen):
                self.new(ChartPoint, chart_series_id=cs.id, position=i, value=r.number_value, is_forecast=r.is_forecast,
                         category=self.period_label(r) if r.period else r.path.split("[", 1)[-1].split("]", 1)[0])


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
