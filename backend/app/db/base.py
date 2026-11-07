"""Declarative base and the column groups many tables share (docs/SCHEMA_DESIGN.md §4).

Enum-like columns are plain text: the allowed values are listed next to each column and checked by the
services, so a new value (a new rule type, a new segment kind) is a code-level addition, not a migration.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column


class Base(DeclarativeBase):
    pass


def pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def fk(target: str, nullable: bool = False, ondelete: str = "CASCADE") -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), ForeignKey(target, ondelete=ondelete), nullable=nullable, index=True)


def created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class Frame:
    """Position on the page, in inches (like PowerPoint)."""
    x_in: Mapped[float] = mapped_column(Float, default=0)
    y_in: Mapped[float] = mapped_column(Float, default=0)
    w_in: Mapped[float] = mapped_column(Float, default=0)
    h_in: Mapped[float] = mapped_column(Float, default=0)
    rotation_deg: Mapped[float] = mapped_column(Float, default=0)
    z_index: Mapped[int] = mapped_column(Integer, default=0)


class TextStyle:
    """Text look. Every column is optional: unset means "take it from the role". Colours are palette names."""
    font: Mapped[str | None] = mapped_column(String)
    size_pt: Mapped[float | None] = mapped_column(Float)
    bold: Mapped[bool | None] = mapped_column(Boolean)
    italic: Mapped[bool | None] = mapped_column(Boolean)
    caps: Mapped[bool | None] = mapped_column(Boolean)
    color_ref: Mapped[str | None] = mapped_column(String)
    align: Mapped[str | None] = mapped_column(String)          # left, center, right, justify
    anchor: Mapped[str | None] = mapped_column(String)         # top, middle, bottom
    line_spacing: Mapped[float | None] = mapped_column(Float)
    space_before_pt: Mapped[float | None] = mapped_column(Float)
    space_after_pt: Mapped[float | None] = mapped_column(Float)
    bullet_char: Mapped[str | None] = mapped_column(String)
    bullet_color_ref: Mapped[str | None] = mapped_column(String)
    indent_in: Mapped[float | None] = mapped_column(Float)


class Provenance:
    """Where a spec value came from and who confirmed it."""
    source: Mapped[str | None] = mapped_column(String)         # guide, template, memo, person, assumed, generated
    evidence_quote: Mapped[str | None] = mapped_column(Text)
    evidence_ref: Mapped[str | None] = mapped_column(String)   # e.g. "guide p.3", "template layout 'Cover'"
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InSpecVersion(Provenance):
    """A row of a firm spec: belongs to one spec version and carries its provenance."""

    @declared_attr
    def spec_version_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(UUID(as_uuid=True), ForeignKey("spec_versions.id", ondelete="CASCADE"), index=True)
