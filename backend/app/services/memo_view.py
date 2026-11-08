"""The memo as the canvas receives it: the memo's slides and elements (app/services/memo_data) plus what the
canvas needs from the spec to paint them (page, palette, text roles, fonts, table and chart style, grid,
sections). The canvas resolves role → font, size and colour, and palette name → hex, from this."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import spec as S
from app.db.memo import Element
from app.db.record import Deal, RecordRevision
from app.services.memo_data import ElementData, MemoData, group

ROLE = ("font", "size_pt", "min_size_pt", "bold", "italic", "caps", "color_ref")
TABLE = ("header_fill_ref", "header_text_ref", "header_bold", "forecast_header_fill_ref", "body_text_ref", "banding_ref",
         "rule_color_ref", "rule_width_pt", "header_rule_width_pt", "total_bold", "highlight_color_ref", "highlight_bold",
         "number_align", "min_size_pt")


def spec_view(session: Session, version_id) -> dict:
    q = session.scalars
    v = session.get(S.SpecVersion, version_id)
    table = session.scalar(select(S.TableStyle).where(S.TableStyle.spec_version_id == v.id))
    chart = session.scalar(select(S.ChartStyle).where(S.ChartStyle.spec_version_id == v.id))
    grid = session.scalar(select(S.Grid).where(S.Grid.spec_version_id == v.id))
    series_colors = [c.color_ref for c in q(select(S.ChartSeriesColor).where(S.ChartSeriesColor.chart_style_id == chart.id)
                                             .order_by(S.ChartSeriesColor.position))] if chart else []
    roles = q(select(S.TextRole).where(S.TextRole.spec_version_id == v.id)).all()
    return {
        "version": {"id": str(v.id), "number": v.number, "spec_id": str(v.spec_id)},
        "page": {"w_in": v.page_w_in, "h_in": v.page_h_in},
        "palette": {p.name: p.hex for p in q(select(S.PaletteColor).where(S.PaletteColor.spec_version_id == v.id))},
        "roles": {r.name: {k: getattr(r, k) for k in ROLE} for r in roles},
        "fonts": {f.role: f.substitute_family or f.family for f in q(select(S.Font).where(S.Font.spec_version_id == v.id))},
        "table_style": {k: getattr(table, k) for k in TABLE} if table else None,
        "chart_style": {"series_colors": series_colors, "labels_on": chart.labels_on if chart else None,
                        "gridlines": chart.gridlines if chart else None, "focus_color": chart.focus_color_ref if chart else None},
        "grid": {k: getattr(grid, k) for k in ("margin_top_in", "margin_right_in", "margin_bottom_in", "margin_left_in",
                                                "columns", "gutter_in", "snap_in")} if grid else None,
        "sections": {str(x.id): {"name": x.name, "status": x.status}
                     for x in q(select(S.Section).where(S.Section.spec_version_id == v.id))},
    }


def memo_view(session: Session, memo_id) -> dict:
    d = MemoData(session, memo_id)
    m = d.memo
    revision = session.get(RecordRevision, m.record_revision_id)
    return {
        "memo": {"id": str(m.id), "title": m.title, "status": m.status, "sequence": m.sequence or 0,
                 "deal": session.get(Deal, m.deal_id).name, "stage": d.stage_name, "memo_date": m.memo_date,
                 "record_revision": {"id": str(revision.id), "number": revision.number}},
        "spec": spec_view(session, m.spec_version_id),
        "master": [d.element_json(e, master_box=True) for e in sorted(d.master_elements, key=lambda e: e.z_index or 0)],
        "slides": [d.slide_json(s) for s in d.slides],
    }


# --- a spec's layouts, painted by the canvas (onboarding review) ------------------------------------------
RECIPE_KIND = {"fixed": "text", "memo_data": "text", "figure": "chip", "fact": "chip", "model_writes": "model_draft",
               "person_writes": "person_slot", "conditional": "conditional"}


def layouts_view(session: Session, version_id) -> dict:
    """Every layout of a spec version as a sample slide: each box shows its recipe (fixed words, figures as
    chips, what the model or a person writes), so what is approved is what memos will look like."""
    q = session.scalars
    layouts = q(select(S.Layout).where(S.Layout.spec_version_id == version_id).order_by(S.Layout.position)).all()
    master = next((l for l in layouts if l.is_master), None)
    elements = q(select(Element).where(Element.layout_id.in_([l.id for l in layouts]))).all()
    data = ElementData(session, elements)
    recipe = group(q(select(S.RecipeSegment).where(S.RecipeSegment.element_id.in_([e.id for e in elements]))).all(), "element_id")
    concept_rows = q(select(S.Concept).where(S.Concept.spec_version_id == version_id)).all()
    concepts = {c.id: c.name for c in concept_rows}
    kinds = {c.id: c.value_kind for c in concept_rows}
    # a figure is shown as the firm writes that kind of value (its own example), so the sample reads like a memo
    examples: dict = {}
    for f in q(select(S.Format).where(S.Format.spec_version_id == version_id, S.Format.example.isnot(None))):
        if f.context in ("any", "prose") or f.item not in examples:
            examples[f.item] = f.example.split(", ")[0]
    sections: dict = {}
    for link, name in session.execute(select(S.SectionLayout.layout_id, S.Section.name)
                                      .join(S.Section, S.Section.id == S.SectionLayout.section_id)
                                      .where(S.Section.spec_version_id == version_id)):
        sections.setdefault(link, []).append(name)

    def paragraphs(el: Element, styles: list[dict]) -> list[dict]:
        out: dict[int, list[dict]] = {}
        for r in sorted(recipe.get(el.id, []), key=lambda r: (r.paragraph or 0, r.position)):
            kind = RECIPE_KIND.get(r.kind)
            if kind is None:                                  # a table or chart: the box itself says so
                continue
            concept = concepts.get(r.concept_id)
            if r.kind in ("figure", "fact"):
                kind = kinds.get(r.concept_id)
                text = r.text or examples.get(kind or "") or                     ("12.3" if r.kind == "figure" and kind not in ("text", "date") else (concept or "figure").replace("_", " "))
                kind = "chip"
            elif r.kind == "memo_data":
                text = f"‹{(concept or r.text or 'memo data').replace('_', ' ')}›"
            elif r.kind == "fixed":
                text = r.text or ""
            else:
                who = "model" if r.kind == "model_writes" else "person" if r.kind == "person_writes" else "if"
                text = f"‹{who}: {r.what or r.text or r.when_rule or ''}›"
            out.setdefault(r.paragraph or 0, []).append({"kind": kind, "text": text, "concept": concept, "path": None})
        return [{**(styles[min(i, len(styles) - 1)] if styles else {"level": 0, "role": None, "style": {}}), "segments": segs}
                for i, (_, segs) in enumerate(sorted(out.items()))]

    def element(el: Element, master_box: bool = False) -> dict:
        out = data.element_json(el, master_box=master_box)
        out["slide_id"] = str(el.layout_id)
        if recipe.get(el.id):
            out["paragraphs"] = paragraphs(el, out["paragraphs"]) or out["paragraphs"]
        return out

    order = lambda e: (e.z_index or 0, e.y_in or 0, e.x_in or 0)
    by_layout = group(elements, "layout_id")
    return {
        "spec": spec_view(session, version_id),
        "master": [element(e, master_box=True) for e in sorted(by_layout.get(master.id, []), key=order)] if master else [],
        "layouts": [{"id": str(l.id), "position": i, "kind": "layout", "revision": 0, "section_id": None, "layout": l.name,
                     "background": l.background_ref, "shows_master": bool(master) and l.shows_master,
                     "source": l.source_kind, "blank": l.is_blank, "sections": sections.get(l.id, []),
                     "elements": [element(e) for e in sorted(by_layout.get(l.id, []), key=order)]}
                    for i, l in enumerate(x for x in layouts if not x.is_master)],
    }
