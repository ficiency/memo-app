"""Deal records (docs/SCHEMA_DESIGN.md §6): per firm and deal, one immutable row set per revision."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, created_at, fk, pk


class Deal(Base):
    __tablename__ = "deals"
    id: Mapped[uuid.UUID] = pk()
    name: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = created_at()


class RecordRevision(Base):
    """Revision 1 is the original upload; every save or upload creates the next one."""
    __tablename__ = "record_revisions"
    id: Mapped[uuid.UUID] = pk()
    deal_id: Mapped[uuid.UUID] = fk("deals.id")
    firm_id: Mapped[uuid.UUID] = fk("firms.id")
    number: Mapped[int] = mapped_column(Integer)
    record_schema: Mapped[str] = mapped_column(String)          # which shape of record (binding key)
    as_of: Mapped[date | None] = mapped_column(Date)
    asset_id: Mapped[uuid.UUID | None] = fk("assets.id", nullable=True, ondelete="SET NULL")
    previous_id: Mapped[uuid.UUID | None] = fk("record_revisions.id", nullable=True, ondelete="SET NULL")
    created_by: Mapped[uuid.UUID | None] = fk("users.id", nullable=True, ondelete="SET NULL")
    created_at: Mapped[datetime] = created_at()


class RecordValue(Base):
    """One datum of a revision, addressed by its path (app/readers/deal_record.py lists the forms)."""
    __tablename__ = "record_values"
    id: Mapped[uuid.UUID] = pk()
    revision_id: Mapped[uuid.UUID] = fk("record_revisions.id")
    path: Mapped[str] = mapped_column(String, index=True)
    family: Mapped[str | None] = mapped_column(String, index=True)   # the path without its period
    label: Mapped[str | None] = mapped_column(Text)
    value_kind: Mapped[str | None] = mapped_column(String)          # money, percent, count, date, text… as the record says
    is_forecast: Mapped[bool] = mapped_column(Boolean, default=False)
    kind: Mapped[str] = mapped_column(String)                   # number, text, date
    number_value: Mapped[float | None] = mapped_column(Float)
    text_value: Mapped[str | None] = mapped_column(Text)
    date_value: Mapped[date | None] = mapped_column(Date)
    unit: Mapped[str | None] = mapped_column(String)
    period: Mapped[str | None] = mapped_column(String)
    source: Mapped[str | None] = mapped_column(Text)
