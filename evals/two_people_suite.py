"""Two people editing the same memo at once, in repeated rounds that include the PowerPoint round trip.

The brief (§05): "Run it through repeated round trips with two people editing. Report, for each firm: edits
that survive and conflicts surfaced rather than lost." This suite starts the server, connects two people (A,
B) to the memo's room over websockets and, every round:

  live       A and B edit different boxes and commit at the same moment (both applied, nobody blocked);
             both reach for the same box at the same moment (exactly one gets it, the other is told);
             B writes into the box A holds (refused); A edits from an older view (kept aside, with a
             conflict); A types over a figure (refused, everyone told)
  PowerPoint the memo is exported; B edits box X in the app after the export; the file edits X too (kept
             aside, B's text stays) and box Y nobody touched in the app (applied)

Then the memo is exported again and checked: every applied edit is in it, nothing refused or kept aside
overwrote anything, every kept-aside edit has its conflict, and every action has its logged result.

Usage (from the repo root):  backend/.venv/Scripts/python evals/two_people_suite.py [--rounds 5] [--firm f]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

import websockets  # noqa: E402
from pptx import Presentation  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from app.db import spec as S  # noqa: E402
from app.db.history import Conflict  # noqa: E402
from app.db.memo import Element  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.services.generator.memo import Generator  # noqa: E402
from app.services.memo_data import MemoData  # noqa: E402
from app.services.roundtrip import SHAPE, export, import_pptx, read  # noqa: E402

PORT = 8011
WORK = REPO / "backend" / ".work" / "evals" / "two_people"
off = asyncio.to_thread                 # slow calls run in a thread, so the sockets keep answering the server's pings


def start_server() -> subprocess.Popen:
    proc = subprocess.Popen([str(REPO / "backend" / ".venv" / "Scripts" / "uvicorn"), "app.main:app", "--port", str(PORT)],
                            cwd=REPO / "backend", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://localhost:{PORT}/health", timeout=2)
            return proc
        except Exception:
            time.sleep(0.5)
    proc.kill()
    raise RuntimeError("the server did not start")


def boxes(memo_id, exported: Path) -> tuple[list[str], list[tuple[str, str, str]]]:
    """Free-text boxes (the firm places them, people write them, no chip) and boxes with a figure, among the
    boxes the file shows as the memo's own (master boxes such as footers are not edited in the memo)."""
    painted = {eid for sl in read(exported)["slides"] for eid in sl["shapes"]}
    with SessionLocal() as s:
        memo = MemoData(s, memo_id)                       # the whole memo in a few queries, not one per paragraph
        free, figures = [], []
        for e in memo.elements:
            if e.type != "text" or e.firm_lock != "content":
                continue
            segs = [ss for _, ss in memo.paragraphs(element_id=e.id)]
            chips = [x for ss in segs for x in ss if x.kind == "chip"]
            numbers = [x for x in chips if any(c.isdigit() for c in x.text or "")]
            if numbers:
                figures.append((str(e.id), "\n".join("".join(x.text for x in ss) for ss in segs), numbers[0].text))
            elif segs and not chips:
                free.append(str(e.id))
    return [x for x in free if x in painted], [f for f in figures if f[0] in painted]


def revision(eid: str) -> int:
    with SessionLocal() as s:
        return s.get(Element, eid).revision


def conflicts(memo_id) -> int:
    with SessionLocal() as s:
        return s.scalar(select(func.count()).select_from(Conflict).where(Conflict.memo_id == memo_id))


async def until(ws, pred, timeout=15):
    async def loop():
        while True:
            m = json.loads(await ws.recv())
            if pred(m):
                return m
    return await asyncio.wait_for(loop(), timeout)


def result_of(who, target):
    return lambda m: m["type"] == "operation" and m["by"] == who and m["target"] == str(target)


def set_text(target, base, text):
    return json.dumps({"type": "op", "op": {"type": "set_text", "target": target, "base_revision": base, "payload": {"text": text}}})


async def run_round(memo_id, firm: str, n: int, a, b, A: str, B: str, folder: Path, log: list, expect_text: dict) -> None:
    exported, edited = folder / f"r{n}_export.pptx", folder / f"r{n}_edited.pptx"
    await off(export, memo_id, exported)
    free, figures = await off(boxes, memo_id, exported)
    pick = lambda k: free[(n * 7 + k) % len(free)]
    box_a, box_b, race, stale, x_box, y_box = (pick(k) for k in range(6))

    def record(action, expected, got, detail=""):
        log.append({"round": n, "action": action, "expected": expected, "got": got, "detail": detail})

    # 1. different boxes, committed at the same moment
    await a.send(json.dumps({"type": "take", "element_id": box_a}))
    await b.send(json.dumps({"type": "take", "element_id": box_b}))
    ra, rb = await off(revision, box_a), await off(revision, box_b)
    ta, tb = f"A wrote this in round {n}", f"B wrote this in round {n}"
    await asyncio.gather(a.send(set_text(box_a, ra, ta)), b.send(set_text(box_b, rb, tb)))
    ma, mb = await until(b, result_of(A, box_a)), await until(a, result_of(B, box_b))
    record("A and B edit different boxes at once", "applied+applied", f"{ma['status']}+{mb['status']}")
    if ma["status"] == "applied":
        expect_text[box_a] = ta
    if mb["status"] == "applied":
        expect_text[box_b] = tb
    await asyncio.gather(a.send(json.dumps({"type": "release", "element_id": box_a})), b.send(json.dumps({"type": "release", "element_id": box_b})))

    # 2. both reach for the same box at the same moment: exactly one gets it
    await asyncio.gather(a.send(json.dumps({"type": "take", "element_id": race})), b.send(json.dumps({"type": "take", "element_id": race})))
    taken = await until(a, lambda m: m["type"] == "people" and any(p["editing"] == race for p in m["people"]))
    winner_email = next(p["email"] for p in taken["people"] if p["editing"] == race)
    winner, loser = (a, b) if winner_email == A else (b, a)
    told = await until(loser, lambda m: m["type"] == "box_taken" and m["element_id"] == race)
    record("A and B take the same box at once", "one winner, other told", "one winner, other told" if told["by"] == winner_email else "two winners")
    tw = f"{winner_email.split('@')[0]} won the race in round {n}"
    await winner.send(set_text(race, await off(revision, race), tw))
    mw = await until(loser, result_of(winner_email, race))
    record("the winner commits the raced box", "applied", mw["status"])
    if mw["status"] == "applied":
        expect_text[race] = tw

    # 3. the loser writes into the box the winner still holds
    loser_email = B if winner_email == A else A
    await loser.send(set_text(race, await off(revision, race), "should not land"))
    ml = await until(loser, result_of(loser_email, race))
    record("write into a box someone else holds", "refused", ml["status"], ml.get("reason") or "")
    await winner.send(json.dumps({"type": "release", "element_id": race}))

    # 4. an edit from an older view: kept aside, with its conflict
    before = await off(conflicts, memo_id)
    await a.send(set_text(stale, max(await off(revision, stale) - 1, 0) if await off(revision, stale) > 1 else 0, "an edit from an older view"))
    ms = await until(a, result_of(A, stale))
    record("edit from an older view", "kept_aside", ms["status"])
    record("the kept-aside edit has a conflict", "yes", "yes" if await off(conflicts, memo_id) == before + 1 else "no")

    # 5. type over a figure: refused, and the other person is told
    if figures:
        fid, full, chip = figures[n % len(figures)]
        await a.send(set_text(fid, await off(revision, fid), full.replace(chip, "".join("9" if c.isdigit() else c for c in chip), 1)))
        mf = await until(b, result_of(A, fid))
        record("type over a figure (other person told)", "refused", mf["status"])
        expect_text.setdefault(fid, None)
        expect_text[("figure", fid)] = chip

    # 6. PowerPoint while the other person keeps editing in the app
    await off(export, memo_id, exported)                          # again: the live edits above are in this file
    tx_app = f"B edited X in the app after the export, round {n}"
    await b.send(json.dumps({"type": "take", "element_id": x_box}))
    await b.send(set_text(x_box, await off(revision, x_box), tx_app))
    mx = await until(b, result_of(B, x_box))
    await b.send(json.dumps({"type": "release", "element_id": x_box}))
    if mx["status"] == "applied":
        expect_text[x_box] = tx_app
    prs = Presentation(str(exported))
    ty = f"Y edited in PowerPoint, round {n}"
    for slide in prs.slides:
        for shape in slide.shapes:
            m = SHAPE.match(shape.name or "")
            if m and m.group(1) in (x_box, y_box) and shape.has_text_frame and shape.text_frame.paragraphs[0].runs:
                shape.text_frame.paragraphs[0].runs[0].text = "X edited in PowerPoint" if m.group(1) == x_box else ty
                for p in shape.text_frame.paragraphs[1:]:
                    for r in p.runs:
                        r.text = ""
    prs.save(str(edited))
    before = await off(conflicts, memo_id)
    imported = await off(import_pptx, memo_id, edited, actor_email=A)
    by_target = {r["target"]: r["status"] for r in imported["results"]}
    record("PowerPoint edit of a box changed in the app after the export", "kept_aside", by_target.get(x_box, "missing"))
    record("PowerPoint edit of a box nobody touched", "applied", by_target.get(y_box, "missing"))
    if by_target.get(y_box) == "applied":
        expect_text[y_box] = ty
    kept = sum(1 for r in imported["results"] if r["status"] == "kept_aside")
    record("every kept-aside import has a conflict", "yes", "yes" if await off(conflicts, memo_id) - before == kept else "no")
    record("nothing found in the file without an operation (lost)", "0", str(imported["changes"] - len(imported["results"])))


def survives(memo_id, folder: Path, n: int, expect_text: dict, log: list) -> None:
    """Export again: every applied edit is there, and no figure was overwritten."""
    after = folder / f"r{n}_after.pptx"
    export(memo_id, after)
    shown = {}
    for sl in read(after)["slides"]:
        for eid, shape in sl["shapes"].items():
            shown.setdefault(eid, (shape.get("text") or "").lower())
    for key, want in expect_text.items():
        if want is None:
            continue
        if isinstance(key, tuple):                         # a figure someone tried to type over must be unchanged
            ok = want.lower() in shown.get(key[1], "")
            log.append({"round": n, "action": "figure unchanged after the round", "expected": "yes", "got": "yes" if ok else "no"})
        else:
            ok = shown.get(key, "").startswith(want.lower())
            log.append({"round": n, "action": "applied edit present after the round", "expected": "yes", "got": "yes" if ok else "no",
                        "detail": "" if ok else f"{key[:8]} shows {shown.get(key, '')[:50]!r}"})


async def run_firm(firm: str, rounds: int) -> dict:
    with SessionLocal() as s:
        f = s.scalar(select(S.Firm).where(S.Firm.name == firm))
        version = s.scalar(select(S.SpecVersion).join(S.Spec).where(S.Spec.firm_id == f.id).order_by(S.SpecVersion.number.desc()))
        stages = s.scalars(select(S.Stage).where(S.Stage.spec_version_id == version.id)).all()
        stage = max(stages, key=lambda st: len(s.scalars(select(S.StageSection).where(S.StageSection.stage_id == st.id, S.StageSection.required)).all()))
        memo_id = Generator(s, firm, stage.name, "2026-10-15", "Lead (lead); Associate").run()["memo_id"]
    A, B = f"alex@{firm}.demo", f"sam@{firm}.demo"
    folder = WORK / firm
    folder.mkdir(parents=True, exist_ok=True)
    url = f"ws://localhost:{PORT}/memos/{memo_id}/live?email="
    log, expect_text = [], {}
    a, b = await websockets.connect(url + A), await websockets.connect(url + B)
    await until(a, lambda m: m["type"] == "people" and len(m["people"]) == 2)
    first = folder / "r0_export.pptx"
    await off(export, memo_id, first)
    free, _ = await off(boxes, memo_id, first)
    for n in range(1, rounds + 1):
        try:
            await run_round(memo_id, firm, n, a, b, A, B, folder, log, expect_text)
        except asyncio.TimeoutError:
            log.append({"round": n, "action": "round finished", "expected": "yes", "got": "timed out"})
        await off(survives, memo_id, folder, n, expect_text, log)
    await a.close()
    await b.close()
    return {"firm": firm, "stage": stage.name, "rounds": rounds, "free_boxes": len(free), "log": log}


def report(results: list[dict]) -> str:
    def stat(r, action_prefix):
        rows = [x for x in r["log"] if x["action"].startswith(action_prefix)]
        ok = sum(1 for x in rows if x["got"] == x["expected"])
        return f"{100 * ok / len(rows):.0f}% ({ok}/{len(rows)})" if rows else "–"
    out = [f"# Two people editing · {datetime.now():%Y-%m-%d %H:%M}", "",
           "Two people (A, B) in the same memo's room over websockets, in repeated rounds: live edits at the same",
           "moment, a race for the same box, writing into a held box, an edit from an older view, typing over a figure,",
           "and a PowerPoint round trip while the other person keeps editing in the app.", "",
           "| | " + " | ".join(f"{r['firm']} ({r['stage']}, {r['rounds']} rounds)" for r in results) + " |",
           "|---|" + "---|" * len(results)]
    rows = [
        ("All actions as expected", ""),
        ("Different boxes at once: both applied", "A and B edit different boxes"),
        ("Same box at once: exactly one winner, the other told", "A and B take the same box"),
        ("Writing into a held box refused", "write into a box"),
        ("Edit from an older view kept aside, with its conflict", "edit from an older view"),
        ("Typing over a figure refused, the other person told", "type over a figure"),
        ("PowerPoint edit of a box changed in the app since the export kept aside", "PowerPoint edit of a box changed"),
        ("**Measure 1** · applied edits present after each round", "applied edit present"),
        ("**Measure 1** · figures unchanged after typing over", "figure unchanged"),
        ("**Measure 1** · every kept-aside edit has a conflict", "every kept-aside"),
        ("**Measure 1** · lost (changes with no operation)", "nothing found"),
    ]
    for label, prefix in rows:
        out.append(f"| {label} | " + " | ".join(stat(r, prefix) for r in results) + " |")
    out += ["", "## Where it breaks", ""]
    lines = [f"- {r['firm']} · round {x['round']} · {x['action']}: expected {x['expected']}, got {x['got']}"
             + (f" ({x['detail']})" if x.get("detail") else "")
             for r in results for x in r["log"] if x["got"] != x["expected"]]
    out += lines or ["Nothing broke."]
    out += ["", "## Not covered yet", "",
            "- Moving and resizing boxes live (drag) and adding boxes on free slides: they come with the canvas (build step 9).",
            "- Catching up after a reconnection: the sequence numbers exist; the replay is not built.",
            "- The agent as a third editor: build step 7."]
    small = [r["firm"] for r in results if r["free_boxes"] < 6]
    if small:
        out.append(f"- {', '.join(small)}: fewer than six free-text boxes, so some rounds reuse a box for two actions.")
    return "\n".join(out) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--firm")
    args = parser.parse_args()
    with SessionLocal() as s:
        firms = [f.name for f in s.scalars(select(S.Firm).order_by(S.Firm.name))]
    server = start_server()
    try:
        results = [asyncio.run(run_firm(f, args.rounds)) for f in firms if not args.firm or f == args.firm]
    finally:
        server.kill()
    text = report(results)
    path = REPO / "evals" / "reports" / f"{datetime.now():%Y-%m-%d_%H%M}_two_people.md"
    path.write_text(text, encoding="utf-8")
    sys.stdout.write(text + f"\nreport: {path.relative_to(REPO)}\n")


if __name__ == "__main__":
    main()
