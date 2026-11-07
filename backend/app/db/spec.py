"""Firms, users and the firm spec (docs/SCHEMA_DESIGN.md §5). Spec rows hang from a spec version."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Frame, InSpecVersion, TextStyle, created_at, fk, pk


# --- accounts -----------------------------------------------------------------------------------

class Firm(Base):
    """An isolated account: everything a user sees belongs to their firm."""
    __tablename__ = "firms"
    id: Mapped[uuid.UUID] = pk()
    name: Mapped[str] = mapped_column(String, unique=True)
    created_at: Mapped[datetime] = created_at()


class User(Base):
    """Mock auth: users are picked by email; no real login."""
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = pk()
    email: Mapped[str] = mapped_column(String, unique=True)
    display_name: Mapped[str | None] = mapped_column(String)
    firm_id: Mapped[uuid.UUID] = fk("firms.id")


# --- specs and versions -------------------------------------------------------------------------

class Spec(Base):
    __tablename__ = "specs"
    id: Mapped[uuid.UUID] = pk()
    firm_id: Mapped[uuid.UUID] = fk("firms.id")
    name: Mapped[str] = mapped_column(String)                  # set by the user: "IC memo", "Screening"
    created_by: Mapped[uuid.UUID | None] = fk("users.id", nullable=True, ondelete="SET NULL")
    created_at: Mapped[datetime] = created_at()


class SpecVersion(Base):
    """Immutable once approved; each version is a full copy of its rows."""
    __tablename__ = "spec_versions"
    id: Mapped[uuid.UUID] = pk()
    spec_id: Mapped[uuid.UUID] = fk("specs.id")
    number: Mapped[int] = mapped_column(Integer)
    label: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="draft")   # draft, approved, superseded
    created_from: Mapped[str | None] = mapped_column(String)       # onboarding, edit, promoted_slide, pilot_import
    created_from_id: Mapped[uuid.UUID | None] = mapped_column()    # the run, version or slide it came from
    page_w_in: Mapped[float | None] = mapped_column(Float)
    page_h_in: Mapped[float | None] = mapped_column(Float)
    approved_by: Mapped[uuid.UUID | None] = fk("users.id", nullable=True, ondelete="SET NULL")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()


# --- onboarding ---------------------------------------------------------------------------------

class OnboardingRun(Base):
    __tablename__ = "onboarding_runs"
    id: Mapped[uuid.UUID] = pk()
    firm_id: Mapped[uuid.UUID] = fk("firms.id")
    spec_version_id: Mapped[uuid.UUID | None] = fk("spec_versions.id", nullable=True, ondelete="SET NULL")
    status: Mapped[str] = mapped_column(String, default="running")  # running, review, approved, failed
    created_at: Mapped[datetime] = created_at()


class Material(Base):
    __tablename__ = "materials"
    id: Mapped[uuid.UUID] = pk()
    run_id: Mapped[uuid.UUID] = fk("onboarding_runs.id")
    kind: Mapped[str] = mapped_column(String)                   # guide, template, memo, logo
    asset_id: Mapped[uuid.UUID | None] = fk("assets.id", nullable=True, ondelete="SET NULL")
    file_name: Mapped[str] = mapped_column(String)
    extraction_path: Mapped[str | None] = mapped_column(String)


class OnboardingStep(Base):
    __tablename__ = "onboarding_steps"
    id: Mapped[uuid.UUID] = pk()
    run_id: Mapped[uuid.UUID] = fk("onboarding_runs.id")
    number: Mapped[int] = mapped_column(Integer)                # 0 extract … 5 rules
    status: Mapped[str] = mapped_column(String, default="pending")  # pending, running, review, approved, failed
    model_calls: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    seconds: Mapped[float] = mapped_column(Float, default=0)
    unresolved: Mapped[int] = mapped_column(Integer, default=0)
    approved_by: Mapped[uuid.UUID | None] = fk("users.id", nullable=True, ondelete="SET NULL")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConflictDecision(InSpecVersion, Base):
    """Two materials disagree; a person chooses (no fixed precedence)."""
    __tablename__ = "conflict_decisions"
    id: Mapped[uuid.UUID] = pk()
    topic: Mapped[str] = mapped_column(String)
    chosen_option_id: Mapped[uuid.UUID | None] = mapped_column()
    decided_by: Mapped[uuid.UUID | None] = fk("users.id", nullable=True, ondelete="SET NULL")


class ConflictOption(Base):
    __tablename__ = "conflict_options"
    id: Mapped[uuid.UUID] = pk()
    decision_id: Mapped[uuid.UUID] = fk("conflict_decisions.id")
    option_source: Mapped[str] = mapped_column(String)         # guide, template, memo
    value: Mapped[str] = mapped_column(Text)
    evidence: Mapped[str | None] = mapped_column(Text)


# --- house style --------------------------------------------------------------------------------

class PaletteColor(InSpecVersion, Base):
    __tablename__ = "palette_colors"
    id: Mapped[uuid.UUID] = pk()
    name: Mapped[str] = mapped_column(String)
    hex: Mapped[str] = mapped_column(String)
    use_rule: Mapped[str | None] = mapped_column(Text)


class Font(InSpecVersion, Base):
    __tablename__ = "fonts"
    id: Mapped[uuid.UUID] = pk()
    role: Mapped[str] = mapped_column(String)                   # heading, body, tables
    family: Mapped[str] = mapped_column(String)
    font_file_asset_id: Mapped[uuid.UUID | None] = fk("assets.id", nullable=True, ondelete="SET NULL")
    substitute_family: Mapped[str | None] = mapped_column(String)


class TextRole(InSpecVersion, Base):
    __tablename__ = "text_roles"
    id: Mapped[uuid.UUID] = pk()
    name: Mapped[str] = mapped_column(String)                   # action_title, body, source_line, kpi_value…
    applies_to: Mapped[str | None] = mapped_column(Text)
    font: Mapped[str | None] = mapped_column(String)
    size_pt: Mapped[float | None] = mapped_column(Float)
    min_size_pt: Mapped[float | None] = mapped_column(Float)
    max_size_pt: Mapped[float | None] = mapped_column(Float)
    bold: Mapped[bool | None] = mapped_column(Boolean)
    italic: Mapped[bool | None] = mapped_column(Boolean)
    caps: Mapped[bool | None] = mapped_column(Boolean)
    color_ref: Mapped[str | None] = mapped_column(String)


class Format(InSpecVersion, Base):
    __tablename__ = "formats"
    id: Mapped[uuid.UUID] = pk()
    item: Mapped[str] = mapped_column(String)                   # money, negative, percent, date, multiple…
    context: Mapped[str] = mapped_column(String, default="any") # prose, table, chart, any
    rule: Mapped[str] = mapped_column(Text)
    example: Mapped[str | None] = mapped_column(String)
    applies_to: Mapped[str | None] = mapped_column(Text)              # which figures, when a kind is written two ways
    # G8: fields a program writes numbers with (app/services/formats.py)
    decimals: Mapped[int | None] = mapped_column(Integer)
    symbol: Mapped[str | None] = mapped_column(String)
    symbol_position: Mapped[str | None] = mapped_column(String)      # before, after
    unit_display: Mapped[str | None] = mapped_column(String)         # inline (£22.9m), header (unit in the column header)
    negative_style: Mapped[str | None] = mapped_column(String)       # minus, parentheses, unicode_minus
    thousands_separator: Mapped[str | None] = mapped_column(String)
    decimal_separator: Mapped[str | None] = mapped_column(String)
    suffix: Mapped[str | None] = mapped_column(String)               # "x" for multiples
    pattern: Mapped[str | None] = mapped_column(String)              # dates: YYYY YY MMMM MMM MM M DD D


class FormatScale(Base):
    """One range of a money format: from this value up, write it in this scale with these decimals."""
    __tablename__ = "format_scales"
    id: Mapped[uuid.UUID] = pk()
    format_id: Mapped[uuid.UUID] = fk("formats.id")
    scale: Mapped[str] = mapped_column(String)                       # units, k, m, bn
    from_value: Mapped[float] = mapped_column(Float, default=0)
    decimals: Mapped[int] = mapped_column(Integer, default=0)


class Grid(InSpecVersion, Base):
    __tablename__ = "grids"
    id: Mapped[uuid.UUID] = pk()
    margin_top_in: Mapped[float | None] = mapped_column(Float)
    margin_right_in: Mapped[float | None] = mapped_column(Float)
    margin_bottom_in: Mapped[float | None] = mapped_column(Float)
    margin_left_in: Mapped[float | None] = mapped_column(Float)
    columns: Mapped[int | None] = mapped_column(Integer)
    gutter_in: Mapped[float | None] = mapped_column(Float)
    snap_in: Mapped[float | None] = mapped_column(Float)


class TableStyle(InSpecVersion, Base):
    """G3: table style as fields a painter can apply."""
    __tablename__ = "table_styles"
    id: Mapped[uuid.UUID] = pk()
    name: Mapped[str] = mapped_column(String, default="default")
    header_fill_ref: Mapped[str | None] = mapped_column(String)
    header_text_ref: Mapped[str | None] = mapped_column(String)
    header_bold: Mapped[bool | None] = mapped_column(Boolean)
    forecast_header_fill_ref: Mapped[str | None] = mapped_column(String)   # header cells of forecast periods
    body_text_ref: Mapped[str | None] = mapped_column(String)
    banding_ref: Mapped[str | None] = mapped_column(String)
    rule_color_ref: Mapped[str | None] = mapped_column(String)
    rule_width_pt: Mapped[float | None] = mapped_column(Float)
    header_rule_width_pt: Mapped[float | None] = mapped_column(Float)
    total_bold: Mapped[bool | None] = mapped_column(Boolean)
    highlight_color_ref: Mapped[str | None] = mapped_column(String)
    highlight_bold: Mapped[bool | None] = mapped_column(Boolean)
    number_align: Mapped[str | None] = mapped_column(String)
    min_size_pt: Mapped[float | None] = mapped_column(Float)


class ChartStyle(InSpecVersion, Base):
    __tablename__ = "chart_styles"
    id: Mapped[uuid.UUID] = pk()
    native_only: Mapped[bool] = mapped_column(Boolean, default=True)
    labels_on: Mapped[bool | None] = mapped_column(Boolean)
    label_size_pt: Mapped[float | None] = mapped_column(Float)
    gridlines: Mapped[bool | None] = mapped_column(Boolean)
    legend_rule: Mapped[str | None] = mapped_column(Text)
    title_rule: Mapped[str | None] = mapped_column(Text)
    forecast_treatment: Mapped[str | None] = mapped_column(Text)
    focus_color_ref: Mapped[str | None] = mapped_column(String)


class ChartSeriesColor(Base):
    __tablename__ = "chart_series_colors"
    id: Mapped[uuid.UUID] = pk()
    chart_style_id: Mapped[uuid.UUID] = fk("chart_styles.id")
    position: Mapped[int] = mapped_column(Integer)
    color_ref: Mapped[str] = mapped_column(String)


class CitationScheme(InSpecVersion, Base):
    __tablename__ = "citation_schemes"
    id: Mapped[uuid.UUID] = pk()
    scheme: Mapped[str] = mapped_column(String)  # per_slide_letters, per_slide_numbered_notes, global_numbered_references, none
    marker: Mapped[str | None] = mapped_column(String)
    placement: Mapped[str | None] = mapped_column(Text)


class FooterItem(InSpecVersion, Base):
    __tablename__ = "footer_items"
    id: Mapped[uuid.UUID] = pk()
    position: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String)                   # fixed, memo_data, fact, page_number
    text: Mapped[str | None] = mapped_column(Text)
    concept_id: Mapped[uuid.UUID | None] = fk("concepts.id", nullable=True, ondelete="SET NULL")


# --- structure ----------------------------------------------------------------------------------

class Stage(InSpecVersion, Base):
    __tablename__ = "stages"
    id: Mapped[uuid.UUID] = pk()
    name: Mapped[str] = mapped_column(String)
    position: Mapped[int] = mapped_column(Integer)


class Section(InSpecVersion, Base):
    __tablename__ = "sections"
    id: Mapped[uuid.UUID] = pk()
    name: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)                 # locked, mandatory, optional, free
    count: Mapped[str] = mapped_column(String, default="one")   # one, any, n
    count_n: Mapped[int | None] = mapped_column(Integer)
    must_contain: Mapped[str | None] = mapped_column(Text)
    numbering_scheme: Mapped[str | None] = mapped_column(String)
    position: Mapped[int] = mapped_column(Integer)


class SectionLayout(Base):
    __tablename__ = "section_layouts"
    id: Mapped[uuid.UUID] = pk()
    section_id: Mapped[uuid.UUID] = fk("sections.id")
    layout_id: Mapped[uuid.UUID] = fk("layouts.id")


class OrderConstraint(InSpecVersion, Base):
    __tablename__ = "order_constraints"
    id: Mapped[uuid.UUID] = pk()
    section_id: Mapped[uuid.UUID] = fk("sections.id")
    kind: Mapped[str] = mapped_column(String)  # first, last, position, after, before, immediately_after, immediately_before, after_all
    target_section_id: Mapped[uuid.UUID | None] = fk("sections.id", nullable=True)
    target_status: Mapped[str | None] = mapped_column(String)
    position: Mapped[int | None] = mapped_column(Integer)


class StageSection(Base):
    __tablename__ = "stage_sections"
    id: Mapped[uuid.UUID] = pk()
    stage_id: Mapped[uuid.UUID] = fk("stages.id")
    section_id: Mapped[uuid.UUID] = fk("sections.id")
    required: Mapped[bool] = mapped_column(Boolean, default=True)


# --- layouts (their elements live in the shared `elements` table, see memo.py) ------------------

class Layout(InSpecVersion, Base):
    __tablename__ = "layouts"
    id: Mapped[uuid.UUID] = pk()
    name: Mapped[str] = mapped_column(String)
    source_kind: Mapped[str] = mapped_column(String)            # imported, generated, promoted
    is_master: Mapped[bool] = mapped_column(Boolean, default=False)
    is_blank: Mapped[bool] = mapped_column(Boolean, default=False)
    shows_master: Mapped[bool] = mapped_column(Boolean, default=True)
    background_ref: Mapped[str | None] = mapped_column(String)  # G2
    background_asset_id: Mapped[uuid.UUID | None] = fk("assets.id", nullable=True, ondelete="SET NULL")
    position: Mapped[int] = mapped_column(Integer, default=0)


class LayoutZone(Frame, Base):
    __tablename__ = "layout_zones"
    id: Mapped[uuid.UUID] = pk()
    layout_id: Mapped[uuid.UUID] = fk("layouts.id")
    kind: Mapped[str] = mapped_column(String)                   # locked, free


# --- recipe and concepts ------------------------------------------------------------------------

class Concept(InSpecVersion, Base):
    __tablename__ = "concepts"
    id: Mapped[uuid.UUID] = pk()
    name: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)                   # figure, fact, memo_data
    value_kind: Mapped[str | None] = mapped_column(String)      # money, percent, multiple, date, text…
    description: Mapped[str | None] = mapped_column(Text)


class RecipeSegment(InSpecVersion, Base):
    """What goes in a layout element, paragraph by paragraph (G1)."""
    __tablename__ = "recipe_segments"
    id: Mapped[uuid.UUID] = pk()
    element_id: Mapped[uuid.UUID] = fk("elements.id")
    paragraph: Mapped[int] = mapped_column(Integer, default=0)
    position: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String)  # fixed, figure, fact, memo_data, conditional, model_writes, person_writes
    text: Mapped[str | None] = mapped_column(Text)
    concept_id: Mapped[uuid.UUID | None] = fk("concepts.id", nullable=True, ondelete="SET NULL")
    when_rule: Mapped[str | None] = mapped_column(Text)
    what: Mapped[str | None] = mapped_column(Text)
    seen_in: Mapped[int | None] = mapped_column(Integer)
    memos_with_box: Mapped[int | None] = mapped_column(Integer)


class RecipeMustInclude(Base):
    __tablename__ = "recipe_must_include"
    id: Mapped[uuid.UUID] = pk()
    recipe_segment_id: Mapped[uuid.UUID] = fk("recipe_segments.id")
    concept_id: Mapped[uuid.UUID] = fk("concepts.id")


class StyleExample(Base):
    __tablename__ = "style_examples"
    id: Mapped[uuid.UUID] = pk()
    recipe_segment_id: Mapped[uuid.UUID] = fk("recipe_segments.id")
    memo_name: Mapped[str | None] = mapped_column(String)
    text: Mapped[str] = mapped_column(Text)


# --- table and chart templates (G4, G5, G6) -----------------------------------------------------

class TableTemplate(InSpecVersion, Base):
    __tablename__ = "table_templates"
    id: Mapped[uuid.UUID] = pk()
    element_id: Mapped[uuid.UUID] = fk("elements.id")
    table_style_id: Mapped[uuid.UUID | None] = fk("table_styles.id", nullable=True, ondelete="SET NULL")


class TableTemplateColumn(Base):
    __tablename__ = "table_template_columns"
    id: Mapped[uuid.UUID] = pk()
    table_template_id: Mapped[uuid.UUID] = fk("table_templates.id")
    position: Mapped[int] = mapped_column(Integer)
    width_in: Mapped[float | None] = mapped_column(Float)
    header_text: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String)                   # label, period, fixed, data


class TableTemplateRow(Base):
    __tablename__ = "table_template_rows"
    id: Mapped[uuid.UUID] = pk()
    table_template_id: Mapped[uuid.UUID] = fk("table_templates.id")
    position: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String)                   # header, group, data, total
    label: Mapped[str | None] = mapped_column(Text)
    concept_id: Mapped[uuid.UUID | None] = fk("concepts.id", nullable=True, ondelete="SET NULL")
    height_in: Mapped[float | None] = mapped_column(Float)


class TableTemplateCell(Base):
    __tablename__ = "table_template_cells"
    id: Mapped[uuid.UUID] = pk()
    table_template_id: Mapped[uuid.UUID] = fk("table_templates.id")
    row: Mapped[int] = mapped_column(Integer)
    col: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String)                   # fixed, data
    fixed_text: Mapped[str | None] = mapped_column(Text)
    fill_ref: Mapped[str | None] = mapped_column(String)


class TableColumnRule(Base):
    __tablename__ = "table_column_rules"
    id: Mapped[uuid.UUID] = pk()
    table_template_id: Mapped[uuid.UUID] = fk("table_templates.id")
    actual_periods: Mapped[int | None] = mapped_column(Integer)
    forecast_periods: Mapped[int | None] = mapped_column(Integer)
    period_label_format: Mapped[str | None] = mapped_column(String)


class ChartTemplate(InSpecVersion, Base):
    __tablename__ = "chart_templates"
    id: Mapped[uuid.UUID] = pk()
    element_id: Mapped[uuid.UUID] = fk("elements.id")
    chart_type: Mapped[str] = mapped_column(String)             # bar, line, stacked_bar, waterfall…
    category_rule: Mapped[str | None] = mapped_column(String)   # periods, items
    category_count: Mapped[int | None] = mapped_column(Integer)


class ChartTemplateSeries(Base):
    __tablename__ = "chart_template_series"
    id: Mapped[uuid.UUID] = pk()
    chart_template_id: Mapped[uuid.UUID] = fk("chart_templates.id")
    position: Mapped[int] = mapped_column(Integer)
    concept_id: Mapped[uuid.UUID | None] = fk("concepts.id", nullable=True, ondelete="SET NULL")
    color_ref: Mapped[str | None] = mapped_column(String)
    forecast_color_ref: Mapped[str | None] = mapped_column(String)
    label_rule: Mapped[str | None] = mapped_column(Text)


# --- rules --------------------------------------------------------------------------------------

class Rule(InSpecVersion, Base):
    __tablename__ = "rules"
    id: Mapped[uuid.UUID] = pk()
    type: Mapped[str] = mapped_column(String)                   # one of the 14 library types, or "unmapped"
    section_id: Mapped[uuid.UUID | None] = fk("sections.id", nullable=True, ondelete="SET NULL")
    says: Mapped[str] = mapped_column(Text)


class RuleParam(Base):
    """One row per parameter: (rule 7, max, 5), (rule 7, when_exceeded, appendix)."""
    __tablename__ = "rule_params"
    id: Mapped[uuid.UUID] = pk()
    rule_id: Mapped[uuid.UUID] = fk("rules.id")
    name: Mapped[str] = mapped_column(String)
    value: Mapped[str | None] = mapped_column(Text)
    value_kind: Mapped[str] = mapped_column(String, default="text")  # text, number, bool, section, concept, color, layout
    ref_id: Mapped[uuid.UUID | None] = mapped_column()               # the section, concept or layout when value_kind says so


class Binding(Base):
    """Concept → record path, once per firm and record schema (outside onboarding)."""
    __tablename__ = "bindings"
    id: Mapped[uuid.UUID] = pk()
    firm_id: Mapped[uuid.UUID] = fk("firms.id")
    record_schema: Mapped[str] = mapped_column(String)
    concept_name: Mapped[str] = mapped_column(String)           # by name: survives new spec versions
    kind: Mapped[str] = mapped_column(String)                   # path, derived, missing
    path: Mapped[str | None] = mapped_column(String)
    formula: Mapped[str | None] = mapped_column(Text)
    approved_by: Mapped[uuid.UUID | None] = fk("users.id", nullable=True, ondelete="SET NULL")
    created_at: Mapped[datetime] = created_at()
