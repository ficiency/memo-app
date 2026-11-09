"""Run the evals that exist so far and write a report (docs/EVALS.md).

Each check is unlocked by a build step (BIBLE §6) and runs from then on. Firms are discovered from the
database and the pilot outputs; no firm name appears in this file.

Usage (from the repo root):  backend/.venv/Scripts/python evals/run.py
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from sqlalchemy import func, select  # noqa: E402

from app.db import spec as S  # noqa: E402
from app.db.memo import Element  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402

ONBOARDING_OUT = REPO / "backend" / ".work" / "onboarding"      # approved step results the writer puts in the DB
MANIFESTS = REPO / "backend" / "onboarding_firms"
CODE_DIRS = [REPO / "backend" / "app", REPO / "backend" / "scripts", REPO / "evals"]


def schema_fits() -> tuple[list[str], list[dict]]:
    """Every item the onboarding produced for each firm has a row in its latest spec version."""
    lines, failures = [], []
    with SessionLocal() as s:
        for folder in sorted(p for p in ONBOARDING_OUT.iterdir() if p.is_dir() and (p / "step_5.json").exists()):
            step = {n: json.loads((folder / f"step_{n}.json").read_text(encoding="utf-8")) for n in range(1, 6)}
            a = {n: step[n].get("answer", step[n]) for n in step}
            name = json.loads((MANIFESTS / f"{folder.name}.json").read_text(encoding="utf-8"))["firm"]
            firm = s.scalar(select(S.Firm).where(S.Firm.name == name))
            version = firm and s.scalar(select(S.SpecVersion).join(S.Spec).where(S.Spec.firm_id == firm.id)
                                        .order_by(S.SpecVersion.created_at.desc()))
            if not version:
                failures.append({"firm": name, "what": "no spec version loaded"})
                continue
            v = version.id

            def count(model, *where):
                return s.scalar(select(func.count()).select_from(model).where(*where)) or 0

            layout_ids = select(S.Layout.id).where(S.Layout.spec_version_id == v)
            expected = {
                "palette colours": (len(a[1].get("palette") or []), count(S.PaletteColor, S.PaletteColor.spec_version_id == v)),
                "text roles": (len(a[1].get("text_roles") or []), count(S.TextRole, S.TextRole.spec_version_id == v)),
                "formats": (len(a[1].get("formats") or []), count(S.Format, S.Format.spec_version_id == v)),
                "sections": (len(a[2].get("sections") or []), count(S.Section, S.Section.spec_version_id == v)),
                "stages": (len(a[2].get("stages") or []), count(S.Stage, S.Stage.spec_version_id == v)),
                "layouts (+ master)": (len(step[3].get("layouts") or []) + 1, count(S.Layout, S.Layout.spec_version_id == v)),
                "layout elements": (len(step[3].get("master") or []) + sum(len(l.get("boxes") or []) for l in step[3].get("layouts") or []),
                                    count(Element, Element.layout_id.in_(layout_ids))),
                "sections with a layout": (sum(1 for x in a[2].get("sections") or [] if x.get("layouts")),
                                           s.scalar(select(func.count(func.distinct(S.SectionLayout.section_id)))
                                                    .where(S.SectionLayout.layout_id.in_(layout_ids))) or 0),
                "recipe segments": (sum(len(r.get("pattern") or []) for r in (a[4].get("recipes") or [])),
                                    count(S.RecipeSegment, S.RecipeSegment.spec_version_id == v)),
                "rules (+ unmapped)": (len(a[5].get("rules") or []) + len(a[5].get("unmapped") or []),
                                       count(S.Rule, S.Rule.spec_version_id == v)),
            }
            lines.append(f"\n**{name}** (spec version {version.number}, {version.label})\n\n| Item | Onboarding | In DB | |\n|---|---|---|---|")
            for item, (want, got) in expected.items():
                ok = want == got
                lines.append(f"| {item} | {want} | {got} | {'✅' if ok else '❌'} |")
                if not ok:
                    failures.append({"firm": name, "what": f"{item}: onboarding {want}, DB {got}"})
    return lines, failures


def no_firm_names_in_code() -> tuple[list[str], list[dict]]:
    """Configuration, not code: no firm name in the engine, the loaders or the evals."""
    names = set()
    for m in MANIFESTS.glob("*.json"):
        firm = json.loads(m.read_text(encoding="utf-8"))["firm"]
        names |= {w.lower() for w in re.findall(r"[A-Za-z]{5,}", firm)} | {m.stem.lower()}
    names -= {"partners", "credit", "capital"}                   # generic words in firm names
    hits = []
    for d in CODE_DIRS:
        for f in d.rglob("*.py"):
            text = f.read_text(encoding="utf-8").lower()
            hits += [f"{f.relative_to(REPO)}: '{n}'" for n in sorted(names) if re.search(rf"\b{n}\b", text)]
    lines = [f"Words checked: {', '.join(sorted(names))}. Hits: {len(hits)}."] + [f"- {h}" for h in hits]
    return lines, [{"firm": "-", "what": h} for h in hits]


def spec_completeness() -> tuple[list[str], list[dict]]:
    """How much of what painters need (gaps G1–G7, BIBLE §5) each firm's latest spec carries. A measure,
    not pass/fail: it shows the onboarding fixes landing."""
    from app.db.memo import ElementParagraph

    rows = []
    with SessionLocal() as s:
        for firm in s.scalars(select(S.Firm).order_by(S.Firm.name)):
            version = s.scalar(select(S.SpecVersion).join(S.Spec).where(S.Spec.firm_id == firm.id)
                               .order_by(S.SpecVersion.created_at.desc()))
            if not version:
                continue
            v = version.id
            layouts = select(S.Layout.id).where(S.Layout.spec_version_id == v)
            master = select(S.Layout.id).where(S.Layout.spec_version_id == v, S.Layout.is_master)
            templates = select(S.TableTemplate.id).where(S.TableTemplate.spec_version_id == v)

            def n(q):
                return s.scalar(q) or 0

            multi_style = n(select(func.count()).select_from(
                select(ElementParagraph.element_id).join(Element, Element.id == ElementParagraph.element_id)
                .where(Element.layout_id.in_(layouts)).group_by(ElementParagraph.element_id)
                .having(func.count(func.distinct(ElementParagraph.size_pt)) > 1).subquery()))
            master_text = select(Element.id).where(Element.layout_id.in_(master), Element.type == "text")
            # a master text box has content when it has a recipe or fixed text (a paragraph with a segment)
            from app.db.memo import Segment
            with_recipe = select(S.RecipeSegment.element_id).where(S.RecipeSegment.element_id.in_(master_text))
            with_text = (select(ElementParagraph.element_id).join(Segment, Segment.paragraph_id == ElementParagraph.id)
                         .where(ElementParagraph.element_id.in_(master_text)))
            master_content = n(select(func.count()).select_from(with_recipe.union(with_text).subquery()))
            cols = n(select(func.count()).where(S.TableTemplateColumn.table_template_id.in_(templates)))
            cols_w = n(select(func.count()).where(S.TableTemplateColumn.table_template_id.in_(templates),
                                                  S.TableTemplateColumn.width_in.is_not(None)))
            trows = n(select(func.count()).where(S.TableTemplateRow.table_template_id.in_(templates)))
            trows_c = n(select(func.count()).where(S.TableTemplateRow.table_template_id.in_(templates),
                                                   S.TableTemplateRow.concept_id.is_not(None)))
            rows.append((firm.name, {
                "G1 elements with several paragraph styles": multi_style,
                "G2 layouts with a background": f"{n(select(func.count()).where(S.Layout.spec_version_id == v, S.Layout.background_ref.is_not(None)))}"
                                                f" / {n(select(func.count()).where(S.Layout.spec_version_id == v))}",
                "G3 table styles as fields": n(select(func.count()).where(S.TableStyle.spec_version_id == v)),
                "G4 table columns with a width": f"{cols_w} / {cols}",
                "G4 table cells with fixed text or fill": n(select(func.count()).where(S.TableTemplateCell.table_template_id.in_(templates))),
                "G5 table rows bound to a concept": f"{trows_c} / {trows}",
                "G5 table column rules": n(select(func.count()).where(S.TableColumnRule.table_template_id.in_(templates))),
                "G6 chart templates (chart boxes)": f"{n(select(func.count()).where(S.ChartTemplate.spec_version_id == v))}"
                                                    f" ({n(select(func.count()).where(Element.layout_id.in_(layouts), Element.type == 'chart'))})",
                "G7 master text boxes with content": f"{master_content} / {n(select(func.count()).select_from(master_text.subquery()))}",
            }))
    if not rows:
        return ["No spec loaded."], []
    keys = list(rows[0][1])
    lines = ["", "| Measure | " + " | ".join(r[0] for r in rows) + " |", "|---|" + "---|" * len(rows)]
    lines += [f"| {k} | " + " | ".join(str(r[1][k]) for r in rows) + " |" for k in keys]
    return lines, []


def third_firm() -> tuple[list[str], list[dict]]:
    """Measure 7: the firm that came with only a guide (no PowerPoint template, no past memos), as configuration and
    as code. Configuration: what onboarding wrote for it (its spec's rows). Code: none of it names the firm (the
    check above); the generic paths only such a firm takes, counted in its spec, with what each one surfaced."""
    from app.db.memo import ElementParagraph, TableColumn
    from app.services.fit import _font
    from app.services.painters.pptx import materials
    lines, failures = [], []
    with SessionLocal() as s:
        for firm in s.scalars(select(S.Firm).order_by(S.Firm.name)):
            got = materials(firm.name)
            if got.get("template") or got.get("memo"):
                continue                                        # a firm with a PowerPoint template or past memos
            v = s.scalars(select(S.SpecVersion).join(S.Spec).where(S.Spec.firm_id == firm.id).order_by(S.SpecVersion.number.desc())).first()
            layouts = select(S.Layout.id).where(S.Layout.spec_version_id == v.id)

            def n(model, *where):
                return s.scalar(select(func.count()).select_from(model).where(*where)) or 0

            config = {
                "layouts (one generated as its master)": n(S.Layout, S.Layout.spec_version_id == v.id),
                "boxes on its layouts": n(Element, Element.layout_id.in_(layouts)),
                "text roles": n(S.TextRole, S.TextRole.spec_version_id == v.id),
                "number and date formats": n(S.Format, S.Format.spec_version_id == v.id),
                "palette colours": n(S.PaletteColor, S.PaletteColor.spec_version_id == v.id),
                "sections and stages": n(S.Section, S.Section.spec_version_id == v.id) + n(S.Stage, S.Stage.spec_version_id == v.id),
                "order rules and rules": n(S.OrderConstraint, S.OrderConstraint.spec_version_id == v.id) + n(S.Rule, S.Rule.spec_version_id == v.id),
                "recipe segments (what each box holds)": n(S.RecipeSegment, S.RecipeSegment.spec_version_id == v.id),
                "concepts (its figures)": n(S.Concept, S.Concept.spec_version_id == v.id),
                "table templates": n(S.TableTemplate, S.TableTemplate.spec_version_id == v.id),
                "free areas (where people add boxes)": n(S.LayoutZone, S.LayoutZone.layout_id.in_(layouts)),
            }
            tables = s.scalars(select(S.TableTemplate).where(S.TableTemplate.spec_version_id == v.id)).all()
            no_columns = sum(1 for t in tables if not n(S.TableTemplateColumn, S.TableTemplateColumn.table_template_id == t.id))
            fonts = {f.family for f in s.scalars(select(S.Font).where(S.Font.spec_version_id == v.id))}
            missing = sorted(f for f in fonts if not _font(f, False, False)[1])
            paths = [
                ("its master generated from the guide (no PowerPoint to take it from)",
                 n(S.Layout, S.Layout.spec_version_id == v.id, S.Layout.is_master), "—"),
                ("boxes drawn, not placeholders (the painter places every one; no template insets)",
                 n(Element, Element.layout_id.in_(layouts), Element.placeholder_idx.is_(None)), "—"),
                ("tables with no column definitions (measured and drawn with the columns their cells use)", no_columns, "#36, found by measure 3"),
                ("fonts not installed here (measured with a stand-in, flagged approximate)", len(missing), ", ".join(missing) or "—"),
            ]
            lines += [f"\n**{firm.name}** (spec version {v.number}: a guide only, no PowerPoint template, no past memos)", "",
                      "Configuration: what onboarding wrote for it, with no code of its own.", "", "| Spec item | Rows |", "|---|---|"]
            lines += [f"| {k} | {val} |" for k, val in config.items()]
            lines += ["", "Code: no line names the firm (the check above). The generic paths only a guide-only firm takes:", "",
                      "| Generic path | In its spec | What it surfaced |", "|---|---|---|"]
            lines += [f"| {k} | {val} | {note} |" for k, val, note in paths]
    return lines or ["Every firm came with a PowerPoint template."], failures


CHECKS = [
    ("Onboarding → DB · every item lands in the tables, no special cases", schema_fits),
    ("Spec completeness · what painters need (G1–G7)", spec_completeness),
    ("Configuration vs code · no firm names in code", no_firm_names_in_code),
    ("Measure 7 · the third firm: configuration vs code", third_firm),
]


def main() -> None:
    stamp = datetime.now()
    out = [f"# Eval report · {stamp:%Y-%m-%d %H:%M}", ""]
    breaks = []
    for title, check in CHECKS:
        lines, failures = check()
        out += [f"## {title}", *lines, ""]
        breaks += [{"check": title, **f} for f in failures]
    out += ["## Where it breaks", ""]
    out += [f"- {b['check']} · {b['firm']} · {b['what']}" for b in breaks] or ["Nothing failed."]
    reports = REPO / "evals" / "reports"
    reports.mkdir(exist_ok=True)
    path = reports / f"{stamp:%Y-%m-%d_%H%M}.md"
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))
    print(f"\nreport: {path.relative_to(REPO)}")


if __name__ == "__main__":
    main()
