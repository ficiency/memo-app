"""Memos: list, create from a spec and a record, read for the canvas, edit through the door, the memo's room
in real time, and the PowerPoint round trip (download, upload an edited copy, what was kept aside)."""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import REPO
from ..db import spec as S
from ..db.history import Conflict, Operation
from ..db.memo import Memo
from ..db.record import Deal, RecordRevision
from ..db.session import SessionLocal, get_session
from ..services import binding, proposals, roundtrip, updates
from ..services.agent import slide as agent
from ..services.generator.memo import Generator
from ..services.memo_view import memo_view
from ..services.operations.door import Door
from ..services.realtime.hub import hub
from .auth import current_user

router = APIRouter(prefix="/memos", tags=["memos"])
FILES = REPO / "backend" / ".work"


def own_memo(session: Session, user: S.User, memo_id: str) -> Memo:
    try:
        memo = session.get(Memo, uuid.UUID(memo_id))
    except ValueError:
        memo = None
    if memo is None or memo.firm_id != user.firm_id:            # firms are isolated accounts
        raise HTTPException(404, "no such memo for this firm")
    return memo


@router.get("")
def list_memos(user: S.User = Depends(current_user), session: Session = Depends(get_session)) -> list[dict]:
    out = []
    for m in session.scalars(select(Memo).where(Memo.firm_id == user.firm_id).order_by(Memo.created_at.desc())):
        version = session.get(S.SpecVersion, m.spec_version_id)
        revision = session.get(RecordRevision, m.record_revision_id)
        creator = session.get(S.User, m.created_by) if m.created_by else None
        out.append({"id": str(m.id), "title": m.title, "status": m.status, "deal": session.get(Deal, m.deal_id).name,
                    "stage": session.get(S.Stage, m.stage_id).name if m.stage_id else None,
                    "spec": session.get(S.Spec, version.spec_id).name, "spec_version": version.number,
                    "record_revision": revision.number, "memo_date": m.memo_date,
                    "created_at": m.created_at.isoformat(), "created_by": creator.email if creator else None})
    return out


class NewMemo(BaseModel):
    spec_version_id: str
    record_revision_id: str
    stage: str
    memo_date: str | None = None
    deal_team: str | None = None


@router.post("")
async def create_memo(body: NewMemo, user: S.User = Depends(current_user)) -> dict:
    """A memo from a spec version, a record revision and a stage. The firm's binding is made the first time
    (one model call), if it has none for this kind of record."""
    def create():
        with SessionLocal() as session:
            firm = session.get(S.Firm, user.firm_id)
            revision = session.get(RecordRevision, uuid.UUID(body.record_revision_id))
            if revision is None or revision.firm_id != firm.id:
                raise HTTPException(404, "no such record for this firm")
            bound = session.scalar(select(S.Binding.id).where(S.Binding.firm_id == firm.id,
                                                               S.Binding.record_schema == revision.record_schema))
            firm_name = firm.name
        if bound is None:
            binding.bind(firm_name)
        with SessionLocal() as session:
            try:
                return Generator(session, firm_name, body.stage, body.memo_date, body.deal_team,
                                 spec_version_id=uuid.UUID(body.spec_version_id), revision_id=uuid.UUID(body.record_revision_id),
                                 created_by=user.id).run()
            except ValueError as e:
                raise HTTPException(400, str(e))

    return await run_in_threadpool(create)


@router.get("/{memo_id}")
def read_memo(memo_id: str, user: S.User = Depends(current_user), session: Session = Depends(get_session)) -> dict:
    """The memo as the canvas paints it, with the spec's styles, and who is in it right now."""
    memo = own_memo(session, user, memo_id)
    return {**memo_view(session, memo.id), "people": hub.people(str(memo.id))}


# --- edits ----------------------------------------------------------------------------------------
class OperationIn(BaseModel):
    type: str
    target: str | None = None
    base_revision: int | None = None
    payload: dict = {}


@router.get("/{memo_id}/pendings")
def list_pendings(memo_id: str, user: S.User = Depends(current_user), session: Session = Depends(get_session)) -> list[dict]:
    """What the memo still needs from a person, computed now."""
    from ..services import pendings
    own_memo(session, user, memo_id)
    return pendings.pendings(session, memo_id)


class Dismiss(BaseModel):
    kind: str
    target_id: str


@router.post("/{memo_id}/pendings/dismiss")
async def dismiss_pending(memo_id: str, body: Dismiss, user: S.User = Depends(current_user)) -> dict:
    """"It's fine" on a pending a person may accept; everyone in the memo sees it go."""
    from ..services import pendings

    def run():
        with SessionLocal() as session:
            own_memo(session, user, memo_id)
            try:
                pendings.dismiss(session, memo_id, body.kind, body.target_id, user.id)
            except ValueError as e:
                raise HTTPException(400, str(e))

    await run_in_threadpool(run)
    await hub.send(memo_id, {"type": "pendings", "by": user.email})
    return {"dismissed": True}


@router.post("/{memo_id}/pictures")
async def upload_picture(memo_id: str, file: UploadFile, user: S.User = Depends(current_user)) -> dict:
    """A picture for this memo (stored once by content); it goes on a slide with an add_element operation."""
    data = await file.read()

    def store():
        from ..services import storage
        with SessionLocal() as session:
            memo = session.get(Memo, uuid.UUID(memo_id))
            if memo is None or memo.firm_id != user.firm_id:
                raise HTTPException(404, "no such memo")
            try:
                picture, content_type = storage.as_picture(data, file.content_type)
                asset = storage.put(session, picture, "image", memo.firm_id, content_type, file.filename)
            except ValueError as e:
                raise HTTPException(400, str(e))
            session.commit()
            return {"asset_id": str(asset.id)}

    return await run_in_threadpool(store)


@router.post("/{memo_id}/operations")
async def apply_operation(memo_id: str, op: OperationIn, user: S.User = Depends(current_user)) -> dict:
    """Every edit goes through the door: applied, refused or kept aside, always logged; whoever has the memo
    open sees the result."""
    body = op.model_dump()
    other = hub.holder(memo_id, body.get("target"))

    def apply():
        with SessionLocal() as session:
            memo = own_memo(session, user, memo_id)
            held_by = other.email if other is not None and other.email != user.email else None
            return Door(session, memo.id).apply(body, actor_type="person", actor_id=user.id, held_by=held_by)

    result = await run_in_threadpool(apply)
    message = {"type": "operation", "by": user.email, "op": body["type"], "target": body.get("target"),
               "status": result.status, "reason": result.reason, "sequence": result.sequence,
               "element": result.element, "slides": result.slides, "created": [str(x) for x in result.created or []],
                   "warnings": result.warnings or []}
    await hub.send(memo_id, message)
    return message


@router.websocket("/{memo_id}/live")
async def live(socket: WebSocket, memo_id: str, email: str):
    """The memo's room: presence, one person per box, live drafts, and edits through the door."""
    def allowed():
        with SessionLocal() as session:
            user = session.scalar(select(S.User).where(S.User.email == email))
            memo = session.get(Memo, uuid.UUID(memo_id))
            return user if user is not None and memo is not None and memo.firm_id == user.firm_id else None

    user = await run_in_threadpool(allowed)
    await socket.accept()
    if user is None:
        await socket.close(code=4403)                            # firms are isolated accounts
        return
    await hub.join(memo_id, socket, user.id, user.email)
    # Saving runs beside the socket: taking, leaving and drafting are answered at once, while one's own edits
    # are saved one after the other, in the order they were made.
    saving = asyncio.Lock()
    pending: set = set()

    async def save(op: dict, person, held_by: str | None) -> None:
        async with saving:
            await hub.commit(memo_id, person, op, held_by)

    try:
        while True:
            message = await socket.receive_json()
            kind = message.get("type")
            if kind == "take":
                await hub.take(memo_id, socket, str(message.get("element_id")))
            elif kind == "release":
                await hub.release(memo_id, socket, message.get("element_id"))
            elif kind == "draft":
                await hub.draft(memo_id, socket, str(message.get("element_id")), message.get("text"))
            elif kind == "op":
                op, person = message.get("op") or {}, hub.person(memo_id, socket)
                if person is None:
                    continue
                task = asyncio.create_task(save(op, person, hub.held_by_other(memo_id, socket, str(op.get("target") or ""))))
                pending.add(task)
                task.add_done_callback(pending.discard)
    except WebSocketDisconnect:
        pass
    finally:
        await hub.leave(memo_id, socket)
        if pending:                                              # edits sent before leaving are still saved
            await asyncio.gather(*pending, return_exceptions=True)


# --- PowerPoint round trip ------------------------------------------------------------------------
@router.get("/{memo_id}/export")
async def export(memo_id: str, user: S.User = Depends(current_user)):
    """The memo as a PowerPoint file, painted from the database (never from the screen)."""
    with SessionLocal() as session:
        memo = own_memo(session, user, memo_id)
        name = "".join(c if c.isalnum() or c in " -_" else "_" for c in memo.title).strip() or "memo"
    out = FILES / "exports" / memo_id / f"{datetime.now():%Y%m%d_%H%M%S}.pptx"
    await run_in_threadpool(roundtrip.export, memo_id, out)
    return FileResponse(out, filename=f"{name}.pptx",
                        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")


@router.get("/{memo_id}/export.pdf")
async def export_pdf(memo_id: str, user: S.User = Depends(current_user)):
    """The memo as a PDF for committee: the painted PowerPoint, converted (it reads as the PowerPoint does)."""
    from ..services import pdf
    with SessionLocal() as session:
        memo = own_memo(session, user, memo_id)
        name = "".join(c if c.isalnum() or c in " -_" else "_" for c in memo.title).strip() or "memo"
    out = FILES / "exports" / memo_id / f"{datetime.now():%Y%m%d_%H%M%S}.pdf"
    try:
        await run_in_threadpool(pdf.export_pdf, memo_id, out)
    except ValueError as e:
        raise HTTPException(503, str(e))
    return FileResponse(out, filename=f"{name}.pdf", media_type="application/pdf")


@router.post("/{memo_id}/upload")
async def upload(memo_id: str, file: UploadFile, user: S.User = Depends(current_user)) -> dict:
    """One way in for a PowerPoint or a PDF; the file says what it is:
    - this memo's own export, edited: brought back (each change through the door, what cannot be applied kept
      aside, never lost);
    - another memo's export: refused, it is brought back in that memo;
    - anything else: its slides (or pages) come in as slides the firm leaves free.
    Everyone in the memo gets the memo again."""
    from ..db.memo import Slide
    from ..services import slide_upload
    name = file.filename or ""
    suffix = Path(name).suffix.lower()
    if suffix not in (".pptx", ".pdf"):
        raise HTTPException(400, "upload a PowerPoint (.pptx) or a PDF")
    path = FILES / "uploads" / f"{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = await file.read()
    path.write_bytes(data)

    def run() -> dict:
        with SessionLocal() as session:
            memo = own_memo(session, user, memo_id)
            ids = roundtrip.slide_ids(path) if suffix == ".pptx" else []
            if ids:
                owners = set(session.scalars(select(Slide.memo_id).where(Slide.id.in_([uuid.UUID(x) for x in ids]))))
                if memo.id not in owners:
                    other = session.get(Memo, next(iter(owners))) if owners else None
                    raise HTTPException(400, "this is the PowerPoint of another memo" + (f" ({other.title})" if other else "")
                                        + ": bring it back in that memo")
        if ids:
            try:
                return {"kind": "bring_back", **roundtrip.import_pptx(memo_id, path, None, user.email)}
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(400, f"could not read this file against the memo's last export: {e}")
        try:
            return {"kind": "slides", **slide_upload.upload(memo_id, data, name, file.content_type, user)}
        except ValueError as e:
            raise HTTPException(400, str(e))

    result = await run_in_threadpool(run)
    reason = "PowerPoint import" if result["kind"] == "bring_back" else "slide upload"
    await hub.send(memo_id, {"type": "reload", "by": user.email, "reason": reason})
    return result


@router.get("/{memo_id}/conflicts")
def conflicts(memo_id: str, user: S.User = Depends(current_user), session: Session = Depends(get_session)) -> list[dict]:
    """What was kept aside (an edit on an older version, a box added or deleted in PowerPoint…), never lost."""
    memo = own_memo(session, user, memo_id)
    out = []
    for c in session.scalars(select(Conflict).where(Conflict.memo_id == memo.id).order_by(Conflict.created_at.desc())):
        op = session.get(Operation, c.operation_id) if c.operation_id else None
        actor = session.get(S.User, op.actor_id or op.on_behalf_of) if op and (op.actor_id or op.on_behalf_of) else None
        out.append({"id": str(c.id), "kind": c.kind, "detail": c.detail, "status": c.status,
                    "element_id": str(c.element_id) if c.element_id else None, "created_at": c.created_at.isoformat(),
                    "operation": op and {"type": op.op_type, "actor": op.actor_type, "by": actor.email if actor else None,
                                         "payload": op.payload}})
    return out


# --- a newer deal record ------------------------------------------------------------------------------
@router.get("/{memo_id}/updates")
def update_available(memo_id: str, user: S.User = Depends(current_user), session: Session = Depends(get_session)) -> dict:
    """The memo's record revision, and a newer one for the deal if there is one the memo has not ignored."""
    return updates.available(session, own_memo(session, user, memo_id))


@router.get("/{memo_id}/updates/{revision_id}")
async def update_changes(memo_id: str, revision_id: str, user: S.User = Depends(current_user)) -> dict:
    """What that revision would change in the memo (before → after) and where a person is needed."""
    def run():
        with SessionLocal() as session:
            own_memo(session, user, memo_id)
            try:
                return updates.changes(session, memo_id, revision_id)
            except ValueError as e:
                raise HTTPException(400, str(e))

    return await run_in_threadpool(run)


async def _update_op(memo_id: str, revision_id: str, kind: str, user: S.User) -> dict:
    def run():
        with SessionLocal() as session:
            memo = own_memo(session, user, memo_id)
            door = Door(session, memo.id)
            result = door.apply({"type": kind, "target": memo_id, "payload": {"revision_id": revision_id}},
                                actor_type="update", actor_id=user.id, on_behalf_of=user.id)
            return result, getattr(door, "update_count", 0)

    result, count = await run_in_threadpool(run)
    if result.status == "applied" and kind == "apply_update":
        await hub.send(memo_id, {"type": "reload", "by": user.email, "reason": "deal record update"})
    return {"status": result.status, "reason": result.reason, "sequence": result.sequence, "changed": count}


@router.post("/{memo_id}/updates/{revision_id}/apply")
async def apply_update(memo_id: str, revision_id: str, user: S.User = Depends(current_user)) -> dict:
    """The revision's figures written into the memo through the door; everyone in the memo gets it again."""
    return await _update_op(memo_id, revision_id, "apply_update", user)


@router.post("/{memo_id}/updates/{revision_id}/ignore")
async def ignore_update(memo_id: str, revision_id: str, user: S.User = Depends(current_user)) -> dict:
    """The memo stays on its revision; this one is not offered again."""
    return await _update_op(memo_id, revision_id, "ignore_update", user)


# --- a newer version of the firm's spec ------------------------------------------------------------------
def _version_json(v: S.SpecVersion) -> dict:
    return {"id": str(v.id), "number": v.number, "label": v.label, "approved_at": v.approved_at.isoformat() if v.approved_at else None}


@router.get("/{memo_id}/spec-update")
def spec_update(memo_id: str, user: S.User = Depends(current_user), session: Session = Depends(get_session)) -> dict:
    """The memo's version of its firm's spec, and a newer approved one if the memo has not ignored it."""
    from ..services import spec_move
    memo = own_memo(session, user, memo_id)
    newer = spec_move.newer(session, memo)
    return {"current": _version_json(session.get(S.SpecVersion, memo.spec_version_id)), "available": newer and _version_json(newer)}


@router.get("/{memo_id}/spec-update/{version_id}")
async def spec_update_changes(memo_id: str, version_id: str, user: S.User = Depends(current_user)) -> dict:
    """What moving to that version would do: the same move, undone."""
    from ..services import spec_move

    def run():
        with SessionLocal() as session:
            own_memo(session, user, memo_id)
            try:
                return spec_move.preview(session, memo_id, version_id)
            except ValueError as e:
                raise HTTPException(400, str(e))

    return await run_in_threadpool(run)


async def _spec_op(memo_id: str, version_id: str, kind: str, user: S.User) -> dict:
    def run():
        with SessionLocal() as session:
            memo = own_memo(session, user, memo_id)
            door = Door(session, memo.id)
            result = door.apply({"type": kind, "target": memo_id, "payload": {"version_id": version_id}},
                                actor_type="update", actor_id=user.id, on_behalf_of=user.id)
            return result, getattr(door, "spec_report", None)

    result, report = await run_in_threadpool(run)
    if result.status == "applied" and kind == "move_spec":
        await hub.send(memo_id, {"type": "reload", "by": user.email, "reason": "spec version"})
    return {"status": result.status, "reason": result.reason, "report": report}


@router.post("/{memo_id}/spec-update/{version_id}/apply")
async def apply_spec_update(memo_id: str, version_id: str, user: S.User = Depends(current_user)) -> dict:
    """The memo moved to the newer version; everyone in the memo gets it again."""
    return await _spec_op(memo_id, version_id, "move_spec", user)


@router.post("/{memo_id}/spec-update/{version_id}/ignore")
async def ignore_spec_update(memo_id: str, version_id: str, user: S.User = Depends(current_user)) -> dict:
    """The memo stays on its version; this one is not offered again."""
    return await _spec_op(memo_id, version_id, "ignore_spec", user)


# --- proposals: edits made for a person to confirm --------------------------------------------------
@router.get("/{memo_id}/proposals")
def list_proposals(memo_id: str, user: S.User = Depends(current_user), session: Session = Depends(get_session)) -> list[dict]:
    own_memo(session, user, memo_id)
    return proposals.open_for(session, memo_id)


class OperationCheck(BaseModel):
    type: str
    target: str | None = None
    base_revision: int | None = None
    payload: dict = {}


@router.post("/{memo_id}/operations/check")
async def check_operation(memo_id: str, op: OperationCheck, user: S.User = Depends(current_user)) -> dict:
    """What an edit would do, without doing it (the canvas asks before removing a figure)."""
    def run():
        with SessionLocal() as session:
            own_memo(session, user, memo_id)
            c = Door(session, uuid.UUID(memo_id)).check(op.model_dump(), actor_type="person", actor_id=user.id)
            return {"status": c.status, "reason": c.reason, "warnings": c.warnings}

    return await run_in_threadpool(run)


async def _broadcast(memo_id: str, by: str, items: list, results: list) -> None:
    for item, r in zip(items, results):
        op = item["op"]
        await hub.send(memo_id, {"type": "operation", "by": by, "op": op.get("type"), "target": str(op.get("target") or ""),
                                 "status": r.status, "reason": r.reason, "sequence": r.sequence, "element": r.element,
                                 "slides": r.slides, "created": [str(x) for x in r.created or []], "warnings": r.warnings or []})


class ApplyChoice(BaseModel):
    only: list[int] | None = None                 # the operations chosen (their positions); all when absent


@router.post("/{memo_id}/proposals/{proposal_id}/apply")
async def apply_proposal(memo_id: str, proposal_id: str, body: ApplyChoice | None = None,
                         user: S.User = Depends(current_user)) -> dict:
    """The chosen operations of a proposal through the door, on behalf of the person who confirms them."""
    def run():
        with SessionLocal() as session:
            own_memo(session, user, memo_id)
            try:
                return proposals.apply(session, proposal_id, user.id, body.only if body else None)
            except ValueError as e:
                raise HTTPException(409, str(e))

    out, applied, results = await run_in_threadpool(run)
    await _broadcast(memo_id, user.email, applied, results)
    await hub.send(memo_id, {"type": "proposal", "id": proposal_id, "status": out["status"]})
    return out


@router.post("/{memo_id}/proposals/{proposal_id}/discard")
async def discard_proposal(memo_id: str, proposal_id: str, user: S.User = Depends(current_user)) -> dict:
    def run():
        with SessionLocal() as session:
            own_memo(session, user, memo_id)
            try:
                return proposals.discard(session, proposal_id, user.id)
            except ValueError as e:
                raise HTTPException(409, str(e))

    out = await run_in_threadpool(run)
    await hub.send(memo_id, {"type": "proposal", "id": proposal_id, "status": out["status"]})
    return out


# --- the memo's agent ---------------------------------------------------------------------------------
class AgentRequest(BaseModel):
    message: str
    slide_id: str | None = None
    element_id: str | None = None


@router.post("/{memo_id}/agent")
async def ask_agent(memo_id: str, body: AgentRequest, user: S.User = Depends(current_user)) -> dict:
    """A request in words for one slide (or one box): the agent's reply and its proposal, never applied."""
    with SessionLocal() as session:
        own_memo(session, user, memo_id)
    if not body.message.strip():
        raise HTTPException(400, "say what to do")
    out = await run_in_threadpool(agent.ask, memo_id, user, body.message.strip(), body.slide_id, body.element_id)
    if out["proposal"]:
        await hub.send(memo_id, {"type": "proposal", "id": out["proposal"]["id"], "status": "proposed"})
    return out


@router.get("/{memo_id}/agent")
def agent_history(memo_id: str, user: S.User = Depends(current_user), session: Session = Depends(get_session)) -> list[dict]:
    own_memo(session, user, memo_id)
    return agent.history(session, memo_id)



@router.get("/{memo_id}/record-boxes")
def record_boxes(memo_id: str, user: S.User = Depends(current_user), session: Session = Depends(get_session)) -> dict:
    """What a new table or chart can show: the record's tables (rows, columns) and its figures with periods."""
    from ..services import boxes
    from ..services.figures import Figures
    memo = own_memo(session, user, memo_id)
    figures = Figures(session, session.get(S.Firm, memo.firm_id), session.get(S.SpecVersion, memo.spec_version_id),
                      session.get(RecordRevision, memo.record_revision_id))
    return {"tables": boxes.record_tables(figures), "families": boxes.period_families(figures)}
