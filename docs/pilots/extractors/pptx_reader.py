"""Read a PowerPoint file (a firm template or a past memo) into plain, ordered data.

What it returns is everything a memo generator could need, nothing else:

- page: slide size.
- theme: colour slots with their hex, heading and body fonts.
- master: default text styles per level, background, and the elements every slide inherits.
- layouts: every layout with its placeholders (with their effective style) and fixed elements.
- slides: every slide in order, with its layout and every element on it.
- assets: every image, saved once by content hash.

Units: inches (3 decimals) and points. Colours: {"hex": "#RRGGBB", "theme": "accent2"} when they
come from the theme, {"hex": "#RRGGBB"} when typed by hand.

Usage:
    python -m extractors.pptx_reader <file.pptx> [--out <dir>]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from lxml import etree
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.opc.constants import RELATIONSHIP_TYPE as RT

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
}
EMU_PER_INCH = 914400
SCHEME_ALIASES = {"tx1": "dk1", "bg1": "lt1", "tx2": "dk2", "bg2": "lt2"}


def inches(emu) -> float | None:
    return None if emu is None else round(emu / EMU_PER_INCH, 3)


class PptxReader:
    def __init__(self, path: Path, asset_dir: Path | None = None):
        self.path = Path(path)
        self.prs = Presentation(str(self.path))
        self.asset_dir = asset_dir
        self.assets: dict[str, dict] = {}
        self.theme_colors: dict[str, str] = {}
        self.fonts: dict[str, str] = {}

    # ------------------------------------------------------------------ public

    def read(self) -> dict:
        master = self.prs.slide_masters[0]
        self._read_theme(master)
        return {
            "file": self.path.name,
            "page": {"width_in": inches(self.prs.slide_width), "height_in": inches(self.prs.slide_height)},
            "theme": {"colors": self.theme_colors, "fonts": self.fonts},
            "master": self._read_master(master),
            "layouts": [self._read_layout(layout) for layout in master.slide_layouts],
            "slides": [self._read_slide(n, slide) for n, slide in enumerate(self.prs.slides, 1)],
            "assets": list(self.assets.values()),
        }

    # ------------------------------------------------------------------ theme

    def _read_theme(self, master) -> None:
        theme = etree.fromstring(master.part.part_related_by(RT.THEME).blob)
        for slot in theme.find(".//a:clrScheme", NS):
            name = etree.QName(slot).localname
            srgb = slot.find("a:srgbClr", NS)
            sys_clr = slot.find("a:sysClr", NS)
            value = srgb.get("val") if srgb is not None else sys_clr.get("lastClr") if sys_clr is not None else None
            if value:
                self.theme_colors[name] = "#" + value.upper()
        font_scheme = theme.find(".//a:fontScheme", NS)
        self.fonts = {
            "heading": font_scheme.find("a:majorFont/a:latin", NS).get("typeface"),
            "body": font_scheme.find("a:minorFont/a:latin", NS).get("typeface"),
        }

    def _color(self, container) -> dict | None:
        """Colour inside a fill element (solidFill, buClr, ...)."""
        if container is None:
            return None
        srgb = container.find(".//a:srgbClr", NS)
        if srgb is not None:
            return {"hex": "#" + srgb.get("val").upper()}
        scheme = container.find(".//a:schemeClr", NS)
        if scheme is not None:
            slot = SCHEME_ALIASES.get(scheme.get("val"), scheme.get("val"))
            color = {"theme": slot, "hex": self.theme_colors.get(slot)}
            mods = {etree.QName(m).localname: m.get("val") for m in scheme}
            if mods:
                color["mods"] = mods
            return color
        return None

    def _font(self, typeface: str | None) -> str | None:
        if typeface in ("+mj-lt", "+mj-ea", "+mj-cs"):
            return self.fonts.get("heading")
        if typeface in ("+mn-lt", "+mn-ea", "+mn-cs"):
            return self.fonts.get("body")
        return typeface

    # ------------------------------------------------------------------ text styles

    def _run_style(self, rpr) -> dict:
        """Explicit character properties on a run or a level (only what is set)."""
        if rpr is None:
            return {}
        style = {}
        if rpr.get("sz"):
            style["size_pt"] = int(rpr.get("sz")) / 100
        for attr, key in (("b", "bold"), ("i", "italic")):
            if rpr.get(attr) is not None:
                style[key] = rpr.get(attr) in ("1", "true")
        if rpr.get("u") not in (None, "none"):
            style["underline"] = True
        if rpr.get("baseline"):
            style["baseline_pct"] = int(rpr.get("baseline")) / 1000
        if rpr.get("cap") in ("all", "small"):
            style["caps"] = rpr.get("cap")
        if rpr.get("spc"):
            style["spacing_pt"] = int(rpr.get("spc")) / 100
        latin = rpr.find("a:latin", NS)
        if latin is not None:
            style["font"] = self._font(latin.get("typeface"))
        fill = rpr.find("a:solidFill", NS)
        if fill is not None:
            style["color"] = self._color(fill)
        return style

    def _level_style(self, lvl) -> dict:
        """Paragraph-level defaults (lvlNpPr): alignment, bullet, and default run style."""
        if lvl is None:
            return {}
        style = self._run_style(lvl.find("a:defRPr", NS))
        if lvl.get("algn"):
            style["align"] = lvl.get("algn")
        if lvl.find("a:buNone", NS) is not None:
            style["bullet"] = None
        bu = lvl.find("a:buChar", NS)
        if bu is not None:
            style["bullet"] = bu.get("char")
            color = self._color(lvl.find("a:buClr", NS))
            if color:
                style["bullet_color"] = color
        return style

    def _list_style(self, container) -> dict:
        """lstStyle or txStyles block -> {level: style} for levels 1-3."""
        if container is None:
            return {}
        levels = {}
        for n in (1, 2, 3):
            style = self._level_style(container.find(f"a:lvl{n}pPr", NS))
            if style:
                levels[n] = style
        return levels

    def _body_props(self, txbody) -> dict:
        body = txbody.find("a:bodyPr", NS) if txbody is not None else None
        if body is None:
            return {}
        props = {}
        if body.get("anchor"):
            props["anchor"] = {"t": "top", "ctr": "middle", "b": "bottom"}.get(body.get("anchor"), body.get("anchor"))
        insets = {k: inches(int(body.get(k))) for k in ("lIns", "tIns", "rIns", "bIns") if body.get(k) is not None}
        if insets:
            props["insets_in"] = insets
        if body.find("a:normAutofit", NS) is not None:
            props["autofit"] = "shrink"
        elif body.find("a:spAutoFit", NS) is not None:
            props["autofit"] = "grow_box"
        elif body.find("a:noAutofit", NS) is not None:
            props["autofit"] = "none"
        if body.get("wrap") == "none":
            props["wrap"] = False
        return props

    # ------------------------------------------------------------------ text content

    def _paragraphs(self, txbody) -> list[dict]:
        if txbody is None:
            return []
        paragraphs = []
        for p in txbody.findall("a:p", NS):
            ppr = p.find("a:pPr", NS)
            para = {"level": int(ppr.get("lvl")) + 1 if ppr is not None and ppr.get("lvl") else 1}
            para.update(self._level_style(ppr))
            runs = []
            for node in p:
                tag = etree.QName(node).localname
                if tag == "r":
                    run = {"text": node.find("a:t", NS).text or ""}
                    run.update(self._run_style(node.find("a:rPr", NS)))
                    runs.append(run)
                elif tag == "br":
                    runs.append({"text": "\n"})
                elif tag == "fld":
                    t = node.find("a:t", NS)
                    runs.append({"text": t.text if t is not None else "", "field": node.get("type")})
            para["runs"] = runs
            para["text"] = "".join(r["text"] for r in runs)
            paragraphs.append(para)
        return paragraphs

    @staticmethod
    def _plain_text(paragraphs: list[dict]) -> str:
        return "\n".join(p["text"] for p in paragraphs)

    # ------------------------------------------------------------------ shapes

    def _frame(self, shape) -> dict | None:
        if shape.left is None:
            return None
        frame = {"x": inches(shape.left), "y": inches(shape.top), "w": inches(shape.width), "h": inches(shape.height)}
        if getattr(shape, "rotation", 0):
            frame["rotation"] = shape.rotation
        return frame

    def _fill_and_line(self, el) -> dict:
        sppr = el.find("p:spPr", NS)
        out = {}
        if sppr is None:
            return out
        if sppr.find("a:solidFill", NS) is not None:
            out["fill"] = self._color(sppr.find("a:solidFill", NS))
        elif sppr.find("a:noFill", NS) is not None:
            out["fill"] = None
        if sppr.find("a:gradFill", NS) is not None:
            out["fill"] = "gradient"
        ln = sppr.find("a:ln", NS)
        if ln is not None:
            if ln.find("a:noFill", NS) is not None:
                out["line"] = None
            else:
                line = {"color": self._color(ln.find("a:solidFill", NS))}
                if ln.get("w"):
                    line["width_pt"] = round(int(ln.get("w")) / 12700, 2)
                dash = ln.find("a:prstDash", NS)
                if dash is not None and dash.get("val") != "solid":
                    line["dash"] = dash.get("val")
                out["line"] = line
        geom = sppr.find("a:prstGeom", NS)
        if geom is not None and geom.get("prst") not in ("rect", "line"):
            out["geometry"] = geom.get("prst")
        return out

    def _element(self, shape) -> dict:
        el = shape._element
        item = {"name": shape.name, "frame": self._frame(shape)}
        if shape.is_placeholder:
            pf = shape.placeholder_format
            item["placeholder"] = {"idx": pf.idx, "type": str(pf.type).split(".")[-1].split(" ")[0].lower()}

        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            item["kind"] = "group"
            item["children"] = [self._element(child) for child in shape.shapes]
            return item
        if getattr(shape, "has_table", False) and shape.has_table:
            item["kind"] = "table"
            item["table"] = self._table(shape)
            return item
        if getattr(shape, "has_chart", False) and shape.has_chart:
            item["kind"] = "chart"
            item["chart"] = self._chart(shape)
            return item
        if el.find(".//p:blipFill", NS) is not None:
            item["kind"] = "image"
            item["image"] = self._image(shape)
            return item
        if etree.QName(el).localname == "cxnSp" or shape.shape_type == MSO_SHAPE_TYPE.LINE:
            item["kind"] = "line"
            item.update(self._fill_and_line(el))
            return item

        txbody = el.find("p:txBody", NS)
        paragraphs = self._paragraphs(txbody)
        has_text = any(p["text"].strip() for p in paragraphs)
        item["kind"] = "text" if (has_text or shape.is_placeholder or shape.shape_type == MSO_SHAPE_TYPE.TEXT_BOX) else "shape"
        item.update(self._fill_and_line(el))
        if txbody is not None:
            body = self._body_props(txbody)
            if body:
                item["text_box"] = body
            levels = self._list_style(txbody.find("a:lstStyle", NS))
            if levels:
                item["level_styles"] = levels
            if has_text or shape.is_placeholder:
                item["paragraphs"] = paragraphs
                item["text"] = self._plain_text(paragraphs)
        return item

    def _table(self, shape) -> dict:
        table = shape.table
        rows = []
        for row in table.rows:
            cells = []
            for cell in row.cells:
                if cell.is_spanned:
                    cells.append({"spanned": True})
                    continue
                tc = cell._tc
                paragraphs = self._paragraphs(tc.find("a:txBody", NS))
                out = {"text": self._plain_text(paragraphs)}
                runs = [r for p in paragraphs for r in p["runs"] if r["text"].strip()]
                style = {k: v for k, v in (runs[0] if runs else {}).items() if k != "text"}
                if style:
                    out["style"] = style
                aligns = {p.get("align") for p in paragraphs if p.get("align")}
                if aligns:
                    out["align"] = aligns.pop()
                tcpr = tc.find("a:tcPr", NS)
                if tcpr is not None and tcpr.find("a:solidFill", NS) is not None:
                    out["fill"] = self._color(tcpr.find("a:solidFill", NS))
                if cell.is_merge_origin:
                    out["span"] = {"rows": cell.span_height, "cols": cell.span_width}
                cells.append(out)
            rows.append({"height_in": inches(row.height), "cells": cells})
        return {"column_widths_in": [inches(c.width) for c in table.columns], "rows": rows}

    def _chart(self, shape) -> dict:
        chart = shape.chart
        out = {"type": str(chart.chart_type).split(".")[-1].split(" ")[0].lower(), "has_legend": chart.has_legend}
        if chart.has_title and chart.chart_title.has_text_frame:
            out["title"] = chart.chart_title.text_frame.text
        try:
            out["value_axis_gridlines"] = chart.value_axis.has_major_gridlines
        except Exception:
            pass
        plots = []
        for plot in chart.plots:
            try:
                categories = [str(c) for c in plot.categories]
            except Exception:
                categories = []
            series = []
            for s in plot.series:
                sppr = s._element.find("c:spPr", NS)
                entry = {"name": s.name, "values": list(s.values)}
                if sppr is not None:
                    color = self._color(sppr.find("a:solidFill", NS)) or self._color(sppr.find("a:ln/a:solidFill", NS))
                    if color:
                        entry["color"] = color
                points = {}
                for dpt in s._element.findall("c:dPt", NS):
                    color = self._color(dpt.find(".//a:solidFill", NS))
                    if color:
                        points[int(dpt.find("c:idx", NS).get("val"))] = color
                if points:
                    entry["point_colors"] = points
                labels = s._element.find("c:dLbls", NS)
                if labels is not None:
                    show = labels.find("c:showVal", NS)
                    entry["data_labels"] = show is not None and show.get("val") == "1"
                series.append(entry)
            plots.append({"kind": etree.QName(plot._element).localname, "categories": categories, "series": series})
        out["plots"] = plots
        return out

    def _image(self, shape) -> dict:
        try:
            image = shape.image
        except Exception:
            return {"asset": None}
        digest = hashlib.sha1(image.blob).hexdigest()[:12]
        name = f"{digest}.{image.ext}"
        if digest not in self.assets:
            self.assets[digest] = {"id": digest, "file": name, "content_type": image.content_type, "bytes": len(image.blob)}
            if self.asset_dir:
                self.asset_dir.mkdir(parents=True, exist_ok=True)
                (self.asset_dir / name).write_bytes(image.blob)
        alt = shape._element.find(".//p:cNvPr", NS)
        out = {"asset": digest}
        if alt is not None and alt.get("descr"):
            out["alt"] = alt.get("descr")
        return out

    # ------------------------------------------------------------------ master, layouts, slides

    def _read_master(self, master) -> dict:
        tx = master._element.find("p:txStyles", NS)
        styles = {}
        if tx is not None:
            for key, tag in (("title", "p:titleStyle"), ("body", "p:bodyStyle"), ("other", "p:otherStyle")):
                styles[key] = self._list_style(tx.find(tag, NS))
        bg = master._element.find(".//p:bg", NS)
        return {
            "text_styles": styles,
            "background": self._color(bg),
            "elements": [self._element(shape) for shape in master.shapes],
        }

    def _read_layout(self, layout) -> dict:
        bg = layout._element.find(".//p:bg", NS)
        show_master = layout._element.get("showMasterSp")
        elements = []
        for shape in layout.shapes:
            item = self._element(shape)
            if shape.is_placeholder:
                item["effective_style"] = self._effective_style(shape, item)
            elements.append(item)
        return {
            "name": layout.name,
            "background": self._color(bg),
            "shows_master_elements": show_master != "0",
            "elements": elements,
        }

    def _effective_style(self, shape, item: dict) -> dict:
        """Level-1 style a placeholder's text takes: master text style, then master placeholder, then layout."""
        kind = item["placeholder"]["type"]
        family = "title" if kind in ("title", "center_title") else "other" if kind in ("footer", "slide_number", "date") else "body"
        master = self.prs.slide_masters[0]
        style = dict(self._read_master_cache(master).get(family, {}).get(1, {}))
        for ph in master.placeholders:
            if str(ph.placeholder_format.type) == str(shape.placeholder_format.type):
                style.update(self._list_style(ph._element.find(".//a:lstStyle", NS)).get(1, {}))
                break
        style.update(item.get("level_styles", {}).get(1, {}))
        if "font" not in style:
            style["font"] = self.fonts["heading" if family == "title" else "body"]
        style.update(item.get("text_box", {}))
        return style

    def _read_master_cache(self, master) -> dict:
        if not hasattr(self, "_master_styles"):
            tx = master._element.find("p:txStyles", NS)
            self._master_styles = {
                key: self._list_style(tx.find(tag, NS)) if tx is not None else {}
                for key, tag in (("title", "p:titleStyle"), ("body", "p:bodyStyle"), ("other", "p:otherStyle"))
            }
        return self._master_styles

    def _read_slide(self, number: int, slide) -> dict:
        notes = ""
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
            notes = slide.notes_slide.notes_text_frame.text.strip()
        out = {
            "number": number,
            "layout": slide.slide_layout.name,
            "elements": [self._element(shape) for shape in slide.shapes],
        }
        if slide._element.get("show") == "0":
            out["hidden"] = True
        if notes:
            out["speaker_notes"] = notes
        return out


def read_pptx(path: Path, asset_dir: Path | None = None) -> dict:
    return PptxReader(path, asset_dir).read()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pptx", type=Path)
    parser.add_argument("--out", type=Path, default=None, help="directory for <name>.json and assets/")
    args = parser.parse_args()
    out_dir = args.out or args.pptx.parent
    data = read_pptx(args.pptx, out_dir / "assets")
    target = out_dir / f"{args.pptx.stem}.json"
    target.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    sys.stdout.write(f"{target}  layouts={len(data['layouts'])} slides={len(data['slides'])} assets={len(data['assets'])}\n")


if __name__ == "__main__":
    main()
