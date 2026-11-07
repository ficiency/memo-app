"""The element model and the memo (docs/SCHEMA_DESIGN.md §7–8).

`elements` is shared: a row belongs either to a spec layout (layout_id) or to a memo slide (slide_id).
Both painters (canvas HTML, PPTX) and the importer speak this one model.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Frame, TextStyle, created_at, fk, pk


class Asset(Base):
    """A file in Supabase Storage: image, logo, font, upload, export."""
    __tablename__ = "assets"
    id: Mapped[uuid.UUID] = pk()
    firm_id: Mapped[uuid.UUID | None] = fk("firms.id", nullable=True)
    kind: Mapped[str] = mapped_column(String)                   # image, logo, font, upload, export
    storage_path: Mapped[str] = mapped_column(String)
    content_type: Mapped[str | None] = mapped_column(String)
    hash: Mapped[str | None] = mapped_column(String, index=True)
    created_at: Mapped[datetime] = created_at()


class Memo(Base):
    __tablename__ = "memos"
    id: Mapped[uuid.UUID] = pk()
    firm_id: Mapped[uuid.UUID] = fk("firms.id")
    deal_id: Mapped[uuid.UUID] = fk("deals.id")
    spec_version_id: Mapped[uuid.UUID] = fk("spec_versions.id", ondelete="RESTRICT")
    record_revision_id: Mapped[uuid.UUID] = fk("record_revisions.id", ondelete="RESTRICT")
    stage_id: Mapped[uuid.UUID | None] = fk("stages.id", nullable=True, ondelete="SET NULL")
    title: Mapped[str] = mapped_column(String)
    memo_date: Mapped[str | None] = mapped_column(String)       # ISO date of the memo or meeting (memo data)
    deal_team: Mapped[str | None] = mapped_column(Text)         # one member per line (memo data)
    sequence: Mapped[int] = mapped_column(Integer, default=0)   # last operation number
    status: Mapped[str] = mapped_column(String, default="editing")  # editing, reviewed
    created_by: Mapped[uuid.UUID | None] = fk("users.id", nullable=True, ondelete="SET NULL")
    created_at: Mapped[datetime] = created_at()


class Slide(Base):
    __tablename__ = "slides"
    id: Mapped[uuid.UUID] = pk()
    memo_id: Mapped[uuid.UUID] = fk("memos.id")
    position: Mapped[int] = mapped_column(Integer)
    section_id: Mapped[uuid.UUID | None] = fk("sections.id", nullable=True, ondelete="SET NULL")
    layout_id: Mapped[uuid.UUID | None] = fk("layouts.id", nullable=True, ondelete="SET NULL")
    kind: Mapped[str] = mapped_column(String, default="template")   # template, free, imported
    background_ref: Mapped[str | None] = mapped_column(String)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Element(Frame, Base):
    __tablename__ = "elements"
    __table_args__ = (
        CheckConstraint("(slide_id IS NULL) <> (layout_id IS NULL)", name="element_in_slide_or_layout"),
    )
    id: Mapped[uuid.UUID] = pk()
    slide_id: Mapped[uuid.UUID | None] = fk("slides.id", nullable=True)
    layout_id: Mapped[uuid.UUID | None] = fk("layouts.id", nullable=True)
    origin_element_id: Mapped[uuid.UUID | None] = fk("elements.id", nullable=True, ondelete="SET NULL")
    parent_id: Mapped[uuid.UUID | None] = fk("elements.id", nullable=True)
    name: Mapped[str | None] = mapped_column(String)            # the source shape's name, for matching
    placeholder_idx: Mapped[int | None] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String)                   # text, table, chart, image, shape, line, group
    shape_kind: Mapped[str | None] = mapped_column(String)      # rect, rounded_rect, ellipse… for shapes
    role: Mapped[str | None] = mapped_column(String)
    fill_kind: Mapped[str] = mapped_column(String, default="person")  # record, model, person, fixed
    firm_lock: Mapped[str] = mapped_column(String, default="free")    # fixed, content, free
    user_lock_by: Mapped[uuid.UUID | None] = fk("users.id", nullable=True, ondelete="SET NULL")
    user_lock_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    description: Mapped[str | None] = mapped_column(Text)       # what the box is for (empty guide-only boxes)
    fill_ref: Mapped[str | None] = mapped_column(String)
    line_ref: Mapped[str | None] = mapped_column(String)
    line_width_pt: Mapped[float | None] = mapped_column(Float)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_by: Mapped[uuid.UUID | None] = fk("users.id", nullable=True, ondelete="SET NULL")
    updated_at: Mapped[datetime] = created_at()
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ElementParagraph(TextStyle, Base):
    """G1: each paragraph keeps its own style overrides and level."""
    __tablename__ = "element_paragraphs"
    id: Mapped[uuid.UUID] = pk()
    element_id: Mapped[uuid.UUID | None] = fk("elements.id", nullable=True)
    table_cell_id: Mapped[uuid.UUID | None] = fk("table_cells.id", nullable=True)
    position: Mapped[int] = mapped_column(Integer)
    level: Mapped[int] = mapped_column(Integer, default=0)
    role: Mapped[str | None] = mapped_column(String)


class Segment(Base):
    """A run of text. A chip is a segment bound to a record path; its text is never typed."""
    __tablename__ = "segments"
    id: Mapped[uuid.UUID] = pk()
    paragraph_id: Mapped[uuid.UUID] = fk("element_paragraphs.id")
    position: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String, default="text")   # text, chip, model_draft, person_slot, conditional
    text: Mapped[str] = mapped_column(Text, default="")
    concept_name: Mapped[str | None] = mapped_column(String)
    record_path: Mapped[str | None] = mapped_column(String, index=True)
    value_snapshot: Mapped[str | None] = mapped_column(Text)
    format_item: Mapped[str | None] = mapped_column(String)


class ElementTable(Base):
    __tablename__ = "element_tables"
    id: Mapped[uuid.UUID] = pk()
    element_id: Mapped[uuid.UUID] = fk("elements.id")
    table_template_id: Mapped[uuid.UUID | None] = fk("table_templates.id", nullable=True, ondelete="SET NULL")
    table_style_id: Mapped[uuid.UUID | None] = fk("table_styles.id", nullable=True, ondelete="SET NULL")


class TableColumn(Base):
    __tablename__ = "table_columns"
    id: Mapped[uuid.UUID] = pk()
    element_table_id: Mapped[uuid.UUID] = fk("element_tables.id")
    position: Mapped[int] = mapped_column(Integer)
    width_in: Mapped[float | None] = mapped_column(Float)


class TableRow(Base):
    __tablename__ = "table_rows"
    id: Mapped[uuid.UUID] = pk()
    element_table_id: Mapped[uuid.UUID] = fk("element_tables.id")
    position: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String, default="data")   # header, group, data, total
    height_in: Mapped[float | None] = mapped_column(Float)


class TableCell(Base):
    __tablename__ = "table_cells"
    id: Mapped[uuid.UUID] = pk()
    element_table_id: Mapped[uuid.UUID] = fk("element_tables.id")
    row: Mapped[int] = mapped_column(Integer)
    col: Mapped[int] = mapped_column(Integer)
    row_span: Mapped[int] = mapped_column(Integer, default=1)
    col_span: Mapped[int] = mapped_column(Integer, default=1)
    kind: Mapped[str] = mapped_column(String, default="data")   # fixed, data
    fill_ref: Mapped[str | None] = mapped_column(String)


class ElementChart(Base):
    __tablename__ = "element_charts"
    id: Mapped[uuid.UUID] = pk()
    element_id: Mapped[uuid.UUID] = fk("elements.id")
    chart_template_id: Mapped[uuid.UUID | None] = fk("chart_templates.id", nullable=True, ondelete="SET NULL")
    chart_type: Mapped[str] = mapped_column(String)


class ChartSeries(Base):
    __tablename__ = "chart_series"
    id: Mapped[uuid.UUID] = pk()
    element_chart_id: Mapped[uuid.UUID] = fk("element_charts.id")
    position: Mapped[int] = mapped_column(Integer)
    name: Mapped[str | None] = mapped_column(String)
    concept_name: Mapped[str | None] = mapped_column(String)
    record_path: Mapped[str | None] = mapped_column(String)
    color_ref: Mapped[str | None] = mapped_column(String)


class ChartPoint(Base):
    """A value snapshot of a series (one per category), so charts stay data, not pictures."""
    __tablename__ = "chart_points"
    id: Mapped[uuid.UUID] = pk()
    chart_series_id: Mapped[uuid.UUID] = fk("chart_series.id")
    position: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String)
    value: Mapped[float | None] = mapped_column(Float)
    is_forecast: Mapped[bool] = mapped_column(Boolean, default=False)


class ElementImage(Base):
    __tablename__ = "element_images"
    id: Mapped[uuid.UUID] = pk()
    element_id: Mapped[uuid.UUID] = fk("elements.id")
    asset_id: Mapped[uuid.UUID | None] = fk("assets.id", nullable=True, ondelete="SET NULL")
    alt: Mapped[str | None] = mapped_column(Text)
    flagged: Mapped[bool] = mapped_column(Boolean, default=False)   # a picture standing in for native content
