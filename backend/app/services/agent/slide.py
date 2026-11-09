"""The memo's agent: a person asks for an edit of one slide in words; the agent proposes it; the person confirms.

Simple and bounded on purpose. One request works on one slide (or one box of it). Code builds everything the
model sees, and nothing else: the slide's boxes (their text, with the figures in them written as {{concept}}),
what the firm's spec says goes in each box and on this section, the firm's rules, and the figures the deal
record has for this firm (each concept with its value as the firm writes it). The model answers with a reply
and edits in JSON. Code turns them into door operations and checks each one with the door without applying
it; anything wrong (a figure typed instead of asked for by name, a box it may not edit, a figure typed over)
goes back to the model to fix, at most twice. What passes becomes a proposal the person applies or discards;
the agent never applies anything. Nothing here knows any firm.
"""
from __future__ import annotations

import re
import time
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import spec as S
from app.db.history import AgentRun, ChatMessage, ChatThread
from app.db.memo import Element
from app.db.record import RecordRevision
from app.db.session import SessionLocal
from app.services import proposals
from app.services.figures import Figures
from app.services.memo_data import MemoData
from app.services.onboarding.llm import call_with_checks
from app.services.operations.door import MARKER, Door

MAX_EDITS = 12
# an amount typed as text: a currency sign before a number, or a number with %, x, k, m or bn after it
TYPED_FIGURE = re.compile(r"[£€$]\s?\d|\d(?:[\d,.]*\d)?\s?(?:%|x\b|k\b|m\b|bn\b)", re.I)

INSTRUCTIONS = """You help a person edit one slide of an investment memo, following the firm's own spec.

You get the slide's boxes (id, what the box is, whether you may edit it, its text now), what the firm's spec
puts in each box and on this section, the firm's rules, and the FIGURES the deal record has for this memo:
each with its name and its value as the firm writes it.

Figures are never typed. Write a figure as {{name}} with a name from FIGURES; the system writes its value. A
figure already in a box appears as {{name}}: keep it as {{name}} where it should stay; leave it out to remove it.
Never write an amount yourself (no £, €, $, %, x, m, bn, k with a number), and never a figure that is not in
FIGURES. If the person asks to change a figure's value, make no edit and say that values change in the deal
record.

Text in ‹model: …› marks what the firm expects the model to write there: replace it with that text. Text in
‹person: …› is for a person: leave it unless asked. Edit only boxes marked editable, only what was asked, in
the firm's style and in the memo's language; keep each box to what its spec says it holds, short enough for it.

Return JSON only:
{"reply": "<one or two sentences to the person: what you propose, or why you cannot>",
 "edits": [{"box": "<box id>", "text": "<the box's whole new text; one line per paragraph>"},
           {"box": "<table box id>", "row": <row>, "col": <col>, "text": "<the cell's new text>"}]}
Return "edits": [] when nothing should change."""


def _box_text(paragraphs) -> str:
    out = []
    for _, segs in paragraphs:
        line = ""
        for x in segs:
            line += f"{{{{{x.concept_name}}}}}" if x.kind == "chip" and x.concept_name and not x.text.startswith("[no data") \
                else ("" if x.kind == "page_number" else x.text or "")
        out.append(line)
    return "\n".join(out)


def context(session: Session, memo_id, slide_id: str | None, element_id: str | None) -> dict:
    d = MemoData(session, memo_id)
    memo = d.memo
    slide = next((x for x in d.slides if str(x.id) == str(slide_id)), d.slides[0])
    firm = session.get(S.Firm, memo.firm_id)
    revision = session.get(RecordRevision, memo.record_revision_id)
    figures = Figures(session, firm, d.version, revision)
    q = session.scalars
    section = session.get(S.Section, slide.section_id) if slide.section_id else None
    recipe = {}
    origins = [e.origin_element_id for e in d.elements_of.get(slide.id, []) if e.origin_element_id]
    concepts = {c.id: c.name for c in figures.concepts.values()}
    writable = set()                                 # boxes whose recipe has something to write (not only fixed words or memo data)
    for r in q(select(S.RecipeSegment).where(S.RecipeSegment.element_id.in_(origins)).order_by(S.RecipeSegment.paragraph, S.RecipeSegment.position)):
        if r.kind not in ("fixed", "memo_data"):
            writable.add(r.element_id)
        part = {"fixed": f'"{r.text}"', "figure": f"figure {concepts.get(r.concept_id)}", "fact": f"fact {concepts.get(r.concept_id)}",
                "memo_data": f"memo data {concepts.get(r.concept_id)}", "model_writes": f"model writes: {r.what}",
                "person_writes": f"person writes: {r.what}", "conditional": f"if {r.when_rule}: {r.text}",
                "table": "a table", "chart": "a chart"}.get(r.kind, r.kind)
        recipe.setdefault(r.element_id, []).append(part)
    boxes = []
    for e in sorted(d.elements_of.get(slide.id, []), key=lambda e: (e.y_in or 0, e.x_in or 0)):
        if e.type not in ("text", "table"):
            continue
        editable = e.firm_lock != "fixed" and (element_id is None or str(e.id) == str(element_id))             and (e.origin_element_id not in recipe or e.origin_element_id in writable)
        box = {"id": str(e.id), "what": e.name or e.role, "role": e.role, "editable": editable,
               "spec": "; ".join(recipe.get(e.origin_element_id, [])) or e.description}
        if e.type == "table" and e.id in d.table_of:
            t = d.table_of[e.id]
            box["table"] = [{"row": c.row, "col": c.col, "text": _box_text(d.paragraphs(cell_id=c.id)),
                             "editable": editable and c.kind != "fixed" and not any(x.kind == "chip" for _, ss in d.paragraphs(cell_id=c.id) for x in ss)}
                            for c in sorted(d.cells_of.get(t.id, []), key=lambda c: (c.row, c.col))]
        else:
            box["text"] = _box_text(d.paragraphs(element_id=e.id))
        boxes.append(box)
    available = []
    for name, b in figures.bindings.items():
        if b.kind == "missing" or name not in figures.concept_named:
            continue
        try:
            chip = figures.chip(name)
        except ValueError:
            continue
        concept = figures.concept_named[name]
        available.append({"name": name, "value": chip["text"], "about": (concept.description or "")[:120]})
    rules = [r.says for r in q(select(S.Rule).where(S.Rule.spec_version_id == d.version.id,
                                                   (S.Rule.section_id.is_(None)) | (S.Rule.section_id == slide.section_id)))]
    return {"memo": memo, "slide": slide, "boxes": boxes, "figures": available, "rules": rules,
            "section": section, "spec_name": session.get(S.Spec, d.version.spec_id).name}


def package(ctx: dict, request: str, target: str | None) -> str:
    section = ctx["section"]
    lines = [f"MEMO: {ctx['memo'].title} (the firm's {ctx['spec_name']})",
             f"SLIDE {ctx['slide'].position + 1}: section {section.name if section else '—'}"
             + (f"; the firm says it must contain: {section.must_contain}" if section and section.must_contain else ""),
             "", "BOXES"]
    for b in ctx["boxes"]:
        lines.append(f"- box {b['id']} · {b['what']} · {'editable' if b['editable'] else 'not editable'} · spec: {b['spec'] or '—'}")
        if "table" in b:
            for c in b["table"]:
                lines.append(f"    cell row {c['row']} col {c['col']}{' (editable)' if c['editable'] else ''}: {c['text']}")
        else:
            lines.append("    text now:\n" + "\n".join(f"      {x}" for x in (b["text"] or "(empty)").split("\n")))
    lines += ["", "FIGURES (name = value as the firm writes it · what it is)"]
    lines += [f"- {f['name']} = {f['value']} · {f['about']}" for f in ctx["figures"]]
    lines += ["", "FIRM RULES"] + [f"- {r}" for r in ctx["rules"][:40]]
    lines += ["", f"THE PERSON ASKS{' (about box ' + target + ')' if target else ''}: {request}"]
    return "\n".join(lines)


def to_operations(session: Session, memo_id, ctx: dict, edits: list) -> tuple[list[dict], list[str]]:
    """The model's edits as door operations, and what is wrong with them (checked by the door, not applied)."""
    errors, ops = [], []
    boxes = {b["id"]: b for b in ctx["boxes"]}
    if len(edits) > MAX_EDITS:
        errors.append(f"at most {MAX_EDITS} edits per request")
    for i, e in enumerate(edits[:MAX_EDITS]):
        box = boxes.get(str(e.get("box")))
        where = f"edit {i + 1} (box {e.get('box')})"
        if box is None:
            errors.append(f"{where}: no such box on this slide")
            continue
        text = str(e.get("text") or "")
        typed = [m.group(0) for m in TYPED_FIGURE.finditer(MARKER.sub("", text))]
        if typed:
            errors.append(f"{where}: amounts are never typed ({', '.join(typed[:3])}); use {{{{name}}}} from FIGURES or leave it out")
            continue
        if "row" in e or "col" in e:
            cell = next((c for c in box.get("table", []) if c["row"] == e.get("row") and c["col"] == e.get("col")), None)
            if cell is None or not cell["editable"]:
                errors.append(f"{where}: cell row {e.get('row')} col {e.get('col')} cannot be edited")
                continue
            if MARKER.search(text):
                errors.append(f"{where}: table cells take words only; figures in tables come from the record")
                continue
            op = {"type": "set_cell", "target": box["id"], "payload": {"row": e["row"], "col": e["col"], "text": text}}
        else:
            if not box["editable"] or "table" in box:
                errors.append(f"{where}: this box cannot be edited")
                continue
            op = keep_or_remove(session, box["id"], text)
        with SessionLocal() as s:
            c = Door(s, memo_id).check(op, actor_type="agent")
        if c.status != "applied":
            errors.append(f"{where}: {c.reason}")
            continue
        ops.append(op)
    return ops, errors


def keep_or_remove(session: Session, element_id: str, text: str) -> dict:
    """A box's new text from the model, as a door operation: a figure already in the box and kept as {{name}}
    stays the same chip (its text in place of the marker), a new {{name}} is asked for, one left out is removed."""
    el = session.get(Element, uuid.UUID(element_id))
    d = MemoData(session, _memo_of(session, el), element_ids=[el.id])
    chips = [x for _, ss in d.paragraphs(element_id=el.id) for x in ss if x.kind == "chip" and x.concept_name]
    waiting: dict = {}
    for c in chips:
        waiting.setdefault(c.concept_name, []).append(c)
    kept = set()

    def keep(m):
        queue = waiting.get(m.group(1))
        if queue:
            c = queue.pop(0)
            kept.add(c.id)
            return c.text
        return m.group(0)                                    # a new figure: asked for by name

    text = MARKER.sub(keep, text)
    payload = {"text": text}
    removed = [str(c.id) for c in chips if c.id not in kept]
    if removed:
        payload["remove_chips"] = removed
    return {"type": "set_text", "target": element_id, "base_revision": el.revision, "payload": payload}


def _memo_of(session: Session, el: Element):
    from app.db.memo import Slide
    return session.get(Slide, el.slide_id).memo_id


def ask(memo_id, user: S.User, request: str, slide_id: str | None, element_id: str | None) -> dict:
    """One request: the agent's reply and, when it proposes edits, the proposal (nothing is applied)."""
    started = time.time()
    with SessionLocal() as session:
        ctx = context(session, memo_id, slide_id, element_id)
        text = package(ctx, request, element_id)
        tries = []

        def validate(answer: dict) -> list[str]:
            edits = answer.get("edits") or []
            if not isinstance(edits, list):
                return ["edits must be a list"]
            ops, errors = to_operations(session, memo_id, ctx, edits)
            tries.append(ops)
            return errors

        result = call_with_checks(INSTRUCTIONS, text, [], validate)
        ops = tries[-1] if tries else []
        reply = str(result["answer"].get("reply") or "")
        if result["unresolved"]:
            reply += f" ({len(result['unresolved'])} edit(s) left out: {'; '.join(result['unresolved'][:3])})"
        thread = session.scalar(select(ChatThread).where(ChatThread.memo_id == ctx["memo"].id)) or ChatThread(memo_id=ctx["memo"].id, created_by=user.id)
        session.add(thread)
        session.flush()
        session.add(ChatMessage(thread_id=thread.id, author_type="person", author_id=user.id, text=request))
        answer = ChatMessage(id=uuid.uuid4(), thread_id=thread.id, author_type="agent", text=reply)
        session.add(answer)
        session.flush()
        proposal = None
        if ops:
            proposal = proposals.propose(session, memo_id, ops, reply, actor_type="agent", on_behalf_of=user.id, message_id=answer.id)
            answer.change_set_id = uuid.UUID(proposal["id"])
        att = result["attempts"]
        session.add(AgentRun(message_id=answer.id, model=result["model"], attempts=len(att),
                             input_tokens=sum(a["prompt_tokens"] or 0 for a in att), output_tokens=sum(a["completion_tokens"] or 0 for a in att),
                             seconds=round(time.time() - started, 1),
                             validation_errors="\n".join(e for a in att for e in a["errors"]) or None))
        session.commit()
        return {"reply": reply, "proposal": proposal, "attempts": len(att), "seconds": round(time.time() - started, 1),
                "tokens": [sum(a["prompt_tokens"] or 0 for a in att), sum(a["completion_tokens"] or 0 for a in att)]}


def history(session: Session, memo_id) -> list[dict]:
    thread = session.scalar(select(ChatThread).where(ChatThread.memo_id == uuid.UUID(str(memo_id))))
    if thread is None:
        return []
    return [{"author": m.author_type, "text": m.text, "proposal_id": str(m.change_set_id) if m.change_set_id else None,
             "at": m.created_at.isoformat()}
            for m in session.scalars(select(ChatMessage).where(ChatMessage.thread_id == thread.id).order_by(ChatMessage.created_at))]
