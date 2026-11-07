"""Pilot: a firm that sent only a guide. Ask a model to propose the memo structure and a layout
for every section from the guide alone (text and page images), then check it with code and draw it.

Code checks:
    - every box sits on the page and inside the side margins the model states
    - boxes of one layout do not overlap (rules and bands may touch others)
    - text sizes are not below the minimum the model reads from the guide
    - colours come from the palette the model reads from the guide
    - every quoted piece of evidence exists in the guide text (tolerant match)
Then every layout is drawn as boxes with their names, one PNG per layout.

Usage (from docs/pilots):
    python guide_layouts.py run <name> <guide.view.txt> <page.png>...
    python guide_layouts.py check <name> <guide.view.txt>
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "out"

INSTRUCTIONS = """A firm sent only its memo guide: no PowerPoint template and no past memos. From the
guide alone (its text and its page images) propose what the platform needs to generate this firm's
memos: page, palette, fonts, the structure of the memo, and a layout for every kind of page.

Positions are in inches on the page size the guide states. Respect the margins it states. Use the
page images: they show how the firm draws things (bands, tabs, tiles, rules), and the guide itself may
be laid out on the firm's own master. Everything you propose must be traceable to the guide: give a
short quote as evidence for each decision. Where the guide is silent, decide, and mark it "assumed".

Every box has:
  name, role (snake_case: action_title, key_figure_value, source_reference, footer...),
  type (text|table|chart|image|shape|line),
  frame {x, y, w, h} in inches,
  style {font, size_pt, bold, caps, color (a palette name), fill (a palette name or null)},
  lock ("firm": part of the master or a locked page; "content": text changes, box does not move; "free"),
  fill ("fixed": text written by the firm, give it in "text"; "record": data of the deal; "model": drafted
        per deal; "person": supplied by a person; "memo": set when the memo is created, like stage or date),
  text (for fixed boxes), what (for the others: what goes in it), assumed (true when the guide is silent).

Return JSON only:
{"page": {"width_in": .., "height_in": .., "margins_in": {"left": .., "right": .., "top": .., "bottom": ..},
          "evidence": "..."},
 "palette": [{"name": "...", "hex": "#RRGGBB", "use": "..."}],
 "fonts": {"heading": "...", "body": "...", "tables": "...", "min_size_pt": .., "evidence": "..."},
 "master": [<boxes on every page>],
 "structure": [{"section": "...", "status": "locked|mandatory|free", "position": "...", "count": "...",
                "layout": "<layout name>", "evidence": "..."}],
 "layouts": [{"name": "...", "used_for": "...", "boxes": [<boxes>], "evidence": ["..."]}],
 "open_questions": ["..."]}
"""


def run(name: str, guide: Path, pages: list[Path]) -> None:
    from dotenv import load_dotenv
    from openai import OpenAI

    load_dotenv(ROOT / ".env")
    model = os.environ.get("OPENAI_MODEL", "gpt-5.5")
    content = [{"type": "text", "text": f"GUIDE TEXT\n{guide.read_text(encoding='utf-8')}"}]
    for page in pages:
        data = base64.b64encode(page.read_bytes()).decode()
        content.append({"type": "text", "text": f"PAGE IMAGE {page.name}"})
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}})
    started = time.time()
    response = OpenAI().chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": INSTRUCTIONS}, {"role": "user", "content": content}],
        response_format={"type": "json_object"},
    )
    record = {"model": model, "seconds": round(time.time() - started, 1),
              "usage": response.usage.model_dump() if response.usage else {},
              "guide": guide.name, "answer": json.loads(response.choices[0].message.content)}
    target = OUT / f"{name}.layouts.json"
    target.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    u = record["usage"]
    sys.stdout.write(f"{target}  seconds={record['seconds']} prompt_tokens={u.get('prompt_tokens')} "
                     f"completion_tokens={u.get('completion_tokens')}\n")


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9%€£.]+", " ", text.lower()).strip()


def overlaps(a: dict, b: dict) -> bool:
    return not (a["x"] + a["w"] <= b["x"] + 0.01 or b["x"] + b["w"] <= a["x"] + 0.01
                or a["y"] + a["h"] <= b["y"] + 0.01 or b["y"] + b["h"] <= a["y"] + 0.01)


def check(name: str, guide: Path) -> None:
    record = json.loads((OUT / f"{name}.layouts.json").read_text(encoding="utf-8"))
    a = record["answer"]
    guide_text = norm(guide.read_text(encoding="utf-8"))
    page = a["page"]
    w, h = page["width_in"], page["height_in"]
    margins = page.get("margins_in", {})
    palette = {p["name"].lower() for p in a.get("palette", [])}
    min_pt = (a.get("fonts") or {}).get("min_size_pt") or 0
    problems, quotes, bad_quotes = [], 0, []

    def check_quote(where: str, quote):
        nonlocal quotes
        for q in ([quote] if isinstance(quote, str) else quote or []):
            words = norm(q).split()
            if len(words) < 3:
                continue
            quotes += 1
            # Tolerant: any run of 5 consecutive words (or the whole quote if shorter) is in the guide.
            window = min(5, len(words))
            if not any(" ".join(words[i:i + window]) in guide_text for i in range(len(words) - window + 1)):
                bad_quotes.append(f"{where}: {q[:90]!r}")

    check_quote("page", page.get("evidence"))
    check_quote("fonts", (a.get("fonts") or {}).get("evidence"))
    for s in a.get("structure", []):
        check_quote(f"structure {s.get('section')}", s.get("evidence"))

    layouts = [{"name": "MASTER", "boxes": a.get("master", [])}] + a.get("layouts", [])
    for layout in layouts:
        check_quote(f"layout {layout['name']}", layout.get("evidence"))
        boxes = layout.get("boxes", [])
        if layout["name"] != "MASTER":
            boxes = boxes + a.get("master", [])
        for box in layout.get("boxes", []):
            f = box.get("frame") or {}
            where = f"{layout['name']} / {box.get('name')}"
            if not f:
                problems.append(f"NO FRAME  {where}")
                continue
            if f["x"] < -0.01 or f["y"] < -0.01 or f["x"] + f["w"] > w + 0.01 or f["y"] + f["h"] > h + 0.01:
                problems.append(f"OFF PAGE  {where} {f}")
            elif box.get("type") not in ("line", "shape") and margins and (
                    f["x"] < margins.get("left", 0) - 0.02 or f["x"] + f["w"] > w - margins.get("right", 0) + 0.02):
                problems.append(f"OUTSIDE SIDE MARGINS  {where} x={f['x']} right={round(f['x'] + f['w'], 3)}")
            style = box.get("style") or {}
            if style.get("size_pt") and min_pt and style["size_pt"] < min_pt:
                problems.append(f"BELOW MIN SIZE  {where} {style['size_pt']}pt < {min_pt}pt")
            for key in ("color", "fill"):
                if style.get(key) and palette and str(style[key]).lower() not in palette:
                    problems.append(f"NOT IN PALETTE  {where} {key}={style[key]}")
        texty = [b for b in boxes if b.get("type") in ("text", "table", "chart", "image") and b.get("frame")]
        for i, b1 in enumerate(texty):
            for b2 in texty[i + 1:]:
                if overlaps(b1["frame"], b2["frame"]):
                    problems.append(f"OVERLAP  {layout['name']}: {b1.get('name')} x {b2.get('name')}")

    u = record.get("usage", {})
    assumed = sum(1 for l in layouts for b in l.get("boxes", []) if b.get("assumed"))
    total = sum(len(l.get("boxes", [])) for l in layouts)
    print(f"seconds={record['seconds']} prompt_tokens={u.get('prompt_tokens')} completion_tokens={u.get('completion_tokens')}")
    print(f"page={w}x{h} palette={len(palette)} sections={len(a.get('structure', []))} layouts={len(a.get('layouts', []))} "
          f"boxes={total} assumed={assumed} quotes={quotes} quotes not in guide={len(bad_quotes)} problems={len(problems)}")
    for p in problems:
        print("  " + p)
    for q in bad_quotes:
        print("  QUOTE NOT IN GUIDE  " + q)
    for q in a.get("open_questions", []):
        print("  OPEN QUESTION  " + q)
    draw(name, a)


def draw(name: str, a: dict) -> None:
    from PIL import Image, ImageDraw

    page = a["page"]
    scale = 110
    hexes = {p["name"].lower(): p["hex"] for p in a.get("palette", [])}
    out_dir = OUT / f"{name}_layouts"
    out_dir.mkdir(parents=True, exist_ok=True)
    for layout in a.get("layouts", []):
        img = Image.new("RGB", (int(page["width_in"] * scale), int(page["height_in"] * scale)), "white")
        d = ImageDraw.Draw(img)
        m = page.get("margins_in", {})
        d.rectangle([m.get("left", 0) * scale, 0, (page["width_in"] - m.get("right", 0)) * scale, img.height - 1], outline="#DDDDDD")
        for box in a.get("master", []) + layout.get("boxes", []):
            f = box.get("frame")
            if not f:
                continue
            rect = [f["x"] * scale, f["y"] * scale, (f["x"] + f["w"]) * scale, (f["y"] + max(f["h"], 0.01)) * scale]
            fill = hexes.get(str((box.get("style") or {}).get("fill") or "").lower())
            d.rectangle(rect, outline="#E4572E" if box in a.get("master", []) else "#333333", fill=fill)
            label = box.get("text") or box.get("name") or ""
            d.text((rect[0] + 3, rect[1] + 2), str(label)[:40], fill="#000000" if not fill else "#FFFFFF")
        safe = re.sub(r"[^A-Za-z0-9]+", "_", layout["name"]).strip("_")
        img.save(out_dir / f"{safe}.png")
    print(f"  drawings in {out_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run")
    r.add_argument("name")
    r.add_argument("guide", type=Path)
    r.add_argument("pages", nargs="+", type=Path)
    c = sub.add_parser("check")
    c.add_argument("name")
    c.add_argument("guide", type=Path)
    args = parser.parse_args()
    if args.command == "run":
        run(args.name, args.guide, args.pages)
    else:
        check(args.name, args.guide)


if __name__ == "__main__":
    main()
