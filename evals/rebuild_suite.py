"""Measure 5: how closely a past memo can be rebuilt (structure; the past deals' records were not given).

The brief (§05): "Report, for each firm: ... how closely a past memo can be rebuilt". EVALS.md: rebuild each past
memo's deal from the spec and the record and compare slides, elements and figures. The firms gave their past
memos (as PowerPoint files) but not those deals' records, so the figures cannot be compared honestly; this
measures the structure the platform rebuilds from the firm's spec.

Per past memo: a memo is made through the API at each of the firm's stages and exported through the API; the stage
whose memo follows the past one best is the one compared (the stage a person would choose: on a tie, the one the
past memo is named after). PowerPoint opens both
files and reads every slide's layout and every box as it is drawn (place, font and size inherited or not), and
the two are compared:
  slides     the past memo's slides found in the rebuilt one (by the firm's layout): in the same order, or in another
             place (the firm's order today differs from that memo's)
  boxes      on those slides, the past memo's boxes found in the rebuilt one (a placeholder by its index; a
             table, chart or picture by kind and place)
  place      found boxes at the same place and size (within 0.1 in; a table's height follows its rows, which are
             the deal's, so a table is compared by place and width)
  type       found text boxes in the same font and size (within 0.5 pt)
  exhibits   the past memo's tables and charts found as tables and charts
Slides the past memo has that the firm's spec does not (one-offs a person made) are listed.

Needs PowerPoint (Windows). Usage (from the repo root):  backend/.venv/Scripts/python evals/rebuild_suite.py [--firm f]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from client import REPO, Person, Server, latest_spec_and_record  # noqa: E402

from sqlalchemy import select  # noqa: E402

from app.db import spec as S  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.services.painters.pptx import materials  # noqa: E402

PORT = 8014
WORK = REPO / "backend" / ".work" / "evals" / "rebuild"
PLACE_IN, SIZE_PT = 0.1, 0.5

READ = r"""
param($file)
$pp = New-Object -ComObject PowerPoint.Application
$pres = $pp.Presentations.Open($file, $true, $false, $false)
$slides = @()
foreach ($slide in $pres.Slides) {
  $shapes = @()
  foreach ($sh in $slide.Shapes) {
    $kind = 'shape'
    if ($sh.HasTable) { $kind = 'table' } elseif ($sh.HasChart) { $kind = 'chart' } elseif ($sh.Type -eq 13) { $kind = 'picture' } elseif ($sh.HasTextFrame -and $sh.TextFrame2.HasText) { $kind = 'text' } elseif ($sh.Type -eq 14 -and $sh.HasTextFrame) { $kind = 'text' }
    $font = $null; $size = $null
    if ($kind -eq 'text' -and $sh.TextFrame2.HasText) { $r = $sh.TextFrame2.TextRange.Characters(1, 1); $font = $r.Font.Name; $size = $r.Font.Size }
    $shapes += [pscustomobject]@{ kind = $kind; placeholder = ($sh.Type -eq 14); x = $sh.Left / 72; y = $sh.Top / 72; w = $sh.Width / 72; h = $sh.Height / 72; font = $font; size = $size; name = $sh.Name }
  }
  $slides += [pscustomobject]@{ layout = $slide.CustomLayout.Name; shapes = $shapes }
}
$pres.Close(); $pp.Quit()
$slides | ConvertTo-Json -Compress -Depth 4
"""


def read(pptx: Path) -> list[dict]:
    """Every slide's layout and every box as PowerPoint draws it."""
    script = WORK / "read.ps1"
    script.write_text(READ, encoding="utf-8")
    raw = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), str(pptx.resolve())],
                         capture_output=True, text=True, check=True, timeout=600).stdout
    slides = json.loads(raw, strict=False) if raw.strip() else []
    slides = slides if isinstance(slides, list) else [slides]
    for s in slides:
        s["shapes"] = s["shapes"] if isinstance(s["shapes"], list) else ([s["shapes"]] if s["shapes"] else [])
    return slides


def placeholders(pptx: Path) -> list[list[int | None]]:
    """Each slide's boxes' placeholder index (python-pptx: the index the layout gives them), in drawing order."""
    from pptx import Presentation
    out = []
    for slide in Presentation(str(pptx)).slides:
        out.append([sh.placeholder_format.idx if sh.is_placeholder else None for sh in slide.shapes])
    return out


def aligned(past: list[str], rebuilt: list[str]) -> list[tuple[int, int]]:
    """The slides of both in the same order, by layout (longest common subsequence)."""
    n, m = len(past), len(rebuilt)
    best = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            best[i][j] = best[i + 1][j + 1] + 1 if past[i] == rebuilt[j] else max(best[i + 1][j], best[i][j + 1])
    pairs, i, j = [], 0, 0
    while i < n and j < m:
        if past[i] == rebuilt[j]:
            pairs.append((i, j))
            i, j = i + 1, j + 1
        elif best[i + 1][j] >= best[i][j + 1]:
            i += 1
        else:
            j += 1
    return pairs


def compare(past_file: Path, rebuilt_file: Path) -> dict:
    past, rebuilt = read(past_file), read(rebuilt_file)
    past_ph, rebuilt_ph = placeholders(past_file), placeholders(rebuilt_file)
    pairs = aligned([s["layout"] for s in past], [s["layout"] for s in rebuilt])
    in_order = len(pairs)
    taken_past, taken_ours = {i for i, _ in pairs}, {j for _, j in pairs}
    for i, sl in enumerate(past):                          # the same layout elsewhere in the rebuilt memo: another order
        if i in taken_past:
            continue
        j = next((j for j, o in enumerate(rebuilt) if j not in taken_ours and o["layout"] == sl["layout"]), None)
        if j is not None:
            pairs.append((i, j))
            taken_past.add(i)
            taken_ours.add(j)
    counts = {"boxes": [0, 0], "place": [0, 0], "type": [0, 0], "exhibits": [0, 0]}
    misses = []
    for i, j in pairs:
        ours = rebuilt[j]["shapes"]
        ours_by_idx = {idx: sh for idx, sh in zip(rebuilt_ph[j], ours) if idx is not None}
        used = set()
        for idx, sh in zip(past_ph[i], past[i]["shapes"]):
            if sh["kind"] == "shape":                       # lines, rules and decoration: the layout's own
                continue
            if idx is not None:
                match = ours_by_idx.get(idx)
            else:                                           # a table, chart, picture or text box drawn on the slide
                near = [o for k, o in enumerate(ours) if k not in used and o["kind"] == sh["kind"]]
                match = min(near, key=lambda o: abs(o["x"] - sh["x"]) + abs(o["y"] - sh["y"])) if near else None
                if match is not None and abs(match["x"] - sh["x"]) + abs(match["y"] - sh["y"]) > 1.5:
                    match = None
            if match is not None:
                used.add(ours.index(match))
            counts["boxes"][1] += 1
            counts["boxes"][0] += match is not None
            if sh["kind"] in ("table", "chart"):
                counts["exhibits"][1] += 1
                counts["exhibits"][0] += match is not None and match["kind"] == sh["kind"]
            if match is None:
                misses.append(f"slide {i + 1} ({past[i]['layout']}): {sh['kind']} {sh['name']!r} not in the rebuilt memo")
                continue
            counts["place"][1] += 1
            keys = ("x", "y", "w") if sh["kind"] == "table" else ("x", "y", "w", "h")
            same_place = all(abs(match[k] - sh[k]) <= PLACE_IN for k in keys)
            counts["place"][0] += same_place
            if not same_place:
                misses.append(f"slide {i + 1}: {sh['name']!r} at {sh['x']:.2f},{sh['y']:.2f} {sh['w']:.2f}×{sh['h']:.2f} in the past memo, "
                              f"{match['x']:.2f},{match['y']:.2f} {match['w']:.2f}×{match['h']:.2f} rebuilt")
            if sh["kind"] == "text" and sh["font"] and match.get("font"):
                counts["type"][1] += 1
                same_type = sh["font"] == match["font"] and abs((sh["size"] or 0) - (match["size"] or 0)) <= SIZE_PT
                counts["type"][0] += same_type
                if not same_type:
                    misses.append(f"slide {i + 1}: {sh['name']!r} in {sh['font']} {sh['size']:g} pt in the past memo, {match['font']} "
                                  f"{match['size']:g} pt rebuilt")
    one_offs = [f"slide {i + 1} ({s['layout']})" for i, s in enumerate(past) if i not in taken_past]
    elsewhere = [f"slide {i + 1} ({past[i]['layout']}) is slide {j + 1}" for i, j in pairs[in_order:]]
    return {"slides": [len(pairs), len(past)], "in_order": in_order, "rebuilt_slides": len(rebuilt), **counts,
            "one_offs": one_offs, "elsewhere": elsewhere, "misses": misses}


def stages_of(firm: str) -> list[str]:
    with SessionLocal() as s:
        f = s.scalar(select(S.Firm).where(S.Firm.name == firm))
        v = s.scalars(select(S.SpecVersion).join(S.Spec).where(S.Spec.firm_id == f.id).order_by(S.SpecVersion.number.desc())).first()
        return [x.name for x in s.scalars(select(S.Stage).where(S.Stage.spec_version_id == v.id).order_by(S.Stage.position))]


def run_firm(server: Server, firm: str) -> dict:
    past_memos = materials(firm).get("memo") or []
    if not past_memos:
        return {"firm": firm, "memos": [], "note": "no past memos were given"}
    p = Person(server, f"alex@{firm}.demo")
    version, revision, _ = latest_spec_and_record(p)
    folder = WORK / firm
    folder.mkdir(parents=True, exist_ok=True)
    rebuilt, made = {}, []
    try:
        for stage in stages_of(firm):                      # a memo at each stage, through the API
            status, out = p.post("/memos", {"spec_version_id": version, "record_revision_id": revision, "stage": stage,
                                            "memo_date": "2026-10-15", "deal_team": "Lead (lead); Associate"})
            if status != 200:
                continue
            made.append(out["memo_id"])
            status, data = p.get(f"/memos/{out['memo_id']}/export")
            path = folder / f"rebuilt_{stage.replace(' ', '_')}.pptx"
            path.write_bytes(data)
            rebuilt[stage] = path
        results = []
        for past in past_memos:
            tried = {stage: compare(past, path) for stage, path in rebuilt.items()}
            named = lambda stage: stage.lower().replace(" ", "_") in past.stem.lower().replace(" ", "_")
            stage, best = max(tried.items(), key=lambda kv: (kv[1]["slides"][0], kv[1]["boxes"][0], named(kv[0])))
            results.append({"memo": past.stem, "stage": stage, **best})
            sys.stdout.write(f"  {firm} · {past.stem}: compared with the {stage} memo\n")
            sys.stdout.flush()
        return {"firm": firm, "memos": results}
    finally:
        from sqlalchemy import delete
        import uuid as _uuid
        from app.db.memo import Memo
        with SessionLocal() as s:
            s.execute(delete(Memo).where(Memo.id.in_([_uuid.UUID(m) for m in made])))
            s.commit()


def pct(pair) -> str:
    return f"{pair[0]}/{pair[1]} ({100 * pair[0] / pair[1]:.0f}%)" if pair[1] else "—"


def report(results: list[dict]) -> str:
    lines = [f"# Rebuilding past memos · {datetime.now():%Y-%m-%d %H:%M}", "",
             "Measure 5, structure: each past memo the firm gave, against the memo the platform builds from the firm's spec at the",
             "stage that follows it best (made and exported through the API); PowerPoint reads both files as it draws them.",
             "Figures are not compared: the past deals' records were not given (only Tidewell's).", "",
             "| Firm · past memo | Stage | Slides found (in the same order) | Boxes found | Same place | Same font and size | Tables and charts |",
             "|---|---|---|---|---|---|---|"]
    for r in results:
        if not r["memos"]:
            lines.append(f"| {r['firm']} | — | {r.get('note', '')} | | | | |")
        for m in r["memos"]:
            lines.append(f"| {r['firm']} · {m['memo']} | {m['stage']} | {pct(m['slides'])} ({m['in_order']} in order) | {pct(m['boxes'])} | {pct(m['place'])} | "
                         f"{pct(m['type'])} | {pct(m['exhibits'])} |")
    lines += ["", "## What differs", ""]
    for r in results:
        for m in r["memos"]:
            lines.append(f"**{r['firm']} · {m['memo']}** ({m['stage']}; past {m['slides'][1]} slides, rebuilt {m['rebuilt_slides']})")
            if m["elsewhere"]:
                lines.append(f"- in another place in the rebuilt memo (the firm's order differs): {'; '.join(m['elsewhere'])}")
            if m["one_offs"]:
                lines.append(f"- not in the rebuilt memo (one-offs, or sections this stage does not include): {', '.join(m['one_offs'])}")
            lines += [f"- {x}" for x in m["misses"][:25]]
            if len(m["misses"]) > 25:
                lines.append(f"- … and {len(m['misses']) - 25} more")
            if not m["one_offs"] and not m["misses"]:
                lines.append("- nothing")
            lines.append("")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--firm")
    args = parser.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    with SessionLocal() as s:
        firms = [f.name for f in s.scalars(select(S.Firm).order_by(S.Firm.name)) if not args.firm or f.name == args.firm]
    with Server(PORT) as server:
        results = [run_firm(server, f) for f in firms]
    text = report(results)
    path = REPO / "evals" / "reports" / f"{datetime.now():%Y-%m-%d_%H%M}_rebuild.md"
    path.write_text(text, encoding="utf-8")
    sys.stdout.write(text + f"\nreport: {path.relative_to(REPO)}\n")


if __name__ == "__main__":
    main()
