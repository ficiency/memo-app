"""Write a firm's approved onboarding (steps 1-5) into the spec tables as a new spec version.

The last piece of the onboarding and the only path from its results to the database. Each run adds a
version (earlier versions stay); nothing here knows any firm.

Usage (from backend/):
    python -m app.services.onboarding.writer <firm>
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import REPO
from app.db import spec as S
from app.db.memo import Element, ElementParagraph, Segment
from app.db.session import SessionLocal
from app.services.onboarding.common import firm_dir, load_step

MANIFESTS = REPO / "backend" / "onboarding_firms"
# a figure as memos write it: a number with an optional currency, sign, brackets, scale, % or x ("£22.9m", "(1,240)")
NUMBER = re.compile(r"[(]?[-−]?[£€$]?[-−]?[0-9][0-9,.]*[ ]?(%|x|k|m|bn|pp|bps)?[)]?")
MASTER = "Master"
LOCK = {"firm": "fixed", "content": "content"}                 # step 3 lock → firm_lock; anything else is free
STYLE_KEYS = ("font", "size_pt", "bold", "italic", "caps", "align", "anchor")
ALIGN = {"l": "left", "c": "center", "ctr": "center", "r": "right", "j": "justify", "just": "justify"}
PERIOD = re.compile(r"^(FY|CY|H[12]|Q[1-4])?\s?'?\d{2,4}\s?[AFEBP]?$|^(FY|CY)", re.I)
TABLE_COLOURS = {"header_fill": "header_fill_ref", "header_text": "header_text_ref",
                 "forecast_header_fill": "forecast_header_fill_ref", "body_text": "body_text_ref",
                 "banding": "banding_ref", "rule_color": "rule_color_ref", "highlight_color": "highlight_color_ref"}


def evidence(item) -> dict:
    ev = (item or {}).get("evidence") if isinstance(item, dict) else None
    ev = ev[0] if isinstance(ev, list) and ev else ev
    ev = ev if isinstance(ev, dict) else {}
    return {"source": ev.get("source"), "evidence_quote": ev.get("quote") or None, "evidence_ref": ev.get("ref")}


def row_key(label) -> str:
    """A row label for matching: case and spaces ignored, and a footnote letter after a capital, a digit or
    a bracket dropped ("Closing ARRa" matches "Closing ARR")."""
    s = re.sub(r"\s+", " ", str(label or "")).strip()
    s = re.sub(r"(?<=[A-Z0-9)%])[a-z]$", "", s)
    return s.lower()


def text(v) -> str | None:
    if v is None:
        return None
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)


class SpecWriter:
    def __init__(self, session: Session, firm: str):
        self.s, self.firm = session, firm
        self.style = load_step(firm, 1)["answer"]
        self.structure = load_step(firm, 2)["answer"]
        self.step3 = load_step(firm, 3)
        self.step4 = load_step(firm, 4)
        self.rules_answer = load_step(firm, 5)["answer"]
        self.counts: Counter = Counter()
        self.palette_by_hex = {str(p.get("hex", "")).upper(): p["name"] for p in self.style.get("palette") or []}
        self.palette_names = {str(p.get("name", "")).lower(): p["name"] for p in self.style.get("palette") or []}
        self.sections: dict[str, uuid.UUID] = {}
        self.layouts: dict[str, uuid.UUID] = {}
        self.boxes: dict[tuple[str, str], uuid.UUID] = {}
        self.box_data: dict[tuple[str, str], dict] = {}
        self.concepts: dict[str, uuid.UUID] = {}

    # --- helpers --------------------------------------------------------------------------------
    def add(self, row):
        self.s.add(row)
        self.counts[row.__tablename__] += 1
        return row

    def flush_add(self, row):
        self.add(row)
        self.s.flush()
        return row

    def colour(self, c) -> str | None:
        """A palette name when the colour is in the palette; otherwise its hex, so nothing is lost."""
        if not c:
            return None
        if isinstance(c, dict):
            h = str(c.get("hex") or "").upper()
            return self.palette_by_hex.get(h, h or None)
        c = str(c)
        if c.startswith("#"):
            return self.palette_by_hex.get(c.upper(), c.upper())
        return self.palette_names.get(c.lower(), c)

    def concept(self, name, kind="figure", value_kind=None) -> uuid.UUID | None:
        if not name or not isinstance(name, str):
            return None
        if name not in self.concepts:
            kind = kind if kind in ("figure", "fact", "memo_data") else "figure"
            self.concepts[name] = self.flush_add(S.Concept(spec_version_id=self.v, name=name, kind=kind,
                                                           value_kind=value_kind, source="memo")).id
        return self.concepts[name]

    # --- run ------------------------------------------------------------------------------------
    def run(self) -> dict:
        manifest = MANIFESTS / f"{self.firm}.json"
        name = json.loads(manifest.read_text(encoding="utf-8"))["firm"] if manifest.exists() else self.firm
        firm = self.s.scalar(select(S.Firm).where(S.Firm.name == name)) or self.flush_add(S.Firm(name=name))
        for who in ("alex", "sam"):                                   # two mock editors per firm
            email = f"{who}@{self.firm}.demo"
            if not self.s.scalar(select(S.User).where(S.User.email == email)):
                self.add(S.User(email=email, display_name=who.title(), firm_id=firm.id))
        spec_name = ((self.style.get("identity") or {}).get("memo_name") or {}).get("value") or "Memo"
        spec = self.s.scalar(select(S.Spec).where(S.Spec.firm_id == firm.id, S.Spec.name == spec_name)) \
            or self.flush_add(S.Spec(firm_id=firm.id, name=spec_name))
        number = (self.s.scalar(select(func.max(S.SpecVersion.number)).where(S.SpecVersion.spec_id == spec.id)) or 0) + 1
        page = self.style.get("page") or {}
        version = self.flush_add(S.SpecVersion(
            spec_id=spec.id, number=number, label="onboarding", status="approved", created_from="onboarding",
            page_w_in=page.get("width_in"), page_h_in=page.get("height_in"), approved_at=datetime.now(timezone.utc)))
        self.v = version.id
        self.house_style()
        self.structure_rows()
        self.layout_rows()
        self.recipes()
        self.rules()
        self.questions_and_conflicts()
        self.s.commit()
        return {"firm": name, "spec": spec_name, "version": number, "rows": dict(self.counts)}

    # --- step 1 ---------------------------------------------------------------------------------
    def house_style(self) -> None:
        st = self.style
        for c in st.get("palette") or []:
            self.add(S.PaletteColor(spec_version_id=self.v, name=c["name"], hex=c["hex"].upper(), use_rule=c.get("use"),
                                    **evidence(c)))
        fonts = st.get("fonts") or {}
        for role in ("heading", "body", "tables"):
            if fonts.get(role):
                self.add(S.Font(spec_version_id=self.v, role=role, family=fonts[role], **evidence(fonts)))
        for r in st.get("text_roles") or []:
            self.add(S.TextRole(spec_version_id=self.v, name=r["role"], applies_to=r.get("applies_to"), font=r.get("font"),
                                size_pt=r.get("size_pt"), min_size_pt=r.get("min_size_pt"), max_size_pt=r.get("max_size_pt"),
                                bold=r.get("bold"), italic=r.get("italic"), caps=r.get("caps"),
                                color_ref=self.colour(r.get("color")), **evidence(r)))
        for f in st.get("formats") or []:
            ex = f.get("examples")
            fmt = self.flush_add(S.Format(
                spec_version_id=self.v, item=f["item"], context=f.get("context") or "any", rule=f["rule"],
                example=", ".join(ex) if isinstance(ex, list) else ex,
                **{k: f.get(k) for k in ("applies_to", "decimals", "symbol", "symbol_position", "unit_display", "negative_style",
                                         "thousands_separator", "decimal_separator", "suffix", "pattern")},
                **evidence(f)))
            for rule in f.get("scales") or []:
                self.add(S.FormatScale(format_id=fmt.id, scale=rule["scale"], from_value=rule.get("from_value") or 0,
                                       decimals=rule.get("decimals") or 0))
        # grid: margins from step 3 (house style, or measured from the design file); columns from step 1
        margins = self.step3.get("margins") or {}
        grid = (st.get("page") or {}).get("grid") or {}
        if margins or grid:
            self.add(S.Grid(spec_version_id=self.v, margin_top_in=margins.get("top"), margin_right_in=margins.get("right"),
                            margin_bottom_in=margins.get("bottom"), margin_left_in=margins.get("left"),
                            columns=grid.get("columns"), gutter_in=grid.get("gutter_in"),
                            source="template" if "measured" in str(self.step3.get("margins_source")) else "guide"))
        t = st.get("tables") or {}
        if t:
            self.add(S.TableStyle(spec_version_id=self.v, **{col: self.colour(t.get(key)) for key, col in TABLE_COLOURS.items()},
                                  header_bold=t.get("header_bold"), total_bold=t.get("total_bold"),
                                  highlight_bold=t.get("highlight_bold"), rule_width_pt=t.get("rule_width_pt"),
                                  header_rule_width_pt=t.get("header_rule_width_pt"), number_align=t.get("number_align"),
                                  min_size_pt=t.get("min_size_pt"), **evidence(t)))
        ch = st.get("charts") or {}
        if ch:
            style = self.flush_add(S.ChartStyle(
                spec_version_id=self.v, native_only=bool(ch.get("native")), labels_on=ch.get("labels_on"),
                label_size_pt=ch.get("label_size_pt"), gridlines=ch.get("gridlines"), legend_rule=text(ch.get("legend")),
                title_rule=text(ch.get("title")), forecast_treatment=text(ch.get("forecast")),
                focus_color_ref=self.colour(ch.get("focus_color")), **evidence(ch)))
            for i, c in enumerate(ch.get("series_colours") or []):
                self.add(S.ChartSeriesColor(chart_style_id=style.id, position=i, color_ref=self.colour(c)))
        cit = st.get("citations") or {}
        if cit.get("scheme"):
            self.add(S.CitationScheme(spec_version_id=self.v, scheme=cit["scheme"], marker=cit.get("marker"),
                                      placement=cit.get("placement"), **evidence(cit)))
        footer = st.get("footer") or {}
        for i, item in enumerate(footer.get("items") or []):
            self.add(S.FooterItem(spec_version_id=self.v, position=i, kind="fixed", text=item, **evidence(footer)))

    # --- step 2 ---------------------------------------------------------------------------------
    def structure_rows(self) -> None:
        sections = self.structure.get("sections") or []
        for i, sec in enumerate(sections):
            count = str(sec.get("count") or "one")
            self.sections[sec["name"]] = self.flush_add(S.Section(
                spec_version_id=self.v, name=sec["name"], status=sec.get("status") or "optional",
                count="n" if count.isdigit() else count, count_n=int(count) if count.isdigit() else None,
                must_contain=sec.get("must_contain"), numbering_scheme=sec.get("numbering"), position=i,
                **evidence(sec))).id
        for sec in sections:
            for c in sec.get("constraints") or []:
                kind, _, arg = c.partition(":")
                target = self.sections.get(arg)
                self.add(S.OrderConstraint(spec_version_id=self.v, section_id=self.sections[sec["name"]], kind=kind,
                                           target_section_id=target, position=int(arg) if arg.isdigit() else None,
                                           target_status=arg if arg and not target and not arg.isdigit() else None))
        for i, st in enumerate(self.structure.get("stages") or []):
            stage = self.flush_add(S.Stage(spec_version_id=self.v, name=st["name"], position=i, **evidence(st)))
            for required, names in ((True, st.get("required") or []), (False, st.get("optional") or [])):
                for n in names:
                    if n in self.sections:
                        self.add(S.StageSection(stage_id=stage.id, section_id=self.sections[n], required=required))

    # --- step 3 ---------------------------------------------------------------------------------
    def layout_rows(self) -> None:
        master = self.flush_add(S.Layout(spec_version_id=self.v, name=MASTER, is_master=True, position=0,
                                         source_kind="generated" if {b.get("source") for b in self.step3.get("master") or []} == {"generated"} else "imported",
                                         background_ref=self.colour(self.step3.get("master_background"))))
        self.layouts[MASTER] = master.id
        for b in self.step3.get("master") or []:
            self.element(master.id, MASTER, b)
        for i, lay in enumerate(self.step3.get("layouts") or [], start=1):
            row = self.flush_add(S.Layout(spec_version_id=self.v, name=lay["name"], source_kind=lay.get("source") or "imported",
                                          shows_master=lay.get("shows_master", True), position=i,
                                          background_ref=self.colour(lay.get("background"))))
            self.layouts[lay["name"]] = row.id
            for b in lay.get("boxes") or []:
                self.element(row.id, lay["name"], b)
        for sec in self.structure.get("sections") or []:
            for ln in sec.get("layouts") or []:
                ln = ln.removeprefix("to_generate:")
                if ln in self.layouts and sec["name"] in self.sections:
                    self.add(S.SectionLayout(section_id=self.sections[sec["name"]], layout_id=self.layouts[ln]))

    def paragraph(self, el_id, position, look: dict, level=None):
        kw = {k: look.get(k) for k in STYLE_KEYS if look.get(k) is not None}
        if "align" in kw:
            kw["align"] = ALIGN.get(kw["align"], kw["align"])
        for flag in ("bold", "italic", "caps"):                     # PowerPoint writes caps as "all" / "small"
            if flag in kw:
                kw[flag] = kw[flag] in (True, "all", "1", "true")
        return self.flush_add(ElementParagraph(element_id=el_id, position=position,
                                               level=(level or look.get("level") or 1) - 1,
                                               color_ref=self.colour(look.get("color")), **kw))

    def element(self, layout_id, layout_name: str, b: dict) -> None:
        f = b.get("frame") or {}
        line = b.get("line") or {}
        line = line if isinstance(line, dict) else {"color": line}
        el = self.flush_add(Element(
            layout_id=layout_id, name=b.get("name"), type=b.get("type") or "shape",
            placeholder_idx=(b.get("placeholder") or {}).get("idx"), role=b.get("role") or b.get("text_role"),
            firm_lock=LOCK.get(b.get("lock"), "free"), fill_kind="fixed" if b.get("text") else "person",
            description=b.get("prompt") or b.get("what"), fill_ref=self.colour(b.get("fill")),
            line_ref=self.colour(line.get("color")), line_width_pt=line.get("width_pt"),
            x_in=f.get("x", 0), y_in=f.get("y", 0), w_in=f.get("w", 0), h_in=f.get("h", 0)))
        self.boxes[(layout_name, b.get("id"))] = el.id
        self.box_data[(layout_name, b.get("id"))] = b
        style = b.get("style") or {}
        fixed_lines = str(b["text"]).split("\n") if b.get("text") else []
        if b.get("paragraphs"):                                     # G1: each paragraph's own look
            for i, p in enumerate(b["paragraphs"]):
                para = self.paragraph(el.id, i, {**style, **p})
                if fixed_lines and i < len(fixed_lines):
                    self.add(Segment(paragraph_id=para.id, position=0, kind="text", text=fixed_lines[i]))
        elif b.get("levels"):                                       # G1: one paragraph style per level
            for i, lv in enumerate(b["levels"]):
                self.paragraph(el.id, i, {**style, **lv}, level=lv.get("level"))
        elif style or fixed_lines:
            for i, line_text in enumerate(fixed_lines or [None]):
                para = self.paragraph(el.id, i, style)
                if line_text is not None:
                    self.add(Segment(paragraph_id=para.id, position=0, kind="text", text=line_text))

    # --- step 4 ---------------------------------------------------------------------------------
    def recipes(self) -> None:
        for name, c in (self.step4.get("catalogue") or {}).items():
            kind = c.get("type")
            values = [v for vs in (c.get("examples") or {}).values() for v in vs]
            numeric = any(NUMBER.fullmatch(str(v).strip()) for v in values)
            if kind not in ("figure", "fact", "memo_data"):      # "mixed" and the like: the examples decide
                kind = "figure" if numeric or not values else "fact"
            if kind == "figure" and values and not numeric:
                kind = "fact"                                    # the memos write it as words, not as a number
                self.counts["concepts_typed_fact_from_examples"] += 1
            self.concept(name, kind, c.get("kind") if kind == "figure" else "text")
            # what the binding needs to recognise the concept: where it is used and how past memos wrote it
            examples = "; ".join(f"{memo}: {', '.join(v[:2])}" for memo, v in list((c.get("examples") or {}).items())[:2] if v)
            row = self.s.get(S.Concept, self.concepts[name])
            row.description = f"used in {', '.join(c.get('used_in') or [])[:200]}" + (f"; examples {examples[:300]}" if examples else "")
        for c in (self.step4["answer"].get("extra_concepts") or []):
            self.concept(c.get("concept"), c.get("type"), c.get("kind"))
        for r in self.step4["answer"].get("recipes") or []:
            key = (r.get("layout"), r.get("box_id"))
            el_id = self.boxes.get(key)
            if not el_id:
                self.counts["recipe_without_box"] += 1
                continue
            for pos, p in enumerate(r.get("pattern") or []):
                seg = self.flush_add(S.RecipeSegment(
                    spec_version_id=self.v, element_id=el_id, paragraph=p.get("paragraph") or 0, position=pos,
                    kind=p["type"], text=p.get("text"), concept_id=self.concept(p.get("concept"), p["type"], p.get("kind")),
                    when_rule=text(p.get("when")), what=text(p.get("what")) or text(p.get("columns")),
                    seen_in=p.get("seen_in_counted"), memos_with_box=p.get("memos_with_box"), source="memo"))
                for memo, example in (p.get("style_examples") or {}).items():
                    self.add(S.StyleExample(recipe_segment_id=seg.id, memo_name=memo, text=str(example)))
                for c in p.get("must_include") or []:
                    if self.concept(c):
                        self.add(S.RecipeMustInclude(recipe_segment_id=seg.id, concept_id=self.concepts[c]))
                if p["type"] == "table":
                    self.table_template(el_id, self.box_data.get(key) or {}, p)
                if p["type"] == "chart":
                    self.chart_template(el_id, p)

    def table_template(self, el_id, box: dict, p: dict) -> None:
        """G4 + G5: the template's own table structure, the recipe's rows and the period rule."""
        t = self.flush_add(S.TableTemplate(spec_version_id=self.v, element_id=el_id, source="template" if box.get("table") else "memo"))
        sample = box.get("table") or {}
        header = [c.get("text", "") for c in (sample.get("rows") or [{}])[0].get("cells", [])] if sample else list(p.get("header") or [])
        widths = sample.get("column_widths_in") or [None] * len(header)
        for i, h in enumerate(header):
            kind = "label" if i == 0 else "period" if PERIOD.match(str(h).strip()) else "data"
            self.add(S.TableTemplateColumn(table_template_id=t.id, position=i, width_in=widths[i] if i < len(widths) else None,
                                           header_text=h, kind=kind))
        # The recipe says which rows are real; the template gives structure (header and group rows, heights,
        # fills). A template data row stays only when a recipe row matches it (its sample labels, such as
        # "Name" or "Guidance: …", are placeholders); rows that change per deal become "variable" rows.
        recipe_rows = [r for r in p.get("rows") or [] if isinstance(r, dict)]
        by_label = {row_key(r.get("label")): r for r in recipe_rows if r.get("label")}
        used, position = set(), 0
        for row in sample.get("rows") or []:
            cells = row.get("cells") or []
            label = cells[0].get("text", "") if cells else ""
            kind = row.get("kind") or "data"
            match = by_label.get(row_key(label))
            if kind == "data" and not match:
                continue
            if match:
                used.add(row_key(label))
            self.add(S.TableTemplateRow(table_template_id=t.id, position=position, kind=kind, label=label,
                                        height_in=row.get("height_in"),
                                        concept_id=self.concept(match.get("concept"), match.get("cells")) if match else None))
            for j, c in enumerate(cells):
                fixed = kind in ("header", "group") or j == 0
                if c.get("spanned") or not (fixed or c.get("fill")):
                    continue
                self.add(S.TableTemplateCell(table_template_id=t.id, row=position, col=j, kind="fixed" if fixed else "data",
                                             fixed_text=c.get("text") if fixed else None, fill_ref=self.colour(c.get("fill"))))
            position += 1
        if not sample and p.get("header"):                      # no template table: a header row from the recipe
            self.add(S.TableTemplateRow(table_template_id=t.id, position=position, kind="header", label=p["header"][0]))
            for j, h in enumerate(p["header"]):
                self.add(S.TableTemplateCell(table_template_id=t.id, row=position, col=j, kind="fixed", fixed_text=h))
            position += 1
        for r in recipe_rows:
            if row_key(r.get("label")) in used:
                continue
            fixed_label = r.get("label_type", "fixed") == "fixed"
            self.add(S.TableTemplateRow(table_template_id=t.id, position=position, kind="data" if fixed_label else "variable",
                                        label=r.get("label"), concept_id=self.concept(r.get("concept"), r.get("cells"))))
            position += 1
        rule = p.get("column_rule")
        if isinstance(rule, dict):
            self.add(S.TableColumnRule(table_template_id=t.id, actual_periods=rule.get("actual_periods"),
                                       forecast_periods=rule.get("forecast_periods"), period_label_format=rule.get("label_format")))

    def chart_template(self, el_id, p: dict) -> None:
        """G6: what a native chart is built from."""
        rule = p.get("column_rule") if isinstance(p.get("column_rule"), dict) else {}
        count = p.get("category_count") or ((rule.get("actual_periods") or 0) + (rule.get("forecast_periods") or 0) or None)
        category_rule = p.get("categories")
        if rule:
            category_rule = f"periods: {rule.get('actual_periods')} actual + {rule.get('forecast_periods')} forecast"
        chart = self.flush_add(S.ChartTemplate(spec_version_id=self.v, element_id=el_id, chart_type=p.get("chart_type") or "other",
                                               category_rule=category_rule, category_count=count, source="memo"))
        colours = (self.style.get("charts") or {}).get("series_colours") or []
        for i, x in enumerate(p.get("series") or []):
            if isinstance(x, dict):
                self.add(S.ChartTemplateSeries(chart_template_id=chart.id, position=i, concept_id=self.concept(x.get("concept")),
                                               color_ref=self.colour(colours[i]) if i < len(colours) else None,
                                               label_rule=x.get("name")))

    # --- step 5 ---------------------------------------------------------------------------------
    def rules(self) -> None:
        for r in self.rules_answer.get("rules") or []:
            params = r.get("params") or {}
            section = params.get("section")
            section = next((x for x in section if isinstance(x, str)), None) if isinstance(section, list) else section
            rule = self.flush_add(S.Rule(spec_version_id=self.v, type=r["type"], says=r.get("says") or "",
                                         section_id=self.sections.get(section) if isinstance(section, str) else None,
                                         **evidence(r)))
            for name, value in params.items():
                for v in value if isinstance(value, list) else [value]:
                    self.rule_param(rule.id, name, v)
        for u in self.rules_answer.get("unmapped") or []:
            self.add(S.Rule(spec_version_id=self.v, type="unmapped", says=u.get("says") or "", **evidence(u)))

    def rule_param(self, rule_id, name: str, value) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                self.rule_param(rule_id, f"{name}.{k}", v)
            return
        kind, ref = "text", None
        if isinstance(value, bool):
            kind = "bool"
        elif isinstance(value, (int, float)):
            kind = "number"
        elif isinstance(value, str) and value in self.sections:
            kind, ref = "section", self.sections[value]
        elif isinstance(value, str) and value in self.concepts:
            kind, ref = "concept", self.concepts[value]
        elif isinstance(value, str) and value in self.layouts:
            kind, ref = "layout", self.layouts[value]
        elif isinstance(value, str) and value.lower() in self.palette_names:
            kind = "color"
        self.add(S.RuleParam(rule_id=rule_id, name=name, value=None if value is None else str(value),
                             value_kind=kind, ref_id=ref))

    # --- conflicts and open questions for the person ---------------------------------------------
    def questions_and_conflicts(self) -> None:
        for answer in (self.style, self.structure):
            for c in answer.get("conflicts") or []:
                d = self.flush_add(S.ConflictDecision(spec_version_id=self.v, topic=c.get("topic") or "", source="onboarding"))
                for o in c.get("options") or []:
                    self.add(S.ConflictOption(decision_id=d.id, option_source=o.get("source") or "", value=str(o.get("value")),
                                              evidence=(o.get("evidence") or {}).get("quote")))
        for c in self.step3.get("conflicts") or []:
            d = self.flush_add(S.ConflictDecision(spec_version_id=self.v, source="onboarding",
                                                  topic=f"{c.get('role')}: {c.get('what')} ({', '.join(c.get('boxes') or [])})"))
            for src in ("template", "guide"):
                if c.get(src):
                    self.add(S.ConflictOption(decision_id=d.id, option_source=src, value=str(c[src])))
        for q in self.step4["answer"].get("questions") or []:   # no options yet: the person answers
            self.add(S.ConflictDecision(spec_version_id=self.v, topic=f"Question: {q}", source="onboarding"))


def write(firm: str) -> dict:
    with SessionLocal() as session:
        return SpecWriter(session, firm).run()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("firm")
    result = write(parser.parse_args().firm)
    rows = ", ".join(f"{k} {v}" for k, v in sorted(result["rows"].items()))
    sys.stdout.write(f"{result['firm']} · {result['spec']} v{result['version']}: {rows}\n")


if __name__ == "__main__":
    main()
