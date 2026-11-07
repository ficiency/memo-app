"""Step 0: read every material a firm sent, once, and leave it ready for the next steps. Code only.

Input: a manifest that says what each file is (guide, template, memo, logo). The kind comes from the
person who uploads the files, never from the file name. No deal record is involved: onboarding
describes the firm, and connecting its concepts to a record happens when its first memo is created.

Output, in out/onboarding/<firm>/:
    extraction/<file>.json      full read of each guide or PowerPoint
    extraction/<file>.view.txt  compact view of each PowerPoint, grouped by layout
    extraction/<file>.txt       guide text in reading order, tables in place
    extraction/pages/           guide page images
    step_0.json                 what was sent, what was found, what is missing, where each file went
    step_0.review.txt           what the person sees

Usage (from backend/):
    python -m app.services.onboarding.step0_extract onboarding_firms/<firm>.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image

from app.readers.pdf_reader import read_pdf, to_text
from app.readers.pptx_reader import read_pptx
from app.readers.pptx_view import build_view
from app.services.onboarding.common import firm_dir, load_manifest, save_step

KINDS = ("guide", "template", "memo", "logo")


def extract_guide(path: Path, folder: Path) -> dict:
    data = read_pdf(path, folder / "pages")
    (folder / f"{path.stem}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    (folder / f"{path.stem}.txt").write_text(to_text(data), encoding="utf-8")
    tables = sum(1 for p in data["pages"] for item in p["content"] if "table" in item)
    return {"pages": len(data["pages"]), "tables": tables,
            "page_size_in": data["pages"][0]["size_in"] if data["pages"] else None,
            "extraction": f"extraction/{path.stem}.json", "text": f"extraction/{path.stem}.txt",
            "page_images": [f"extraction/pages/{p['image']}" for p in data["pages"]]}


def extract_pptx(path: Path, folder: Path) -> dict:
    data = read_pptx(path, folder / "assets")
    (folder / f"{path.stem}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    (folder / f"{path.stem}.view.txt").write_text(build_view(data), encoding="utf-8")
    used = {s["layout"] for s in data["slides"]}
    return {"page_size_in": [data["page"]["width_in"], data["page"]["height_in"]],
            "layouts": len(data["layouts"]), "layouts_used": len(used), "slides": len(data["slides"]),
            "extraction": f"extraction/{path.stem}.json", "view": f"extraction/{path.stem}.view.txt"}


def extract_logo(path: Path) -> dict:
    image = Image.open(path).convert("RGBA")
    opaque = [(r, g, b) for r, g, b, a in image.getdata() if a > 200]
    counts: dict[str, int] = {}
    for rgb in opaque:
        key = "#%02X%02X%02X" % rgb
        counts[key] = counts.get(key, 0) + 1
    colours = sorted(counts, key=counts.get, reverse=True)[:3]
    return {"size_px": list(image.size), "transparent": any(a < 255 for *_, a in image.getdata()),
            "main_colours": colours}


def run(manifest_path: Path) -> None:
    manifest = load_manifest(manifest_path)
    firm = manifest["firm"]
    folder = firm_dir(firm) / "extraction"
    folder.mkdir(parents=True, exist_ok=True)

    materials = []
    for material in manifest["materials"]:
        kind, path = material["kind"], Path(material["file"])
        if kind not in KINDS:
            raise SystemExit(f"unknown material kind {kind!r} for {path.name}")
        entry = {"kind": kind, "file": path.name}
        if kind == "guide":
            entry.update(extract_guide(path, folder))
        elif kind in ("template", "memo"):
            entry.update(extract_pptx(path, folder))
        else:
            entry.update(extract_logo(path))
        materials.append(entry)


    present = {m["kind"] for m in materials}
    missing = [k for k in ("guide", "template", "memo") if k not in present]
    result = {"firm": firm, "materials": materials, "missing": missing}
    save_step(firm, 0, result, review(result))
    sys.stdout.write(review(result) + "\n")


def review(result: dict) -> str:
    lines = [f"STEP 0 · Materials for {result['firm']}", ""]
    for m in result["materials"]:
        if m["kind"] == "guide":
            lines.append(f"  guide     {m['file']}: {m['pages']} pages, {m['tables']} tables, page {m['page_size_in']} in")
        elif m["kind"] in ("template", "memo"):
            lines.append(f"  {m['kind']:<9} {m['file']}: {m['slides']} slides, {m['layouts']} layouts "
                         f"({m['layouts_used']} used), page {m['page_size_in']} in")
        else:
            lines.append(f"  logo      {m['file']}: {m['size_px'][0]}x{m['size_px'][1]} px, "
                         f"{'transparent' if m['transparent'] else 'opaque'}, colours {', '.join(m['main_colours'])}")
    lines.append("")
    lines.append("  Missing: " + (", ".join(result["missing"]) if result["missing"] else "nothing"))
    # Only slides are compared: a guide is a document to read, its page is not the memo's page.
    sizes = {tuple(m["page_size_in"]) for m in result["materials"] if m["kind"] in ("template", "memo")}
    if len(sizes) > 1:
        lines.append(f"  Note: template and memos disagree on slide size: {sorted(sizes)}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("manifest", type=Path)
    run(parser.parse_args().manifest)


if __name__ == "__main__":
    main()
