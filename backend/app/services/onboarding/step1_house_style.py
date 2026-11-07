"""Step 1: house style. Page, palette and what each colour is for, fonts, text styles by role,
number and date formats, citations and footer.

Input package (only what exists):
    guide       full text in reading order + page images
    design file theme, master default text styles and every text look in use (template, or else a memo)
    memos       numbers as they were written (only when there is no guide, to infer formats)
Code then checks the answer; failures go back to the model (llm.call_with_checks).

Usage (from backend/):
    python -m app.services.onboarding.step1_house_style <firm>
"""
from __future__ import annotations

import argparse
import re
import sys

from app.services.onboarding import slices
from app.services.onboarding.common import load_step, save_result, save_step
from app.services.onboarding.checks import guide_quote_errors
from app.services.formats import SCALES, reproduces
from app.services.onboarding.llm import call_with_checks

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
          "margins_in": {{"left": <n>, "right": <n>, "top": <n>, "bottom": <n>}} or null,
          "grid": {{"columns": <number or null>, "gutter_in": <number or null>}}, "evidence": <E>}},
 "palette": [{{"name": "<the firm's name for the colour, lower case>", "hex": "#RRGGBB",
              "use": "<what the firm says this colour is for, including any restriction>", "evidence": <E>}}],
 "fonts": {{"heading": "<font>", "body": "<font>", "tables": "<font or null>", "evidence": <E>}},
 "text_roles": [{{"role": "<snake_case name of a kind of text>", "applies_to": "<which text this is>",
                 "font": "<font>", "size_pt": <usual size>, "min_size_pt": <number or null>, "max_size_pt": <number or null>,
                 "bold": <true/false>, "caps": <true/false>, "color": "<a palette name>", "evidence": <E>}}],
 "formats": [{{"item": "<one of {', '.join(FORMAT_ITEMS)}>", "context": "<prose|table|chart|any>",
              "rule": "<the rule in one line>", "applies_to": "<which figures, when the firm writes this kind of
              figure in more than one way (e.g. returns), else null>",
              "examples": ["<the figure only, exactly as the firm writes it: 25.2% not IRR 25.2%>"],
              "decimals": <n or null>, "symbol": "<currency symbol, or null>", "symbol_position": "<before|after|null>",
              "unit_display": "<inline (£22.9m) | header (the unit is written in the column header) | null>",
              "negative_style": "<minus|parentheses|unicode_minus|null>",
              "thousands_separator": "<',' or ' ' or '.' or '' or null>", "decimal_separator": "<'.' or ',' or null>",
              "suffix": "<'x' for multiples, or null>", "pattern": "<dates only, with YYYY YY MMMM MMM MM M DD D>",
              "scales": [{{"scale": "<units|k|m|bn>", "from_value": <smallest value in whole currency units>,
                          "decimals": <n>}}], "evidence": <E>}}],
 "tables": {{"header_fill": "<palette name, or null when header cells have no fill>", "header_text": "<palette name>", "header_bold": <true/false>,
            "forecast_header_fill": "<palette name for header cells of forecast periods, or null>",
            "body_text": "<palette name>", "banding": "<palette name of alternate-row fill, or null>",
            "rule_color": "<palette name of row rules and borders, or null>", "rule_width_pt": <number or null>,
            "header_rule_width_pt": <number or null>, "total_bold": <true/false/null>,
            "highlight_color": "<palette name used to emphasise a cell, or null>", "highlight_bold": <true/false/null>,
            "number_align": "<right|left|center>", "min_size_pt": <number or null>, "evidence": <E>}},
 "charts": {{"native": <true/false>, "series_colours": ["<palette names, in order>"],
            "forecast": "<how forecast periods are shown, or null>", "focus_color": "<palette name, or null>",
            "labels_on": <true/false/null>, "label_size_pt": <number or null>, "gridlines": <true/false/null>,
            "legend": "<when a legend is shown, or null>", "title": "<whether charts carry a title, or null>",
            "evidence": <E>}},
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
- Tables and charts are fields a program paints from: every colour is a palette name, sizes and widths
  are numbers. This step is appearance only: what a table must contain, where a colour may be used and
  what to do when content does not fit belong to a later rules step; leave them out. The table headers in use in the design
  file (listed below when there is one) show how the firm really paints headers; if the materials say
  otherwise, add a conflict.
- Grid columns and gutter: fill them only when a material states them; otherwise null.
- Formats are fields a program writes numbers with: every example must come out exactly as written when
  its value is formatted with the fields. Money: one scale rule per range ("£k below £1m, £m from £1m":
  k from 0, m from 1000000), with its decimals. Percent values are fractions (0.25 is 25%). Examples are the
  figure alone, without the words around it. When the firm writes one kind of figure in two ways (whole
  percentages in general, one decimal for returns), give one format for each, with applies_to.
- When two materials say different things, add a conflict with each version; do not choose silently.
- Text roles: cover the kinds of text the materials define (titles, body, tables, notes, footer, labels,
  key figures...). Use the text looks in use to see what is really there.
- Where nothing in the materials says it, use source "assumed" and keep it short.
"""


TABLE_COLOURS = ("header_fill", "header_text", "forecast_header_fill", "body_text", "banding", "rule_color",
                 "highlight_color")
TABLE_NUMBERS = ("rule_width_pt", "header_rule_width_pt", "min_size_pt")
TABLE_REQUIRED = ("header_text", "body_text")


def format_errors(f: dict) -> list[str]:
    """A format is right when it writes every example back exactly as the firm wrote it."""
    where = f"format {f.get('item')}/{f.get('context')}"
    errors = []
    for key, allowed in (("symbol_position", ("before", "after", None)), ("unit_display", ("inline", "header", None)),
                         ("negative_style", ("minus", "parentheses", "unicode_minus", None))):
        if f.get(key) not in allowed:
            errors.append(f"{where}: {key} must be one of {allowed}")
    for rule in f.get("scales") or []:
        if rule.get("scale") not in SCALES or not isinstance(rule.get("from_value"), (int, float)) \
                or not isinstance(rule.get("decimals"), int):
            errors.append(f"{where}: each scale rule needs scale (units|k|m|bn), a numeric from_value and integer decimals")
    if errors:
        return errors
    for example in f.get("examples") or []:
        ok, out = reproduces(str(example), f)
        if not ok:
            errors.append(f"{where}: example {example!r} comes out as {out!r} with these fields; fix the fields")
    return errors


def table_and_chart_errors(answer: dict, names: list[str], hex_by_name: dict[str, str],
                           headers_in_use) -> list[str]:
    """Tables and charts must be fields a painter can apply, and agree with the design file's tables."""
    errors = []
    t = answer.get("tables")
    if not isinstance(t, dict):
        return ["tables must be an object with the fields of the shape"]
    for key in TABLE_COLOURS:
        value = t.get(key)
        if value is None and key not in TABLE_REQUIRED:
            continue
        if str(value).lower() not in names:
            errors.append(f"tables.{key} must be a palette name, got {value!r}")
    for key in TABLE_NUMBERS:
        value = t.get(key)
        if value is not None and not (isinstance(value, (int, float)) and value > 0):
            errors.append(f"tables.{key} must be a positive number or null, got {value!r}")
    if t.get("number_align") not in ("right", "left", "center"):
        errors.append(f"tables.number_align must be right, left or center, got {t.get('number_align')!r}")
    for key in ("header_bold", "total_bold", "highlight_bold"):
        if t.get(key) not in (True, False, None) or (key == "header_bold" and t.get(key) is None):
            errors.append(f"tables.{key} must be true or false")
    if headers_in_use:
        (fill, text), _ = headers_in_use.most_common(1)[0]
        conflict_text = " ".join(str(c) for c in answer.get("conflicts") or []).upper()
        for key, real in (("header_fill", fill), ("header_text", text)):
            chosen = hex_by_name.get(str(t.get(key)).lower()) if t.get(key) else None
            if real and chosen != real and real not in conflict_text:
                errors.append(f"tables.{key} is {t.get(key)!r} ({chosen}) but the design file's tables use {real}; "
                              f"use that colour or add a conflict naming {real}")
    ch = answer.get("charts")
    if not isinstance(ch, dict):
        return errors + ["charts must be an object with the fields of the shape"]
    if ch.get("native") not in (True, False):
        errors.append("charts.native must be true or false")
    if ch.get("focus_color") is not None and str(ch["focus_color"]).lower() not in names:
        errors.append(f"charts.focus_color must be a palette name or null, got {ch['focus_color']!r}")
    for key in ("labels_on", "gridlines"):
        if ch.get(key) not in (True, False, None):
            errors.append(f"charts.{key} must be true, false or null")
    size = ch.get("label_size_pt")
    if size is not None and not (isinstance(size, (int, float)) and size > 0):
        errors.append("charts.label_size_pt must be a positive number or null")
    grid = (answer.get("page") or {}).get("grid") or {}
    for key in ("columns", "gutter_in"):
        value = grid.get(key)
        if value is not None and not (isinstance(value, (int, float)) and value > 0):
            errors.append(f"page.grid.{key} must be a positive number or null")
    return errors


def make_validate(guide: str | None, used_colours: set[str], headers_in_use=None):
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
            errors += format_errors(f)
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
        hex_by_name = {str(p.get("name", "")).lower(): str(p.get("hex", "")).upper() for p in palette}
        errors += table_and_chart_errors(answer, names, hex_by_name, headers_in_use)
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
        headers = slices.table_headers(data)
        if headers:
            parts.append("=== TABLE HEADERS IN USE in the design file (header fill / header text: tables)\n"
                         + ", ".join(f"{f} / {t}: {n}" for (f, t), n in headers.most_common()))
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
    out.append(f"Slide: {page.get('width_in')} x {page.get('height_in')} in, margins {page.get('margins_in')}, grid {page.get('grid')}")
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
        fields = {k: f.get(k) for k in ("decimals", "symbol", "unit_display", "negative_style", "suffix", "pattern") if f.get(k) is not None}
        scales = ", ".join(f"{r.get('scale')} from {r.get('from_value'):g} ({r.get('decimals')} dp)" for r in f.get("scales") or []
                           if isinstance(r.get("from_value"), (int, float)))
        out.append(f"            fields {fields}" + (f"; scales: {scales}" if scales else ""))
    t = a.get("tables") or {}
    out += ["", "TABLES"] + [f"  {k:<22} {t.get(k)}" for k in (*TABLE_COLOURS, "header_bold", "total_bold", "highlight_bold",
                                                               *TABLE_NUMBERS, "number_align") if t.get(k) is not None]
    ch = a.get("charts") or {}
    out += ["", "CHARTS"] + [f"  {k:<14} {ch.get(k)}" for k in ("native", "series_colours", "forecast", "focus_color",
                                                                 "labels_on", "label_size_pt", "gridlines", "legend", "title")
                             if ch.get(k) is not None]
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


def headers_in_use(firm: str):
    _, data = slices.design_source(firm)
    return slices.table_headers(data) if data else None


FORMATS_ONLY = """
This time return only the "formats" key: {"formats": [...]}, with the shape and rules above. Every other part
of the house style is already approved and must not change."""


def run_formats_only(firm: str) -> None:
    """Re-extract only the formats and put them into the approved step 1 result: palette and role names
    that later steps already use stay as they are."""
    text, images, _ = build_package(firm)
    guide = slices.guide_text(firm)

    def validate(answer: dict) -> list[str]:
        errors = []
        for f in answer.get("formats") or []:
            if f.get("item") not in FORMAT_ITEMS:
                errors.append(f"format item {f.get('item')!r} is not one of {FORMAT_ITEMS}")
            if not f.get("examples"):
                errors.append(f"format {f.get('item')}/{f.get('context')} has no examples")
            errors += format_errors(f)
        return errors + guide_quote_errors(answer, guide) if answer.get("formats") else ["formats is empty"]

    call = call_with_checks(INSTRUCTIONS + FORMATS_ONLY, text, images, validate)
    result = load_step(firm, 1)
    result["answer"]["formats"] = call["answer"].get("formats") or []
    result["unresolved"] = [e for e in result.get("unresolved") or [] if not e.startswith("format")] + call["unresolved"]
    result["attempts"] = (result.get("attempts") or []) + [dict(a, call="formats") for a in call["attempts"]]
    save_result(firm, 1, result)
    path = save_step(firm, 1, result, review(firm, result))
    sys.stdout.write(f"{path}\n")


def run(firm: str) -> None:
    text, images, used = build_package(firm)
    result = call_with_checks(INSTRUCTIONS, text, images,
                              make_validate(slices.guide_text(firm), used, headers_in_use(firm)))
    result["input_tokens_estimate"] = len(text) // 4
    save_result(firm, 1, result)
    path = save_step(firm, 1, result, review(firm, result))
    sys.stdout.write(f"{path}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("firm")
    parser.add_argument("--formats-only", action="store_true", help="re-extract only the formats")
    args = parser.parse_args()
    run_formats_only(args.firm) if args.formats_only else run(args.firm)


if __name__ == "__main__":
    main()
