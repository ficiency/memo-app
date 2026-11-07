"""Create a new memo from a firm spec: the list of slides and their elements, fully resolved, that
every painter reads (HTML now as a stand-in for the canvas, PowerPoint for the export).

A new memo has one slide per layout the structure uses, in section order, and on each slide the
master's elements (when the layout shows them) and the layout's elements. Each element is resolved:
frame, colours as hex, text style from its own style or its role, and its content from the recipe.
With no deal record, figures stay as chips and drafted parts as placeholders.

Nothing here knows about any firm: it only reads steps 0–4.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from onboarding_pilot import slices
from onboarding_pilot.common import firm_dir, load_step


def _hex(value, palette: dict[str, str]) -> str | None:
    if not value:
        return None
    if isinstance(value, dict):
        return value.get("hex")
    text = str(value)
    if text.startswith("#"):
        return text.upper()
    return palette.get(text.lower())


def _segments(pattern: list[dict]) -> list[dict]:
    """Recipe segments as display segments: text, chip (a value the record or memo will fill),
    model (drafted per deal), person (supplied by a person), cond (added when a fact holds)."""
    out = []
    for seg in pattern:
        kind = seg.get("type")
        if kind == "fixed":
            out.append({"kind": "text", "text": seg.get("text", "")})
        elif kind in ("figure", "fact", "memo_data"):
            out.append({"kind": "chip", "text": seg.get("concept", "?"), "source": kind})
        elif kind == "conditional":
            out.append({"kind": "cond", "text": seg.get("text", ""), "when": seg.get("when", "")})
        elif kind == "model_writes":
            out.append({"kind": "model", "text": seg.get("what", ""), "must_include": seg.get("must_include") or []})
        elif kind == "person_writes":
            out.append({"kind": "person", "text": seg.get("what", "")})
    return out


def _table(seg: dict) -> dict:
    header = [str(h) for h in seg.get("header") or []]
    width = max(len(header), 2)
    rows = []
    for row in seg.get("rows") or []:
        label = {"kind": "text" if row.get("label_type") in (None, "fixed") else "chip", "text": row.get("label", "")}
        cell = {"kind": "chip", "text": row.get("concept")} if row.get("concept") else {"kind": "model", "text": row.get("cells", "")}
        rows.append([label] + [cell] * (width - 1))
    return {"header": header or [""] * width, "rows": rows}


class MemoBuilder:
    def __init__(self, firm: str):
        self.firm = firm
        self.style = load_step(firm, 1)["answer"]
        self.structure = load_step(firm, 2)["answer"]
        step3 = load_step(firm, 3)
        self.master, self.layouts = step3.get("master") or [], {l["name"]: l for l in step3["layouts"]}
        recipes = load_step(firm, 4)["answer"].get("recipes") or []
        self.recipe = {(r.get("layout"), r.get("box_id")): r.get("pattern") or [] for r in recipes}
        self.palette = {str(p.get("name", "")).lower(): str(p.get("hex", "")).upper() for p in self.style.get("palette") or []}
        self.roles = {r.get("role"): r for r in self.style.get("text_roles") or []}
        self.fonts = self.style.get("fonts") or {}
        material, self.design = slices.design_source(firm)
        self.out = firm_dir(firm) / "preview"
        (self.out / "assets").mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------------- images

    def _design_image(self, layout_name: str | None, box_name: str) -> str | None:
        """The asset a design-file box shows (layouts and master keep their pictures)."""
        if not self.design:
            return None
        pools = [self.design["master"]["elements"]]
        if layout_name:
            pools += [l["elements"] for l in self.design["layouts"] if l["name"] == layout_name]
        for pool in pools:
            for el in pool:
                if el["name"] == box_name and (el.get("image") or {}).get("asset"):
                    asset = el["image"]["asset"]
                    found = list((firm_dir(self.firm) / "extraction" / "assets").glob(f"{asset}.*"))
                    if found:
                        target = self.out / "assets" / found[0].name
                        shutil.copy(found[0], target)
                        return f"assets/{found[0].name}"
        return None

    def _firm_logo(self) -> str | None:
        """For a generated image box: the logo the firm sent (the first one listed)."""
        logos = slices.materials(self.firm, "logo")
        manifest_dir = firm_dir(self.firm)
        if not logos:
            return None
        from onboarding_pilot.common import PILOTS
        import json
        manifest = json.loads((PILOTS / "onboarding_pilot" / "firms" / f"{self.firm}.json").read_text(encoding="utf-8"))
        source = next((m["file"] for m in manifest["materials"] if m["kind"] == "logo"), None)
        if not source:
            return None
        path = (PILOTS / "onboarding_pilot" / "firms" / source).resolve()
        target = self.out / "assets" / path.name
        shutil.copy(path, target)
        return f"assets/{path.name}"

    # -------------------------------------------------------------- elements

    def _text_style(self, box: dict) -> dict:
        role = self.roles.get(box.get("role")) or self.roles.get(box.get("text_role")) or {}
        own = box.get("style") or {}
        font = own.get("font") or role.get("font") or self.fonts.get("body")
        return {
            "font": font,
            "size_pt": own.get("size_pt") or role.get("size_pt") or 12,
            "bold": own.get("bold", role.get("bold", False)),
            "caps": bool(role.get("caps")) if "caps" not in own else bool(own.get("caps")),
            "color": _hex(own.get("color"), self.palette) or self.palette.get(str(role.get("color", "")).lower()) or "#000000",
            "align": own.get("align") or "l",
            "anchor": own.get("anchor") or "top",
        }

    def element(self, box: dict, layout_name: str | None, slide_number: int) -> dict:
        el = {"id": box["id"], "name": box.get("name"), "type": box["type"], "frame": box["frame"],
              "role": box.get("role"), "lock": box.get("lock"), "source": box.get("source")}
        el["fill"] = _hex(box.get("fill"), self.palette)
        line = box.get("line")
        if line:
            colour = _hex(line.get("color") if isinstance(line, dict) and "color" in line else line, self.palette)
            el["line"] = {"hex": colour, "width_pt": (line.get("width_pt") if isinstance(line, dict) else None) or 0.75}
        if box["type"] == "image":
            el["src"] = self._design_image(layout_name, box.get("name", "")) or self._firm_logo()
            return el
        if box["type"] in ("text", "table", "chart"):
            el["text_style"] = self._text_style(box)
        pattern = self.recipe.get((layout_name, box["id"])) if layout_name else None
        if box.get("text"):
            el["content"] = [{"kind": "text", "text": box["text"]}]
        elif pattern:
            tables = [s for s in pattern if s.get("type") == "table"]
            if tables:
                el["table"] = _table(tables[0])
            else:
                el["content"] = _segments(pattern)
        elif box.get("placeholder", {}) and (box.get("placeholder") or {}).get("type") == "slide_number":
            el["content"] = [{"kind": "text", "text": str(slide_number)}]
        elif box.get("prompt") or box.get("what"):
            el["content"] = [{"kind": "hint", "text": box.get("prompt") or box.get("what")}]
        return el

    def build(self) -> dict:
        page = self.style.get("page") or {}
        slides, number = [], 0
        for section in self.structure.get("sections") or []:
            for layout_ref in section.get("layouts") or []:
                name = layout_ref.split(":", 1)[1] if str(layout_ref).startswith("to_generate") else layout_ref
                layout = self.layouts.get(name)
                if not layout:
                    continue
                number += 1
                boxes = (self.master if layout.get("shows_master", True) else []) + layout["boxes"]
                elements = [self.element(b, None if b in self.master else name, number) for b in boxes]
                slides.append({"number": number, "section": section["name"], "status": section.get("status"),
                               "layout": name, "elements": elements})
        return {"firm": self.firm, "page": {"width_in": page.get("width_in"), "height_in": page.get("height_in")},
                "slides": slides}


def new_memo(firm: str) -> dict:
    return MemoBuilder(firm).build()
