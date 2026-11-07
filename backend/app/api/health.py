from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db.session import get_session

router = APIRouter()


@router.get("/health")
def health(session: Session = Depends(get_session)) -> dict:
    """The API is up and the database answers."""
    tables = session.execute(text("select count(*) from pg_tables where schemaname = 'public'")).scalar_one()
    return {"ok": True, "tables": tables}
