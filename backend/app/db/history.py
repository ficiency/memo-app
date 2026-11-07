"""Append-only history around a memo (docs/SCHEMA_DESIGN.md §8): operations, change sets, conflicts,
updates, reviews, chat and agent runs, exports and imports."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, created_at, fk, pk


class ChangeSet(Base):
    """Edits that go together: one AI proposal, one import, one update."""
    __tablename__ = "change_sets"
    id: Mapped[uuid.UUID] = pk()
    memo_id: Mapped[uuid.UUID] = fk("memos.id")
    actor_type: Mapped[str] = mapped_column(String)             # person, agent, import, update
    actor_id: Mapped[uuid.UUID | None] = mapped_column()
    on_behalf_of: Mapped[uuid.UUID | None] = fk("users.id", nullable=True, ondelete="SET NULL")
    summary: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String, default="proposed")  # proposed, applied, rejected
    source_message_id: Mapped[uuid.UUID | None] = mapped_column()
    created_at: Mapped[datetime] = created_at()


class Operation(Base):
    """Every edit, from every editor. Only `payload` is JSON: its shape depends on op_type."""
    __tablename__ = "operations"
    __table_args__ = (UniqueConstraint("memo_id", "sequence", name="operation_sequence_per_memo"),)
    id: Mapped[uuid.UUID] = pk()
    memo_id: Mapped[uuid.UUID] = fk("memos.id")
    sequence: Mapped[int] = mapped_column(Integer)
    change_set_id: Mapped[uuid.UUID | None] = fk("change_sets.id", nullable=True, ondelete="SET NULL")
    actor_type: Mapped[str] = mapped_column(String)             # person, agent, import, update, generator
    actor_id: Mapped[uuid.UUID | None] = mapped_column()
    on_behalf_of: Mapped[uuid.UUID | None] = fk("users.id", nullable=True, ondelete="SET NULL")
    op_type: Mapped[str] = mapped_column(String)
    target_id: Mapped[uuid.UUID | None] = mapped_column()
    base_revision: Mapped[int | None] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String)                 # applied, refused, kept_aside
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()


class Conflict(Base):
    """Kept aside from a PPTX import; never lost."""
    __tablename__ = "conflicts"
    id: Mapped[uuid.UUID] = pk()
    memo_id: Mapped[uuid.UUID] = fk("memos.id")
    operation_id: Mapped[uuid.UUID | None] = fk("operations.id", nullable=True, ondelete="SET NULL")
    element_id: Mapped[uuid.UUID | None] = fk("elements.id", nullable=True, ondelete="SET NULL")
    kind: Mapped[str] = mapped_column(String)  # added_box, deleted_box, pasted_picture, changed_since_export, chart_data_changed
    detail: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String, default="open")  # open, accepted, discarded
    resolved_by: Mapped[uuid.UUID | None] = fk("users.id", nullable=True, ondelete="SET NULL")
    created_at: Mapped[datetime] = created_at()


class PendingDismissal(Base):
    """"It's fine" on a pending. Pendings themselves are computed, not stored."""
    __tablename__ = "pending_dismissals"
    id: Mapped[uuid.UUID] = pk()
    memo_id: Mapped[uuid.UUID] = fk("memos.id")
    kind: Mapped[str] = mapped_column(String)
    target_id: Mapped[uuid.UUID | None] = mapped_column()
    dismissed_by: Mapped[uuid.UUID | None] = fk("users.id", nullable=True, ondelete="SET NULL")
    created_at: Mapped[datetime] = created_at()


class MemoUpdate(Base):
    """"See changes" → Apply / Ignore, for a new record revision or spec version."""
    __tablename__ = "memo_updates"
    id: Mapped[uuid.UUID] = pk()
    memo_id: Mapped[uuid.UUID] = fk("memos.id")
    kind: Mapped[str] = mapped_column(String)                   # record, spec
    from_id: Mapped[uuid.UUID] = mapped_column()
    to_id: Mapped[uuid.UUID] = mapped_column()
    status: Mapped[str] = mapped_column(String)                 # applied, ignored
    decided_by: Mapped[uuid.UUID | None] = fk("users.id", nullable=True, ondelete="SET NULL")
    change_set_id: Mapped[uuid.UUID | None] = fk("change_sets.id", nullable=True, ondelete="SET NULL")
    created_at: Mapped[datetime] = created_at()


class Review(Base):
    """Final review: every check run on the server at one point of the memo's history."""
    __tablename__ = "reviews"
    id: Mapped[uuid.UUID] = pk()
    memo_id: Mapped[uuid.UUID] = fk("memos.id")
    sequence: Mapped[int] = mapped_column(Integer)
    by: Mapped[uuid.UUID | None] = fk("users.id", nullable=True, ondelete="SET NULL")
    passed: Mapped[bool] = mapped_column(Boolean)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    report: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()


class ChatThread(Base):
    __tablename__ = "chat_threads"
    id: Mapped[uuid.UUID] = pk()
    memo_id: Mapped[uuid.UUID] = fk("memos.id")
    created_by: Mapped[uuid.UUID | None] = fk("users.id", nullable=True, ondelete="SET NULL")
    created_at: Mapped[datetime] = created_at()


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[uuid.UUID] = pk()
    thread_id: Mapped[uuid.UUID] = fk("chat_threads.id")
    author_type: Mapped[str] = mapped_column(String)            # person, agent
    author_id: Mapped[uuid.UUID | None] = mapped_column()
    text: Mapped[str] = mapped_column(Text)
    change_set_id: Mapped[uuid.UUID | None] = fk("change_sets.id", nullable=True, ondelete="SET NULL")
    created_at: Mapped[datetime] = created_at()


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[uuid.UUID] = pk()
    message_id: Mapped[uuid.UUID] = fk("chat_messages.id")
    model: Mapped[str] = mapped_column(String)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    seconds: Mapped[float] = mapped_column(Float, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    validation_errors: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()


class Export(Base):
    __tablename__ = "exports"
    id: Mapped[uuid.UUID] = pk()
    memo_id: Mapped[uuid.UUID] = fk("memos.id")
    sequence: Mapped[int] = mapped_column(Integer)
    format: Mapped[str] = mapped_column(String)                 # pptx, pdf
    asset_id: Mapped[uuid.UUID | None] = fk("assets.id", nullable=True, ondelete="SET NULL")
    seconds: Mapped[float | None] = mapped_column(Float)
    created_by: Mapped[uuid.UUID | None] = fk("users.id", nullable=True, ondelete="SET NULL")
    created_at: Mapped[datetime] = created_at()


class Import(Base):
    __tablename__ = "imports"
    id: Mapped[uuid.UUID] = pk()
    memo_id: Mapped[uuid.UUID] = fk("memos.id")
    export_id: Mapped[uuid.UUID | None] = fk("exports.id", nullable=True, ondelete="SET NULL")
    asset_id: Mapped[uuid.UUID | None] = fk("assets.id", nullable=True, ondelete="SET NULL")
    change_set_id: Mapped[uuid.UUID | None] = fk("change_sets.id", nullable=True, ondelete="SET NULL")
    report: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = fk("users.id", nullable=True, ondelete="SET NULL")
    created_at: Mapped[datetime] = created_at()
