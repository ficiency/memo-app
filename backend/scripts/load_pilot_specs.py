"""Load what the onboarding pilot produced (docs/pilots/out/onboarding/<firm>/step_1..5.json) into the
spec tables, one approved spec version per firm.

Generic: firms are discovered from the folders; no firm, section or layout name appears here. Re-running
replaces the spec this script created for each firm (same name), so it can be run as often as needed.

Known pilot gaps are loaded as far as the pilot carries them and fixed when the onboarding is ported
(BIBLE §5, G1–G7): table style is prose (G3: no table_styles row), element colours may be raw hex instead of
palette names, table recipes are partly abstract (G5).

Usage (from backend/):  .venv/Scripts/python -m scripts.load_pilot_specs [--firm <folder>]
"""
from __future__ import annotations

import argparse
import json
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import REPO
from app.db import spec as S
from app.db.memo import Element, ElementParagraph, Segment
from app.db.session import SessionLocal

OUT = REPO / "docs" / "pilots" / "out" / "onboarding"
MANIFESTS = REPO / "docs" / "pilots" / "onboarding_pilot" / "firms"
LOCK = {"firm": "fixed", "content": "content"}            # pilot lock → firm_lock; anything else is free
STYLE_KEYS = ("font", "size_pt", "bold", "italic", "caps", "align", "anchor")
ALIGN = {"l": "left", "c": "center", "ctr": "center", "r": "right", "j": "justify", "just": "justify"}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def evidence(item: dict | None) -> dict:
    ev = (item or {}).get("evidence") or {}
    return {"source": ev.get("source"), "evidence_quote": ev.get("quote") or None, "evidence_ref": ev.get("ref")}


class Loader:
    def __init__(self, session: Session, folder: Path):
        self.s = session
        self.folder = folder
        self.steps = {n: load_json(folder / f"step_{n}.json") for n in range(1, 6)}
        self.answers = {n: self.steps[n].get("answer", self.steps[n]) for n in self.steps}
        self.counts: Counter = Counter()
        self.palette_by_hex: dict[str, str] = {}
        self.sections: dict[str, uuid.UUID] = {}
        self.layouts: dict[str, uuid.UUID] = {}
        self.boxes: dict[tuple[str, str], uuid.UUID] = {}
        self.concepts: dict[str, uuid.UUID] = {}

    # --- helpers --------------------------------------------------------------------------------
    def add(self, row, kind: str | None = None):
        self.s.add(row)
        self.counts[kind or row.__tablename__] += 1
        return row

    def color_ref(self, color) -> str | None:
        """Palette name when the hex is in the palette, else the raw hex (a known pilot gap)."""
        if not color:
            return None
        if isinstance(color, str):
            return color if not color.startswith("#") else self.palette_by_hex.get(color.upper(), color.upper())
        hex_ = (color.get("hex") or "").upper()
        return self.palette_by_hex.get(hex_, hex_ or None)

    # --- firm, user, spec -----------------------------------------------------------------------
    def firm_name(self) -> str:
        manifest = MANIFESTS / f"{self.folder.name}.json"
        return load_json(manifest)["firm"] if manifest.exists() else self.folder.name

    def run(self) -> dict:
        s1 = self.answers[1]
        name = self.firm_name()
        firm = self.s.scalar(select(S.Firm).where(S.Firm.name == name)) or self.add(S.Firm(name=name))
        self.s.flush()
        slug = self.folder.name
        for who in ("alex", "sam"):                               # two mock editors per firm
            email = f"{who}@{slug}.demo"
            if not self.s.scalar(select(S.User).where(S.User.email == email)):
                self.add(S.User(email=email, display_name=who.title(), firm_id=firm.id))

        spec_name = ((s1.get("identity") or {}).get("memo_name") or {}).get("value") or "Memo"
        old = self.s.scalars(select(S.Spec).where(S.Spec.firm_id == firm.id, S.Spec.name == spec_name)).all()
        for spec in old:                                          # replace this script's previous load
            self.s.delete(spec)
        self.s.flush()
        spec = self.add(S.Spec(firm_id=firm.id, name=spec_name))
        self.s.flush()
        page = s1.get("page") or {}
        self.version = self.add(S.SpecVersion(
            spec_id=spec.id, number=1, label="pilot import", status="approved", created_from="pilot_import",
            page_w_in=page.get("width_in"), page_h_in=page.get("height_in"),
            approved_at=datetime.now(timezone.utc)))
        self.s.flush()
        self.v = self.version.id

        self.house_style(s1)
        self.structure(self.answers[2])
        self.layouts_and_elements(self.steps[3])
        self.recipes(self.steps[4])
        self.rules(self.answers[5])
        self.conflicts()
        self.s.commit()
        return {"firm": name, "spec": spec_name, "spec_version_id": str(self.v), "rows": dict(self.counts)}

    # --- step 1 ---------------------------------------------------------------------------------
    def house_style(self, s1: dict) -> None:
        for c in s1.get("palette") or []:
            self.add(S.PaletteColor(spec_version_id=self.v, name=c["name"], hex=c["hex"].upper(),
                                    use_rule=c.get("use"), **evidence(c)))
            self.palette_by_hex[c["hex"].upper()] = c["name"]
        fonts = s1.get("fonts") or {}
        for role in ("heading", "body", "tables"):
            if fonts.get(role):
                self.add(S.Font(spec_version_id=self.v, role=role, family=fonts[role], **evidence(fonts)))
        margins = (s1.get("page") or {}).get("margins_in")
        if isinstance(margins, dict):
            self.add(S.Grid(spec_version_id=self.v, margin_top_in=margins.get("top"),
                            margin_right_in=margins.get("right"), margin_bottom_in=margins.get("bottom"),
                            margin_left_in=margins.get("left"), **evidence(s1.get("page"))))
        for r in s1.get("text_roles") or []:
            self.add(S.TextRole(spec_version_id=self.v, name=r["role"], applies_to=r.get("applies_to"),
                                font=r.get("font"), size_pt=r.get("size_pt"), min_size_pt=r.get("min_size_pt"),
                                max_size_pt=r.get("max_size_pt"), bold=r.get("bold"), italic=r.get("italic"),
                                caps=r.get("caps"), color_ref=self.color_ref(r.get("color")), **evidence(r)))
        for f in s1.get("formats") or []:
            examples = f.get("examples")
            self.add(S.Format(spec_version_id=self.v, item=f["item"], context=f.get("context") or "any",
                              rule=f["rule"], example=", ".join(examples) if isinstance(examples, list) else examples,
                              **evidence(f)))
        charts = s1.get("charts") or {}
        if charts:
            style = self.add(S.ChartStyle(
                spec_version_id=self.v, native_only=bool(charts.get("native")), legend_rule=_text(charts.get("legend")),
                title_rule=_text(charts.get("title")), forecast_treatment=_text(charts.get("forecast")),
                focus_color_ref=self.color_ref(charts.get("focus")) if _is_color(charts.get("focus")) else None,
                **evidence(charts)))
            self.s.flush()
            for i, colour in enumerate(charts.get("series_colours") or []):
                self.add(S.ChartSeriesColor(chart_style_id=style.id, position=i, color_ref=self.color_ref(colour)))
        cit = s1.get("citations") or {}
        if cit.get("scheme"):
            self.add(S.CitationScheme(spec_version_id=self.v, scheme=cit["scheme"], marker=cit.get("marker"),
                                      placement=cit.get("placement"), **evidence(cit)))
        footer = s1.get("footer") or {}
        for i, item in enumerate(footer.get("items") or []):
            self.add(S.FooterItem(spec_version_id=self.v, position=i, kind="fixed", text=item, **evidence(footer)))
        # Table conventions are prose in the pilot (G3); the ported step 1 fills table_styles.

    # --- step 2 ---------------------------------------------------------------------------------
    def structure(self, s2: dict) -> None:
        sections = s2.get("sections") or []
        for i, sec in enumerate(sections):
            count = sec.get("count") or "one"
            row = self.add(S.Section(
                spec_version_id=self.v, name=sec["name"], status=sec.get("status") or "optional",
                count="n" if str(count).isdigit() else count, count_n=int(count) if str(count).isdigit() else None,
                must_contain=sec.get("must_contain"), numbering_scheme=sec.get("numbering"), position=i,
                **evidence(sec)))
            self.s.flush()
            self.sections[sec["name"]] = row.id
        for sec in sections:
            for c in sec.get("constraints") or []:
                kind, _, arg = c.partition(":")
                target_section = self.sections.get(arg)
                self.add(S.OrderConstraint(
                    spec_version_id=self.v, section_id=self.sections[sec["name"]], kind=kind,
                    target_section_id=target_section,
                    target_status=arg if arg and not target_section and not arg.isdigit() else None,
                    position=int(arg) if arg.isdigit() else None))
        for i, st in enumerate(s2.get("stages") or []):
            stage = self.add(S.Stage(spec_version_id=self.v, name=st["name"], position=i, **evidence(st)))
            self.s.flush()
            for required, names in ((True, st.get("required") or []), (False, st.get("optional") or [])):
                for n in names:
                    if n in self.sections:
                        self.add(S.StageSection(stage_id=stage.id, section_id=self.sections[n], required=required))
        # Step 2 names layouts the guide asks for but no template has as "to_generate:<name>"; step 3 creates them.
        self._section_layout_names = {sec["name"]: [ln.removeprefix("to_generate:") for ln in sec.get("layouts") or []]
                                      for sec in sections}

    # --- step 3 ---------------------------------------------------------------------------------
    def layouts_and_elements(self, s3: dict) -> None:
        master = self.add(S.Layout(spec_version_id=self.v, name="Master", source_kind=_source(s3["master"]),
                                   is_master=True, position=0))
        self.s.flush()
        self.layouts["Master"] = master.id
        for b in s3.get("master") or []:
            self.element(master.id, "Master", b)
        for i, lay in enumerate(s3.get("layouts") or [], start=1):
            row = self.add(S.Layout(spec_version_id=self.v, name=lay["name"], source_kind=lay.get("source") or "imported",
                                    shows_master=lay.get("shows_master", True),
                                    background_ref=self.color_ref(lay.get("background")), position=i))
            self.s.flush()
            self.layouts[lay["name"]] = row.id
            for b in lay.get("boxes") or []:
                self.element(row.id, lay["name"], b)
        for sec_name, layout_names in self._section_layout_names.items():
            for ln in layout_names:
                if ln in self.layouts and sec_name in self.sections:
                    self.add(S.SectionLayout(section_id=self.sections[sec_name], layout_id=self.layouts[ln]))

    def element(self, layout_id: uuid.UUID, layout_name: str, b: dict) -> None:
        f = b.get("frame") or {}
        line = b.get("line") or {}
        if not isinstance(line, dict):                            # generated layouts give just the colour
            line = {"color": line}
        el = self.add(Element(
            layout_id=layout_id, name=b.get("name"), type=b.get("type") or "shape",
            placeholder_idx=(b.get("placeholder") or {}).get("idx"), role=b.get("role") or b.get("text_role"),
            firm_lock=LOCK.get(b.get("lock"), "free"), fill_kind="fixed" if b.get("text") else "person",
            description=b.get("prompt"), fill_ref=self.color_ref(b.get("fill")), line_ref=self.color_ref(line.get("color")),
            line_width_pt=line.get("width_pt"), x_in=f.get("x", 0), y_in=f.get("y", 0), w_in=f.get("w", 0),
            h_in=f.get("h", 0)))
        self.s.flush()
        self.boxes[(layout_name, b.get("id"))] = el.id
        style = b.get("style") or {}
        if style or b.get("text"):
            kw = {k: style.get(k) for k in STYLE_KEYS if style.get(k) is not None}
            if "align" in kw:
                kw["align"] = ALIGN.get(kw["align"], kw["align"])
            for flag in ("bold", "italic", "caps"):               # PowerPoint writes caps as "all" / "small"
                if flag in kw:
                    kw[flag] = kw[flag] in (True, "all", "1", "true")
            para = self.add(ElementParagraph(element_id=el.id, position=0, color_ref=self.color_ref(style.get("color")), **kw))
            self.s.flush()
            if b.get("text"):
                for i, line_text in enumerate(str(b["text"]).split("\n")):
                    if i:                                         # keep line breaks as paragraphs (G1)
                        para = self.add(ElementParagraph(element_id=el.id, position=i, **kw,
                                                         color_ref=self.color_ref(style.get("color"))))
                        self.s.flush()
                    self.add(Segment(paragraph_id=para.id, position=0, kind="text", text=line_text))

    # --- step 4 ---------------------------------------------------------------------------------
    def recipes(self, s4: dict) -> None:
        for name, c in (s4.get("catalogue") or {}).items():
            row = self.add(S.Concept(spec_version_id=self.v, name=name, kind=c.get("type") or "figure",
                                     value_kind=c.get("kind"), source="memo"))
            self.s.flush()
            self.concepts[name] = row.id
        for r in (s4.get("answer") or {}).get("recipes") or []:
            el_id = self.boxes.get((r["layout"], r["box_id"]))
            if not el_id:
                self.counts["recipe_without_box"] += 1
                continue
            for pos, p in enumerate(r.get("pattern") or []):
                self.concept_for(p.get("concept"), p.get("type"))
                seg = self.add(S.RecipeSegment(
                    spec_version_id=self.v, element_id=el_id, position=pos, kind=p["type"], text=p.get("text"),
                    concept_id=self.concepts.get(p.get("concept")), when_rule=_text(p.get("when")),
                    what=_text(p.get("what")) or _text(p.get("columns")), seen_in=p.get("seen_in_counted"),
                    memos_with_box=p.get("memos_with_box"), source="memo"))
                self.s.flush()
                for ex in _examples(p.get("style_examples")):
                    self.add(S.StyleExample(recipe_segment_id=seg.id, memo_name=ex[0], text=ex[1]))
                for c in p.get("must_include") or []:
                    if self.concept_for(c, "figure"):
                        self.add(S.RecipeMustInclude(recipe_segment_id=seg.id, concept_id=self.concepts[c]))
                if p["type"] == "table":
                    self.table_template(el_id, p)

    def concept_for(self, name, kind) -> uuid.UUID | None:
        if not name or not isinstance(name, str):
            return None
        if name not in self.concepts:
            row = self.add(S.Concept(spec_version_id=self.v, name=name, kind=kind if kind in ("figure", "fact", "memo_data") else "figure",
                                     source="memo"))
            self.s.flush()
            self.concepts[name] = row.id
        return self.concepts[name]

    def table_template(self, el_id: uuid.UUID, p: dict) -> None:
        t = self.add(S.TableTemplate(spec_version_id=self.v, element_id=el_id, source="memo"))
        self.s.flush()
        for i, h in enumerate(p.get("header") or []):
            self.add(S.TableTemplateColumn(table_template_id=t.id, position=i, header_text=h,
                                           kind="label" if i == 0 else "data"))
        for i, row in enumerate(p.get("rows") or []):
            if not isinstance(row, dict):
                continue
            self.add(S.TableTemplateRow(table_template_id=t.id, position=i, kind="data", label=row.get("label"),
                                        concept_id=self.concept_for(row.get("concept"), row.get("cells"))))

    # --- step 5 ---------------------------------------------------------------------------------
    def rules(self, s5: dict) -> None:
        for r in s5.get("rules") or []:
            params = r.get("params") or {}
            rule = self.add(S.Rule(spec_version_id=self.v, type=r["type"], says=r.get("says") or "",
                                   section_id=self.sections.get(params.get("section")), **evidence(r)))
            self.s.flush()
            for name, value in params.items():
                for v in value if isinstance(value, list) else [value]:
                    self.rule_param(rule.id, name, v)
        for u in s5.get("unmapped") or []:
            self.add(S.Rule(spec_version_id=self.v, type="unmapped", says=u.get("says") or "", **evidence(u)))

    def rule_param(self, rule_id: uuid.UUID, name: str, value) -> None:
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
        elif isinstance(value, str) and value in self.palette_by_hex.values():
            kind = "color"
        self.add(S.RuleParam(rule_id=rule_id, name=name, value=None if value is None else str(value),
                             value_kind=kind, ref_id=ref))

    # --- conflicts from steps 1–3 ---------------------------------------------------------------
    def conflicts(self) -> None:
        for n in (1, 2):
            for c in self.answers[n].get("conflicts") or []:
                d = self.add(S.ConflictDecision(spec_version_id=self.v, topic=c.get("topic") or "", source="pilot"))
                self.s.flush()
                for o in c.get("options") or []:
                    self.add(S.ConflictOption(decision_id=d.id, option_source=o.get("source") or "",
                                              value=str(o.get("value")), evidence=(o.get("evidence") or {}).get("quote")))
        for c in self.steps[3].get("conflicts") or []:
            topic = f"{c.get('role')}: {c.get('what')} ({', '.join(c.get('boxes') or [])})"
            d = self.add(S.ConflictDecision(spec_version_id=self.v, topic=topic, source="pilot"))
            self.s.flush()
            for src in ("template", "guide"):
                if c.get(src):
                    self.add(S.ConflictOption(decision_id=d.id, option_source=src, value=str(c[src])))


def _text(v) -> str | None:
    if v is None:
        return None
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)


def _is_color(v) -> bool:
    return isinstance(v, str) and (v.startswith("#") or (len(v) < 30 and " " not in v.strip(" ")))


def _source(boxes: list) -> str:
    kinds = {b.get("source") for b in boxes}
    return "generated" if kinds == {"generated"} else "imported"


def _examples(v) -> list[tuple[str | None, str]]:
    out = []
    for item in v if isinstance(v, list) else [v] if v else []:
        if isinstance(item, dict):
            out += [(k, str(t)) for k, t in item.items()]
        elif isinstance(item, str):
            out.append((None, item))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--firm", help="one firm folder under docs/pilots/out/onboarding")
    args = parser.parse_args()
    folders = sorted(p for p in OUT.iterdir() if p.is_dir() and (p / "step_5.json").exists())
    if args.firm:
        folders = [p for p in folders if p.name == args.firm]
    for folder in folders:
        with SessionLocal() as session:
            result = Loader(session, folder).run()
        rows = result["rows"]
        print(f"{result['firm']} · {result['spec']}: " + ", ".join(f"{k} {v}" for k, v in sorted(rows.items())))


if __name__ == "__main__":
    main()
