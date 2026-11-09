"""Whether text fits its box, measured in the firm's real fonts (the brief: "text measured in the real fonts ...
where it cannot fit the system says so").

Text is laid out the way a slide lays it out: words wrapped on the box's width (less its insets and indent) with
the font's own advance widths, lines as tall as the font size times the line spacing, paragraph spacing added.
When it does not fit, it gets smaller by half points down to the minimum size the firm allows for the box's role;
if it still does not fit, it does not fit, and the memo says so (a pending), never cut. Tables the same: each row
as tall as its tallest cell, the rows against the table's height.

One measure for every reader: the canvas and the PowerPoint draw the size it gives, the pendings list what does
not fit. Fonts are found by the family name inside the font files (the system's fonts, and a folder set with
FONTS_DIR); a family that is not installed is measured with a stand-in and the result says it is approximate.
Nothing here knows any firm.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

FONT_DIRS = [Path(p) for p in (os.environ.get("FONTS_DIR"), r"C:\Windows\Fonts", "/usr/share/fonts", "/Library/Fonts",
                                 str(Path.home() / "AppData/Local/Microsoft/Windows/Fonts")) if p]
STANDIN = "Arial"
UNIT = 100                    # fonts are loaded at 100 px: widths scale linearly to any size
PLACEHOLDER_INSET = (0.1, 0.05, 0.1, 0.05)   # PowerPoint's own text insets (left, top, right, bottom), inches
CELL_INSET = (0.1, 0.05)
SLACK = 1.02                  # rounding between this layout and PowerPoint's


@lru_cache(maxsize=1)
def _index() -> dict:
    """(family lower, bold, italic) → font file, from the names inside the files."""
    out: dict = {}
    for root in FONT_DIRS:
        if not root.exists():
            continue
        for path in list(root.rglob("*.ttf")) + list(root.rglob("*.TTF")) + list(root.rglob("*.otf")):
            try:
                family, style = ImageFont.truetype(str(path), 10).getname()
            except OSError:
                continue
            s = (style or "").lower()
            key = (family.lower(), "bold" in s, "italic" in s or "oblique" in s)
            out.setdefault(key, path)
    return out


@lru_cache(maxsize=256)
def _font(family: str, bold: bool, italic: bool) -> tuple[ImageFont.FreeTypeFont, bool]:
    """The font to measure with, and whether it is the family itself (False: a stand-in)."""
    idx = _index()
    for fam, exact in ((family or STANDIN, True), (STANDIN, False)):
        f = fam.lower()
        for key in ((f, bold, italic), (f, bold, False), (f, False, italic), (f, False, False)):
            if key in idx:
                return ImageFont.truetype(str(idx[key]), UNIT), exact and fam.lower() == (family or STANDIN).lower()
    return ImageFont.load_default(), False


def width_pt(text: str, family: str, size: float, bold=False, italic=False) -> float:
    font, _ = _font(family, bool(bold), bool(italic))
    return font.getlength(text) * size / UNIT


def lines_of(text: str, family: str, size: float, width: float, bold=False, italic=False) -> int:
    """How many lines a paragraph takes at this width (points), words wrapped as a slide wraps them."""
    if width <= 0:
        return 999
    count = 0
    for hard in (text or "").split("\n"):
        words = re.split(r"(\s+)", hard)
        line, n = "", 1
        for w in words:
            trial = line + w
            if line and width_pt(trial.rstrip(), family, size, bold, italic) > width:
                n += 1
                line = w.lstrip()
                while width_pt(line, family, size, bold, italic) > width and len(line) > 1:   # a word longer than the line
                    n += 1
                    line = line[max(1, int(len(line) * width / max(1.0, width_pt(line, family, size, bold, italic)))):]
            else:
                line = trial
        count += n
    return count


@dataclass
class Para:
    text: str
    font: str | None
    size: float | None
    bold: bool | None = None
    italic: bool | None = None
    line_spacing: float | None = None
    space_before: float | None = None
    space_after: float | None = None
    indent_in: float | None = None


@dataclass
class Fit:
    fits: bool
    size_pt: float | None          # the size to draw at, when smaller than the box's own (shrunk to fit)
    needed_in: float               # the height the text takes at that size
    approximate: bool              # a font was not installed: measured with a stand-in
    scale: float = 1.0             # size_pt over the box's own largest size: every paragraph drawn that much smaller

    def json(self) -> dict:
        return {"fits": self.fits, "size_pt": self.size_pt, "scale": round(self.scale, 4), "needed_in": self.needed_in,
                "approximate": self.approximate}


def _height(paras: list[Para], width_in: float, scale: float) -> tuple[float, bool]:
    total, exact_all = 0.0, True
    for i, p in enumerate(paras):
        size = (p.size or 12) * scale
        _, exact = _font(p.font or STANDIN, bool(p.bold), bool(p.italic))
        exact_all &= exact
        w = (width_in - (p.indent_in or 0)) * 72
        n = lines_of(p.text, p.font or STANDIN, size, w, p.bold, p.italic) if p.text else 1
        spacing = p.line_spacing * 1.2 if p.line_spacing and p.line_spacing < 3 else 1.2
        line = size * spacing
        total += n * line + ((p.space_before or 0) if i else 0) + (p.space_after or 0 if i < len(paras) - 1 else 0)
    return total / 72, exact_all


def insets_of(el) -> tuple:
    """A box's text insets (left, top, right, bottom): its template's; PowerPoint's own for a placeholder that has
    none kept; none for a drawn box (the painter draws them without)."""
    kept = (el.inset_l_in, el.inset_t_in, el.inset_r_in, el.inset_b_in)
    if all(v is not None for v in kept):
        return kept
    return PLACEHOLDER_INSET if el.placeholder_idx is not None else (0, 0, 0, 0)


def fit_text(paras: list[Para], w_in: float, h_in: float, min_size: float | None, inset: tuple = (0, 0, 0, 0)) -> Fit:
    """Whether the paragraphs fit the box, shrinking towards the role's minimum when the firm allows it."""
    width, height = w_in - inset[0] - inset[2], h_in - inset[1] - inset[3]
    paras = [p for p in paras if p.text is not None]
    if not paras or not any(p.text.strip() for p in paras):
        return Fit(True, None, 0.0, False)
    need, exact = _height(paras, width, 1.0)
    if need <= height * SLACK:
        return Fit(True, None, round(need, 3), not exact)
    base = max((p.size or 12) for p in paras)
    floor = min_size if min_size and min_size < base else None
    if floor:
        size = base - 0.5
        while size >= floor - 1e-6:
            need, exact = _height(paras, width, size / base)
            if need <= height * SLACK:
                return Fit(True, round(size, 1), round(need, 3), not exact, size / base)
            size -= 0.5
        need, exact = _height(paras, width, floor / base)
        return Fit(False, round(floor, 1), round(need, 3), not exact, floor / base)
    return Fit(False, None, round(need, 3), not exact)


def fit_table(rows: list[list[Para]], col_widths_in: list[float], h_in: float, min_size: float | None,
              row_min_in: list[float] | None = None) -> Fit:
    """Whether a table's rows fit its height: each row as tall as its tallest cell."""
    if not rows:
        return Fit(True, None, 0.0, False)
    base = max((p.size or 10) for r in rows for p in r if p and p.text) if any(p and p.text for r in rows for p in r) else 10

    def total(scale: float) -> tuple[float, bool]:
        h, exact_all = 0.0, True
        for i, r in enumerate(rows):
            tallest = 0.0
            for c, p in enumerate(r):
                if p is None or not p.text:
                    continue
                w = (col_widths_in[c] if c < len(col_widths_in) else 1.0) - 2 * CELL_INSET[0]
                hh, exact = _height([p], w, scale)
                exact_all &= exact
                tallest = max(tallest, hh + 2 * CELL_INSET[1])
            h += max(tallest, (row_min_in[i] if row_min_in and i < len(row_min_in) and row_min_in[i] else 0))
        return h, exact_all

    need, exact = total(1.0)
    if need <= h_in * SLACK:
        return Fit(True, None, round(need, 3), not exact)
    if min_size and min_size < base:
        size = base - 0.5
        while size >= min_size - 1e-6:
            need, exact = total(size / base)
            if need <= h_in * SLACK:
                return Fit(True, round(size, 1), round(need, 3), not exact, size / base)
            size -= 0.5
        need, exact = total(min_size / base)
        return Fit(False, round(min_size, 1), round(need, 3), not exact, min_size / base)
    return Fit(False, None, round(need, 3), not exact)
