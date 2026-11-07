from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import select

from ..db import spec as S
from ..db.memo import Memo
from ..db.session import SessionLocal
from ..services.operations.door import Door
from ..services.realtime.hub import hub

router = APIRouter(prefix="/memos", tags=["memos"])


class OperationIn(BaseModel):
    type: str
    target: str | None = None
    base_revision: int | None = None
    payload: dict = {}
    actor_email: str                     # mock auth: the user is picked by email


@router.post("/{memo_id}/operations")
async def apply_operation(memo_id: str, op: OperationIn) -> dict:
    """Every edit goes through the door: applied, refused or kept aside, always logged; whoever has the memo
    open sees the result."""
    body = op.model_dump(exclude={"actor_email"})
    other = hub.holder(memo_id, body.get("target"))

    def apply():
        with SessionLocal() as session:
            user = session.scalar(select(S.User).where(S.User.email == op.actor_email))
            memo = session.get(Memo, memo_id)
            if user is None or memo is None or memo.firm_id != user.firm_id:
                return None, None                             # firms are isolated accounts
            held_by = other.email if other is not None and other.email != user.email else None
            return user.email, Door(session, memo.id).apply(body, actor_type="person", actor_id=user.id, held_by=held_by)

    email, result = await run_in_threadpool(apply)
    if result is None:
        raise HTTPException(404, "no such memo for this user")
    message = {"type": "operation", "by": email, "op": body["type"], "target": body.get("target"),
               "status": result.status, "reason": result.reason, "sequence": result.sequence,
               "element": result.element, "slides": result.slides, "created": [str(x) for x in result.created or []]}
    await hub.send(memo_id, message)
    return message


@router.websocket("/{memo_id}/live")
async def live(socket: WebSocket, memo_id: str, email: str):
    """The memo's room: presence, one person per box, live drafts, and edits through the door."""
    def allowed():
        with SessionLocal() as session:
            user = session.scalar(select(S.User).where(S.User.email == email))
            memo = session.get(Memo, memo_id)
            return user if user is not None and memo is not None and memo.firm_id == user.firm_id else None

    user = await run_in_threadpool(allowed)
    await socket.accept()
    if user is None:
        await socket.close(code=4403)                            # firms are isolated accounts
        return
    await hub.join(memo_id, socket, user.id, user.email)
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
                await hub.commit(memo_id, socket, message.get("op") or {})
    except WebSocketDisconnect:
        pass
    finally:
        await hub.leave(memo_id, socket)
