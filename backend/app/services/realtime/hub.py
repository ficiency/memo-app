"""Real time: one room per memo, one person per box, live text, and every committed edit shown to everyone.

A room holds the people who have the memo open. A person takes a box before editing it; nobody else can
take it until they leave it or disconnect (no timeout). While they type, the others see the text as it goes
(a draft: shown, never stored). When they commit, the edit goes through the operations door like any other
edit, and the result (applied, refused or kept aside, with the element as it is now and the memo's sequence)
is sent to everyone in the room, so each screen updates that box.

Rooms live in this process's memory: run one server process. A small interface (join, leave, take, release,
draft, commit) keeps the rest of the app unaware of how messages travel, so several processes can later share
rooms through Redis or Postgres LISTEN/NOTIFY without touching anything else.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from fastapi.concurrency import run_in_threadpool

from app.db.session import SessionLocal
from app.services.operations.door import Door


@dataclass
class Person:
    user_id: uuid.UUID
    email: str
    editing: str | None = None                     # the element id this person holds


@dataclass
class Room:
    people: dict = field(default_factory=dict)     # socket → Person


class Hub:
    def __init__(self) -> None:
        self.rooms: dict[str, Room] = {}

    # --- presence ------------------------------------------------------------------------------
    def people(self, memo_id: str) -> list[dict]:
        room = self.rooms.get(memo_id)
        return [{"email": p.email, "editing": p.editing} for p in room.people.values()] if room else []

    def holder(self, memo_id: str, element_id: str | None) -> Person | None:
        room = self.rooms.get(memo_id)
        if not room or not element_id:
            return None
        return next((p for p in room.people.values() if p.editing == element_id), None)

    async def join(self, memo_id: str, socket, user_id, email: str) -> None:
        self.rooms.setdefault(memo_id, Room()).people[socket] = Person(user_id, email)
        await self.send(memo_id, {"type": "people", "people": self.people(memo_id)})

    async def leave(self, memo_id: str, socket) -> None:
        room = self.rooms.get(memo_id)
        if not room:
            return
        room.people.pop(socket, None)                  # whatever box this person held is free now
        if not room.people:
            self.rooms.pop(memo_id, None)
            return
        await self.send(memo_id, {"type": "people", "people": self.people(memo_id)})

    # --- one person per box --------------------------------------------------------------------
    async def take(self, memo_id: str, socket, element_id: str) -> None:
        """Take a box. Messages are handled one after the other, so two people asking at the same moment
        cannot both get it: the first to arrive does."""
        room = self.rooms.get(memo_id)
        me = room.people.get(socket) if room else None
        if me is None:
            return
        other = self.holder(memo_id, element_id)
        if other is not None and other is not me:
            await say(socket, {"type": "box_taken", "element_id": element_id, "by": other.email})
            return
        me.editing = element_id
        await self.send(memo_id, {"type": "people", "people": self.people(memo_id)})

    async def release(self, memo_id: str, socket, element_id: str | None) -> None:
        room = self.rooms.get(memo_id)
        me = room.people.get(socket) if room else None
        if me is None or (element_id and me.editing != element_id):
            return
        me.editing = None
        await self.send(memo_id, {"type": "people", "people": self.people(memo_id)})

    async def draft(self, memo_id: str, socket, element_id: str, text: str) -> None:
        """Text being typed: shown to the others, not stored, not checked. Only the box's holder drafts."""
        room = self.rooms.get(memo_id)
        me = room.people.get(socket) if room else None
        if me is None or me.editing != element_id or not isinstance(text, str) or len(text) > 20000:
            return
        await self.send(memo_id, {"type": "draft", "by": me.email, "element_id": element_id, "text": text}, skip=socket)

    # --- commit through the door ----------------------------------------------------------------
    async def commit(self, memo_id: str, socket, op: dict) -> None:
        """An edit, checked by the door. An element held by someone else is refused there, so a client that
        skips taking the box cannot write into someone else's."""
        room = self.rooms.get(memo_id)
        me = room.people.get(socket) if room else None
        if me is None:
            return
        other = self.holder(memo_id, str(op.get("target") or ""))
        held_by = other.email if other is not None and other is not me else None

        def apply():
            with SessionLocal() as session:
                return Door(session, uuid.UUID(memo_id)).apply(op, actor_type="person", actor_id=me.user_id, held_by=held_by)

        result = await run_in_threadpool(apply)
        message = {"type": "operation", "by": me.email, "op": op.get("type"), "target": str(op.get("target") or ""),
                   "client_op_id": op.get("client_op_id"), "status": result.status, "reason": result.reason,
                   "sequence": result.sequence, "element": result.element, "slides": result.slides,
                   "created": [str(x) for x in result.created or []]}
        await self.send(memo_id, message)

    # --- messages ------------------------------------------------------------------------------
    async def send(self, memo_id: str, message: dict, skip=None) -> None:
        room = self.rooms.get(memo_id)
        for socket in list(room.people) if room else []:
            if socket is skip:
                continue
            if not await say(socket, message):
                room.people.pop(socket, None)


async def say(socket, message: dict) -> bool:
    try:
        await socket.send_json(message)
        return True
    except Exception:
        return False


hub = Hub()
