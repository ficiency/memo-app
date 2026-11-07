"""Step 1: house style. Page, palette and what each colour is for, fonts, text styles by role,
number and date formats, citations and footer.

Input package (only what exists):
    guide       full text in reading order + page images
    design file theme, master default text styles and every text look in use (template, or else a memo)
    memos       numbers as they were written (only when there is no guide, to infer formats)
Code then checks the answer; failures go back to the model (llm.call_with_checks).

Usage (from docs/pilots):
    python -m onboarding_pilot.step1_house_style <firm>
"""
from __future__ import annotations

import argparse
import re
import sys

from onboarding_pilot import slices
from onboarding_pilot.common import load_step, save_result, save_step
from onboarding_pilot.checks import guide_quote_errors
from onboarding_pilot.llm import call_with_checks

CITATION_SCHEMES = ("per_slide_letters", "per_slide_numbered_notes", "global_numbered_references", "none", "other")
FORMAT_ITEMS = ("money", "negative", "percent", "multiple", "date", "period", "count", "currency", "per_share", "other")

INSTRUCTIONS = f"""You set up a firm's house style for its investment memos, from the materials it sent.
The materials are listed at the top; some may be missing. Use what is there and say where each
answer comes from.

Return JSON only, with this shape (angle brackets describe what goes there):
{{
 "identity": {{"memo_name": {{"value": "<what the firm calls its memo>", "evidence": <E>}},
              "stages": [{{"name": "<stage name as written>", "evidence": <E>}}]}},
 "page": {{"width_in": <number>, "height_in": <number>,
          "margins_in": {{"left": <n>, "right": <n>, "top": <n>, "bottom": <n>}} or null, "evidence": <E>}},
 "palette": [{{"name": "<the firm's name for the colour, lower case>", "hex": "#RRGGBB",
              "use": "<what the firm says this colour is for, including any restriction>", "evidence": <E>}}],
 "fonts": {{"heading": "<font>", "body": "<font>", "tables": "<font or null>", "evidence": <E>}},
 "text_roles": [{{"role": "<snake_case name of a kind of text>", "applies_to": "<which text this is>",
                 "font": "<font>", "size_pt": <usual size>, "min_size_pt": <number or null>, "max_size_pt": <number or null>,
                 "bold": <true/false>, "caps": <true/false>, "color": "<a palette name>", "evidence": <E>}}],
 "formats": [{{"item": "<one of {', '.join(FORMAT_ITEMS)}>", "context": "<prose|table|chart|any>",
              "rule": "<the rule in one line>", "examples": ["<examples as the firm writes them>"], "evidence": <E>}}],
 "tables": {{"header": "<how header rows look: fill, text colour, weight, rules>", "body": "<body rows: banding, row rules>",
            "borders": "<borders and rules>", "alignment": "<how numbers and text are aligned>",
            "totals": "<how total rows look, or null>", "highlight": "<how a cell is emphasised, or null>",
            "min_size_pt": <number or null>, "overflow": "<what to do when a table does not fit, or null>", "evidence": <E>}},
 "charts": {{"native": "<whether charts must be native and editable>", "series_colours": ["<palette names, in order>"],
            "forecast": "<how forecast periods are shown, or null>", "focus": "<how a focus series or point is shown, or null>",
            "labels": "<data labels: on or off, size>", "gridlines": "<on or off>", "legend": "<when shown>",
            "title": "<whether charts carry a title>", "evidence": <E>}},
 "citations": {{"scheme": "<one of {', '.join(CITATION_SCHEMES)}>", "marker": "<how a reference is marked>",
               "placement": "<where sources and notes go>", "evidence": <E>}},
 "footer": {{"items": ["<footer items, left to right>"], "slide_number": "<where and how, or none>", "evidence": <E>}},
 "conflicts": [{{"topic": "<what differs>", "options": [{{"source": "<guide|template|memo>", "value": "<what it says>",
                "evidence": <E>}}], "recommendation": "<which option and why in one line>"}}]
}}
<E> is {{"source": "<guide|template|memo|assumed>", "quote": "<exact words from the guide, when source is guide>",
        "ref": "<where in the PowerPoint, when source is template or memo>"}}.

Rules:
- The slide size is the size of the firm's slides. A guide is a document to read: its own page size is
  not the slide size unless the guide says it is.
- Sizes: size_pt is the usual size. When the materials allow a range or a minimum ("10 to 14 pt",
  "never below 9pt", "11pt in dense layouts"), put its ends in min_size_pt and max_size_pt.
- Fill every field the materials state. Margins: when a material states them (in words or as
  measurements), fill margins_in, converting units to inches; leave null only when nothing states them.
- Read the guide from its page images first: they show its tables, colours and emphasis as the firm
  laid them out. The guide text that comes with them was extracted from the PDF and can be out of order;
  use it to copy exact values (hex codes, sizes, examples) and to quote, never to understand the layout.
- Every colour used in the design file must be either in the palette or explained in a conflict.
- When two materials say different things, add a conflict with each version; do not choose silently.
- Text roles: cover the kinds of text the materials define (titles, body, tables, notes, footer, labels,
  key figures...). Use the text looks in use to see what is really there.
- Where nothing in the materials says it, use source "assumed" and keep it short.
"""


def make_validate(guide: str | None, used_colours: set[str]):
    def validate(answer: dict) -> list[str]:
        errors = []
        palette = answer.get("palette") or []
        names = [str(p.get("name", "")).lower() for p in palette]
        if not palette:
            errors.append("palette is empty")
        if len(names) != len(set(names)):
            errors.append("palette names must be unique")
        hexes = set()
        for p in palette:
            if not re.fullmatch(r"#[0-9A-Fa-f]{6}", str(p.get("hex", ""))):
                errors.append(f"palette colour {p.get('name')!r} has an invalid hex {p.get('hex')!r}")
            else:
                hexes.add(p["hex"].upper())
        for role in answer.get("text_roles") or []:
            if str(role.get("color", "")).lower() not in names:
                errors.append(f"text role {role.get('role')!r} uses colour {role.get('color')!r}, which is not a palette name")
            if not isinstance(role.get("size_pt"), (int, float)) or role["size_pt"] <= 0:
                errors.append(f"text role {role.get('role')!r} needs a positive size_pt")
            elif (isinstance(role.get("min_size_pt"), (int, float)) and role["min_size_pt"] > role["size_pt"]) or                     (isinstance(role.get("max_size_pt"), (int, float)) and role["max_size_pt"] < role["size_pt"]):
                errors.append(f"text role {role.get('role')!r}: size_pt must lie between min_size_pt and max_size_pt")
        conflict_text = " ".join(str(c) for c in answer.get("conflicts") or []).upper()
        for hexcode in sorted(used_colours - hexes):
            if hexcode not in conflict_text:
                errors.append(f"colour {hexcode} is used in the design file but is neither in the palette nor in a conflict")
        for colour in (answer.get("charts") or {}).get("series_colours") or []:
            if str(colour).lower() not in names:
                errors.append(f"charts.series_colours uses {colour!r}, which is not a palette name")
        scheme = (answer.get("citations") or {}).get("scheme")
        if scheme not in CITATION_SCHEMES:
            errors.append(f"citations.scheme must be one of {CITATION_SCHEMES}, got {scheme!r}")
        for f in answer.get("formats") or []:
            if f.get("item") not in FORMAT_ITEMS:
                errors.append(f"format item {f.get('item')!r} is not one of {FORMAT_ITEMS}")
            if not f.get("examples"):
                errors.append(f"format {f.get('item')}/{f.get('context')} has no examples")
        page = answer.get("page") or {}
        if not all(isinstance(page.get(k), (int, float)) and page[k] > 0 for k in ("width_in", "height_in")):
            errors.append("page needs positive width_in and height_in")
        # Values read from a page image can lose a digit; they must appear in the guide text as written.
        if guide:
            raw_guide = guide.upper()
            for p in palette:
                hexcode = str(p.get("hex", "")).upper()
                if (p.get("evidence") or {}).get("source") == "guide" and hexcode and hexcode not in raw_guide:
                    errors.append(f"palette colour {p.get('name')!r}: {hexcode} does not appear in the guide text")
            for role in answer.get("text_roles") or []:
                if (role.get("evidence") or {}).get("source") != "guide":
                    continue
                for key in ("size_pt", "min_size_pt", "max_size_pt"):
                    value = role.get(key)
                    if isinstance(value, (int, float)) and f"{value:g}pt" not in guide.replace(" pt", "pt") \
                            and f"{value:g} pt" not in guide:
                        errors.append(f"text role {role.get('role')!r}: {key} {value:g}pt does not appear in the guide text")
        errors += guide_quote_errors(answer, guide)
        return errors

    return validate


def build_package(firm: str) -> tuple[str, list, set[str]]:
    step0 = load_step(firm, 0)
    present = sorted({m["kind"] for m in step0["materials"]})
    parts = [f"MATERIALS SENT: {', '.join(present)}. MISSING: {', '.join(step0['missing']) or 'nothing'}."]
    guide = slices.guide_text(firm)
    if guide:
        parts.append(f"=== GUIDE (text; page images follow)\n{guide}")
    material, data = slices.design_source(firm)
    used: set[str] = set()
    if data:
        parts.append(f"=== DESIGN FILE ({material['kind']}: {material['file']})\n{slices.theme_and_styles(data)}")
        parts.append(f"=== TEXT LOOKS IN USE in the design file\n{slices.style_inventory(data)}")
        counts = slices.colours_used(data)
        used = set(counts)
        parts.append("=== COLOURS USED in the design file (hex: times used)\n"
                     + ", ".join(f"{h}: {n}" for h, n in counts.most_common()))
    if not guide and slices.materials(firm, "memo"):
        parts.append(f"=== NUMBERS AS WRITTEN in the past memos\n{slices.number_samples(firm)}")
    return "\n\n".join(parts), slices.guide_images(firm), used


def review(firm: str, result: dict) -> str:
    a = result["answer"]
    out = [f"STEP 1 · House style for {firm}", ""]
    ident = a.get("identity") or {}
    out.append(f"Memo name: {(ident.get('memo_name') or {}).get('value')}")
    out.append("Stages: " + ", ".join(s.get("name", "") for s in ident.get("stages") or []))
    page = a.get("page") or {}
    out.append(f"Slide: {page.get('width_in')} x {page.get('height_in')} in, margins {page.get('margins_in')}")
    out += ["", "PALETTE"]
    for p in a.get("palette") or []:
        out.append(f"  {p.get('hex')}  {p.get('name'):<16} {p.get('use')}  [{(p.get('evidence') or {}).get('source')}]")
    fonts = a.get("fonts") or {}
    out += ["", f"FONTS heading={fonts.get('heading')} body={fonts.get('body')} tables={fonts.get('tables')}", "", "TEXT ROLES"]
    for r in a.get("text_roles") or []:
        look = f"{r.get('font')} {r.get('size_pt')}pt" + (" bold" if r.get("bold") else "") + (" caps" if r.get("caps") else "")
        lo, hi = r.get("min_size_pt"), r.get("max_size_pt")
        minimum = f" ({lo or r.get('size_pt')}–{hi or r.get('size_pt')}pt)" if (lo or hi) else ""
        out.append(f"  {r.get('role'):<22} {look:<26}{minimum:<12} {r.get('color'):<14} {r.get('applies_to')}")
    out += ["", "FORMATS"]
    for f in a.get("formats") or []:
        out.append(f"  {f.get('item'):<9} {f.get('context'):<6} {' · '.join(f.get('examples') or []):<36} {f.get('rule')}")
    t = a.get("tables") or {}
    out += ["", "TABLES"] + [f"  {k:<10} {t.get(k)}" for k in ("header", "body", "borders", "alignment", "totals",
                                                               "highlight", "min_size_pt", "overflow") if t.get(k)]
    ch = a.get("charts") or {}
    out += ["", "CHARTS"] + [f"  {k:<14} {ch.get(k)}" for k in ("native", "series_colours", "forecast", "focus",
                                                                 "labels", "gridlines", "legend", "title") if ch.get(k)]
    c = a.get("citations") or {}
    out += ["", f"CITATIONS {c.get('scheme')}: marker {c.get('marker')}; {c.get('placement')}"]
    footer = a.get("footer") or {}
    out.append(f"FOOTER {' | '.join(footer.get('items') or [])}; slide number: {footer.get('slide_number')}")
    out += ["", f"CONFLICTS FOR THE PERSON ({len(a.get('conflicts') or [])})"]
    for conflict in a.get("conflicts") or []:
        out.append(f"  ? {conflict.get('topic')}")
        for o in conflict.get("options") or []:
            out.append(f"      {o.get('source')}: {o.get('value')}")
        out.append(f"      recommended: {conflict.get('recommendation')}")
    if result["unresolved"]:
        out += ["", "CHECKS STILL FAILING (for the person)"] + [f"  ! {e}" for e in result["unresolved"]]
    att = result["attempts"]
    out += ["", f"Model calls: {len(att)}, seconds {sum(x['seconds'] for x in att):.0f}, "
                f"tokens in {sum(x['prompt_tokens'] or 0 for x in att)}, out {sum(x['completion_tokens'] or 0 for x in att)}"]
    for i, x in enumerate(att, 1):
        out.append(f"  call {i}: {len(x['errors'])} failed checks" + (f" ({x['errors'][0][:90]}…)" if x["errors"] else ""))
    return "\n".join(out)


def run(firm: str) -> None:
    text, images, used = build_package(firm)
    result = call_with_checks(INSTRUCTIONS, text, images, make_validate(slices.guide_text(firm), used))
    result["input_tokens_estimate"] = len(text) // 4
    save_result(firm, 1, result)
    path = save_step(firm, 1, result, review(firm, result))
    sys.stdout.write(f"{path}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("firm")
    run(parser.parse_args().firm)


if __name__ == "__main__":
    main()
