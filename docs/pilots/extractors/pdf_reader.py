"""Read a PDF (a firm's brand or memo guide) into ordered text, tables and page images.

Per page it returns:
- text in reading order. Multi-column pages are read column by column: columns are found
  from the vertical gaps no text crosses, so it works for one, two or three columns alike.
- tables found on the page, as rows of cells (their text is not repeated in the page text).
- the page rendered as a PNG, for whatever the text cannot carry (colour swatches, diagrams).

It knows nothing about any firm: no headings, words or layouts are looked for.

Usage:
    python -m extractors.pdf_reader <guide.pdf> [--out <dir>] [--dpi 150]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pymupdf

POINTS_PER_INCH = 72
MIN_GAP_PT = 12          # vertical gutter between columns
FULL_WIDTH_SHARE = 0.6   # a block wider than this share of the text width spans columns


def _overlaps(a, b) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def _column_bounds(blocks: list[tuple], left: float, right: float) -> list[tuple[float, float]]:
    """Find columns as x-ranges separated by gaps that no narrow block crosses."""
    width = right - left
    narrow = [b for b in blocks if (b[2] - b[0]) < FULL_WIDTH_SHARE * width]
    if not narrow:
        return [(left, right)]
    covered = sorted((b[0], b[2]) for b in narrow)
    columns, start, end = [], covered[0][0], covered[0][1]
    for x0, x1 in covered[1:]:
        if x0 - end > MIN_GAP_PT:
            columns.append((start, end))
            start, end = x0, x1
        else:
            end = max(end, x1)
    columns.append((start, end))
    return columns


def _reading_order(blocks: list[tuple], page_width: float) -> list[tuple]:
    """Order blocks as a person reads: top to bottom; within a band between full-width blocks,
    each column top to bottom, columns left to right."""
    if not blocks:
        return []
    left = min(b[0] for b in blocks)
    right = max(b[2] for b in blocks)
    width = right - left
    columns = _column_bounds(blocks, left, right)

    def column_of(block):
        center = (block[0] + block[2]) / 2
        for i, (x0, x1) in enumerate(columns):
            if x0 - MIN_GAP_PT <= center <= x1 + MIN_GAP_PT:
                return i
        return 0

    spanning = sorted((b for b in blocks if (b[2] - b[0]) >= FULL_WIDTH_SHARE * width), key=lambda b: b[1])
    others = [b for b in blocks if (b[2] - b[0]) < FULL_WIDTH_SHARE * width]
    ordered, cursor = [], -1.0
    for wall in spanning + [None]:
        limit = wall[1] if wall else float("inf")
        band = [b for b in others if cursor <= b[1] < limit]
        ordered.extend(sorted(band, key=lambda b: (column_of(b), b[1], b[0])))
        if wall:
            ordered.append(wall)
            cursor = wall[1]
    return ordered


def _table_rows(page, table) -> list[list[str]]:
    # Text read from each cell's area keeps the spaces between words.
    rows = [[" ".join(page.get_textbox(cell).split()) if cell else "" for cell in row.cells] for row in table.rows]
    return _merge_continuations(_drop_empty_columns([r for r in rows if any(r)]))


def _tables(page) -> list[dict]:
    """Tables on the page. Ruled tables are read from their lines; a table whose rows are not
    separated by lines (bands of colour) comes out as one or two rows, so that area is read
    again from the position of its text and the reading with more rows is kept."""
    tables = []
    try:
        found = page.find_tables().tables
    except Exception:
        return tables
    for table in found:
        rows = _table_rows(page, table)
        if len(rows) <= 2:
            try:
                retry = page.find_tables(clip=table.bbox, horizontal_strategy="text").tables
            except Exception:
                retry = []
            for candidate in retry:
                candidate_rows = _table_rows(page, candidate)
                if len(candidate_rows) > len(rows):
                    rows = candidate_rows
        if len(rows) >= 2:
            tables.append({"bbox": tuple(round(v, 1) for v in table.bbox), "rows": rows})
    return tables


def _drop_empty_columns(rows: list[list[str]]) -> list[list[str]]:
    if not rows:
        return rows
    width = max(len(r) for r in rows)
    keep = [i for i in range(width) if any(i < len(r) and r[i] for r in rows)]
    return [[r[i] if i < len(r) else "" for i in keep] for r in rows]


def _merge_continuations(rows: list[list[str]]) -> list[list[str]]:
    """A cell that wraps onto a second line can come out as an extra row. Such a row has its key
    column empty (the column that holds text in most rows) and continues the row above it."""
    if len(rows) < 2:
        return rows
    body = rows[1:]
    filled = [sum(1 for r in body if i < len(r) and r[i]) for i in range(len(rows[0]))]
    key = next((i for i, n in enumerate(filled) if n >= len(body) / 2), 0)
    merged: list[list[str]] = [rows[0]]
    for row in body:
        if len(merged) > 1 and not row[key]:
            merged[-1] = [_join_cell(a, b) for a, b in zip(merged[-1], row)]
        else:
            merged.append(row)
    return merged


MIN_OVERLAP_WORDS = 3


def _join_cell(above: str, below: str) -> str:
    """Join a cell with its continuation line. A cell area can reach into the next line, so the
    text above may already hold the start (or all) of the continuation; that repeated part is kept
    once. Only an overlap of MIN_OVERLAP_WORDS words or more counts, so a short phrase that is truly
    written twice is left alone."""
    if not below:
        return above
    if below in above:
        return above
    a, b = above.split(), below.split()
    for size in range(min(len(a), len(b)), MIN_OVERLAP_WORDS - 1, -1):
        if a[-size:] == b[:size]:
            return " ".join(a + b[size:])
    return f"{above} {below}".strip()


def read_pdf(path: Path, image_dir: Path | None = None, dpi: int = 150) -> dict:
    path = Path(path)
    doc = pymupdf.open(str(path))
    pages = []
    for number, page in enumerate(doc, 1):
        tables = _tables(page)
        table_boxes = [t["bbox"] for t in tables]
        blocks = [b[:5] for b in page.get_text("blocks") if b[6] == 0 and b[4].strip()]
        blocks = [b for b in blocks if not any(_overlaps(b, box) for box in table_boxes)]
        # Each table takes part in the reading order as one block, so it stays under its heading.
        blocks += [(*t["bbox"], {"table": i}) for i, t in enumerate(tables)]
        ordered = _reading_order(blocks, page.rect.width)
        content = []
        for block in ordered:
            if isinstance(block[4], dict):
                content.append({"table": tables[block[4]["table"]]["rows"]})
            else:
                content.append({"text": " ".join(block[4].split())})

        image = None
        if image_dir:
            image_dir.mkdir(parents=True, exist_ok=True)
            image = f"{path.stem}_p{number}.png"
            page.get_pixmap(dpi=dpi).save(str(image_dir / image))

        pages.append({
            "number": number,
            "size_in": [round(page.rect.width / POINTS_PER_INCH, 2), round(page.rect.height / POINTS_PER_INCH, 2)],
            "content": content,
            "image": image,
        })
    return {"file": path.name, "title": doc.metadata.get("title") or None, "pages": pages}


def to_text(data: dict) -> str:
    out = [f"FILE {data['file']}" + (f"  TITLE {data['title']!r}" if data.get("title") else "")]
    for page in data["pages"]:
        out.append(f"\n=== PAGE {page['number']} ({page['size_in'][0]} x {page['size_in'][1]} in)"
                   + (f"  image={page['image']}" if page.get("image") else ""))
        for item in page["content"]:
            if "table" in item:
                out.append(f"[table, {len(item['table'])} rows]")
                out.extend("  " + " | ".join(row) for row in item["table"])
                out.append("[end table]")
            else:
                out.append(item["text"])
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--dpi", type=int, default=150)
    args = parser.parse_args()
    out_dir = args.out or args.pdf.parent
    data = read_pdf(args.pdf, out_dir / "pages", args.dpi)
    (out_dir / f"{args.pdf.stem}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    view = to_text(data)
    target = out_dir / f"{args.pdf.stem}.view.txt"
    target.write_text(view, encoding="utf-8")
    tables = sum(1 for p in data["pages"] for item in p["content"] if "table" in item)
    sys.stdout.write(f"{target}  pages={len(data['pages'])} tables={tables} chars={len(view)} ~tokens={len(view) // 4}\n")


if __name__ == "__main__":
    main()
