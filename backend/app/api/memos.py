from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import spec as S
from ..db.memo import Memo
from ..db.session import get_session
from ..services.operations.door import Door

router = APIRouter(prefix="/memos", tags=["memos"])


class OperationIn(BaseModel):
    type: str
    target: str | None = None
    base_revision: int | None = None
    payload: dict = {}
    actor_email: str                     # mock auth: the user is picked by email


@router.post("/{memo_id}/operations")
def apply_operation(memo_id: str, op: OperationIn, session: Session = Depends(get_session)) -> dict:
    """Every edit goes through the door: applied, refused or kept aside, always logged."""
    user = session.scalar(select(S.User).where(S.User.email == op.actor_email))
    memo = session.get(Memo, memo_id)
    if user is None or memo is None or memo.firm_id != user.firm_id:
        raise HTTPException(404, "no such memo for this user")      # firms are isolated accounts
    result = Door(session, memo.id).apply(op.model_dump(exclude={"actor_email"}), actor_type="person", actor_id=user.id)
    return {"status": result.status, "reason": result.reason, "sequence": result.sequence,
            "created": [str(x) for x in result.created or []]}
