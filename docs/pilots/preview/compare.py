"""Review a preview memo against what the firm sent, by code, and build side-by-side contact sheets.

Two kinds of check, neither knows any firm:
1. Against the design file (when the firm sent one): every preview element is matched to the box it
   came from in the design file's layout (same layout, same box) and compared: position and size,
   text font, size, weight and colour, fill and line colours, fixed text, and whether the original box
   uses more than one text style (a value and a smaller line under it).
2. On the preview alone (every firm): content boxes with no recipe, master boxes with no content,
   tables whose structure is left abstract, charts with no data model, text that likely does not fit.

Each finding names the step whose output it traces back to.

Usage (from docs/pilots):
    python -m preview.compare <firm>
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict

from onboarding_pilot import slices
from onboarding_pilot.common import firm_dir, load_step

TOL_IN = 0.02
TOL_PT = 0.5


def _hex(colour) -> str | None:
    if isinstance(colour, dict):
        return (colour.get("hex") or "").upper() or None
    return str(colour).upper() if colour else None


def _text_looks(el: dict) -> list[tuple]:
    """Distinct (size, colour, bold) looks of the runs that hold text in a design-file box."""
    looks = []
    for p in el.get("paragraphs") or []:
        for run in p.get("runs") or []:
            if run.get("text", "").strip():
                look = (run.get("size_pt"), _hex(run.get("color")), run.get("bold"))
                if look not in looks:
                    looks.append(look)
    return looks


def _level_sizes(el: dict) -> list[float]:
    """Sizes the box declares for its paragraph levels (a placeholder can style level 2 apart)."""
    return sorted({s.get("size_pt") for s in (el.get("level_styles") or {}).values() if s.get("size_pt")})


def against_design(firm: str, memo: dict) -> list[dict]:
    _, design = slices.design_source(firm)
    if not design:
        return []
    layouts = {l["name"]: l for l in design["layouts"]}
    sample = defaultdict(list)
    for slide in design["slides"]:
        sample[slide["layout"]].append(slide)
    step3 = {l["name"]: l for l in load_step(firm, 3)["layouts"]}
    findings = []

    def add(slide, el, kind, detail, step):
        findings.append({"slide": slide["number"], "layout": slide["layout"], "box": el.get("name"),
                         "kind": kind, "detail": detail, "step": step})

    for slide in memo["slides"]:
        layout = layouts.get(slide["layout"])
        if not layout or step3.get(slide["layout"], {}).get("source") != "imported":
            continue
        by_name = defaultdict(list)
        for el in layout["elements"]:
            by_name[el["name"]].append(el)
        used = Counter()
        for el in slide["elements"]:
            if el.get("source") != "imported" or el["id"].startswith("m"):
                continue
            candidates = by_name.get(el["name"]) or []
            original = candidates[used[el["name"]]] if used[el["name"]] < len(candidates) else None
            used[el["name"]] += 1
            if original is None:
                add(slide, el, "extra box", "not in the design file's layout", "step 3")
                continue
            f, o = el["frame"], original.get("frame") or {}
            moved = [k for k in ("x", "y", "w", "h") if o and abs(f[k] - o.get(k, 0)) > TOL_IN]
            if moved:
                add(slide, el, "position", f"{', '.join(moved)} differ: {f} vs {o}", "painter")
            if el.get("fill") and _hex(original.get("fill")) and el["fill"].upper() != _hex(original.get("fill")):
                add(slide, el, "fill colour", f"{el['fill']} vs {_hex(original.get('fill'))}", "step 3")
            ts = el.get("text_style")
            own = original.get("effective_style") or {}
            if ts and own.get("size_pt") and abs(ts["size_pt"] - own["size_pt"]) > TOL_PT:
                add(slide, el, "text size", f"{ts['size_pt']}pt vs {own['size_pt']}pt in the design file", "step 3")
            if ts and own.get("color") and _hex(own.get("color")) != ts["color"].upper():
                add(slide, el, "text colour", f"{ts['color']} vs {_hex(own.get('color'))}", "step 3")
            if ts and own.get("font") and own["font"] != ts["font"]:
                add(slide, el, "font", f"{ts['font']} vs {own['font']}", "step 3")
            # A box whose own text uses several looks (a value, then a smaller line under it).
            looks = set()
            for s in sample.get(slide["layout"], []):
                for se in s["elements"]:
                    if (se.get("placeholder") or {}).get("idx") == (original.get("placeholder") or {}).get("idx") and original.get("placeholder"):
                        looks.update(_text_looks(se))
            levels = _level_sizes(original)
            sizes = {l[0] for l in looks if l[0]} | set(levels)
            if len(sizes) > 1:
                add(slide, el, "several text styles in one box",
                    f"the design file uses sizes {sorted(sizes)} inside this box; the spec keeps one style ({ts and ts['size_pt']}pt)",
                    "step 3 (+ step 4 paragraph breaks)")
        for name, candidates in by_name.items():
            if slices.off_page(candidates[0].get("frame"), design["page"]):
                continue
            if used[name] < len(candidates):
                add(slide, {"name": name}, "missing box", f"{len(candidates) - used[name]} box(es) of the design file not in the preview", "step 3")
    return findings


def on_preview(memo: dict) -> list[dict]:
    findings = []
    for slide in memo["slides"]:
        for el in slide["elements"]:
            def add(kind, detail, step):
                findings.append({"slide": slide["number"], "layout": slide["layout"], "box": el.get("name"),
                                 "kind": kind, "detail": detail, "step": step})
            content = el.get("content") or []
            is_master = el["id"].startswith(("m", "gm"))
            if el["type"] in ("text", "table", "chart") and not el.get("table"):
                if not content:
                    add("empty content box", "no recipe and no fixed text", "step 4")
                elif all(seg["kind"] == "hint" for seg in content):
                    add("master box without content" if is_master else "box without recipe",
                        f"shows only its description: {content[0]['text'][:60]!r}", "step 4")
            if el["type"] == "chart" and not el.get("chart"):
                add("chart without data model", "no series, concepts or colours", "step 4 (+ step 1 series colours)")
            if el.get("table"):
                t = el["table"]
                if any(h.strip().lower() in ("periods", "period", "years") for h in t["header"]):
                    add("abstract table columns", f"header {t['header']}: the period columns are not stated", "step 4")
                if len(t["header"]) <= 2 and len(t["rows"]) <= 4 and any(" or " in r[0]["text"] for r in t["rows"]):
                    add("abstract table rows", f"rows describe categories ({t['rows'][0][0]['text']!r}…), not the table's rows", "step 4")
            ts = el.get("text_style")
            if ts and content:
                chars = sum(len(seg.get("text", "")) + 2 for seg in content)
                f = el["frame"]
                per_line = max(1, f["w"] * 72 / (ts["size_pt"] * 0.5))
                lines_fit = max(1, f["h"] * 72 / (ts["size_pt"] * 1.2))
                if chars / per_line > lines_fit * 1.15 and f["h"] > 0.05:
                    add("likely overflow", f"~{chars} chars at {ts['size_pt']}pt in {f['w']:.2f}x{f['h']:.2f}in", "step 3 / painter")
    return findings


def table_style_finding(firm: str) -> list[dict]:
    tables = (load_step(firm, 1)["answer"].get("tables") or {})
    # Structured means colours and sizes a painter can apply; the evidence field does not count.
    structured = any(isinstance(v, dict) for k, v in tables.items() if k != "evidence")
    if tables and not structured:
        return [{"slide": "-", "layout": "-", "box": "-", "kind": "table style in prose",
                 "detail": "step 1 stores table conventions as sentences; painters cannot apply them (tables fall back to the default style)",
                 "step": "step 1"}]
    return []


def contact_sheets(firm: str, memo: dict) -> list[str]:
    """Original slide (the design file's sample slide for that layout, when there is one) next to the
    preview, four pairs per image."""
    from PIL import Image, ImageDraw

    folder = firm_dir(firm) / "preview" / "png"
    _, design = slices.design_source(firm)
    material, _ = slices.design_source(firm)
    originals = {}
    if design:
        pptx_name = material["file"].replace(".pptx", "")
        tiles = firm_dir(firm) / "preview" / "png" / "design"
        for slide in design["slides"]:
            path = tiles / f"s{slide['number']:02d}.png"
            if path.exists() and slide["layout"] not in originals:
                originals[slide["layout"]] = path
    pairs = []
    for slide in memo["slides"]:
        preview = folder / f"pptx_s{slide['number']:02d}.png"
        pairs.append((originals.get(slide["layout"]), preview, f"{slide['number']}. {slide['layout']}"))
    sheets = []
    width = 700
    for i in range(0, len(pairs), 4):
        group = pairs[i:i + 4]
        sample = Image.open(group[0][1])
        h = int(sample.height * width / sample.width)
        sheet = Image.new("RGB", (2 * width + 30, len(group) * (h + 30) + 10), "#9A9A9A")
        draw = ImageDraw.Draw(sheet)
        for r, (orig, prev, label) in enumerate(group):
            y = 10 + r * (h + 30)
            draw.text((10, y), f"original: {label}", fill="black")
            draw.text((width + 20, y), "preview from the spec", fill="black")
            if orig:
                sheet.paste(Image.open(orig).convert("RGB").resize((width, h)), (10, y + 15))
            sheet.paste(Image.open(prev).convert("RGB").resize((width, h)), (width + 20, y + 15))
        path = folder / f"sheet_{i // 4 + 1}.png"
        sheet.save(path)
        sheets.append(str(path))
    return sheets


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("firm")
    firm = parser.parse_args().firm
    memo = json.loads((firm_dir(firm) / "preview" / "memo.json").read_text(encoding="utf-8"))
    findings = table_style_finding(firm) + against_design(firm, memo) + on_preview(memo)
    by_kind = Counter(f["kind"] for f in findings)
    lines = [f"PREVIEW REVIEW · {firm}: {len(findings)} findings", "", "BY KIND (step it traces back to)"]
    steps = {f["kind"]: f["step"] for f in findings}
    for kind, n in by_kind.most_common():
        lines.append(f"  {n:>4}  {kind:<34} {steps[kind]}")
    lines += ["", "DETAIL"]
    for f in findings:
        lines.append(f"  s{f['slide']} {f['layout']} / {f['box']}: {f['kind']} — {f['detail']}")
    (firm_dir(firm) / "preview" / "review.txt").write_text("\n".join(lines), encoding="utf-8")
    (firm_dir(firm) / "preview" / "findings.json").write_text(json.dumps(findings, indent=2, ensure_ascii=False), encoding="utf-8")
    sheets = contact_sheets(firm, memo)
    sys.stdout.write("\n".join(lines[:3 + len(by_kind)]) + f"\n  sheets: {len(sheets)}\n")


if __name__ == "__main__":
    main()
