# Schema design: firm spec, memo, collaboration, AI edits

Status: **draft for review, not built.** Inputs: the brief (as checklist in `BIBLE.md` §2), the onboarding pilot
(`docs/pilots/onboarding_pilot/README.md`) and the preview pilot (`docs/pilots/preview/README.md`).
Decisions below were taken in review on 6 Nov 2026.

## 1. What the schema must carry

| Requirement | From |
|---|---|
| Firm config as tables and columns, versioned, with provenance for every value | brief capability 1; check-in feedback |
| Grid and locked/free zones; one-off slides move and resize on the firm's grid inside locked header, footer and sidebar | brief capabilities 1 and 3 |
| One element model for layouts, memo slides, imported slides and (later) template editing; two painters (canvas HTML, PPTX) read it | BIBLE §3 (Editing) |
| Gaps G1–G7: several text styles in one box; layout background; table style as fields; table structure; concrete table rows and column rules; chart data model; master boxes with a recipe | preview pilot |
| Several people edit one memo at once and see each other live | brief capability 3; the demo |
| AI chat in the memo side panel; its edits are checked operations | brief capability 5 |
| Every edit is an operation, checked on the server, logged | brief capability 3 |
| PowerPoint round trip: identity survives export and import | brief capability 6; "where the difficulty lives" |
| A memo is pinned to (spec version, record revision); a new version shows what changed and what needs a person | brief capability 1 |
| A figure in text, table and chart changes in all three; no number the record does not hold | brief capability 5 |
| Custom slides: blank, uploaded, agent-built; promote one into the firm's template with approval | brief capability 4 |

## 2. Decisions (6 Nov review)

1. **Record figures are never edited in a memo**: not on the canvas, not by the AI, not in a re-imported
   PPTX. The attempt is refused with a modal ("this figure comes from the deal record"). A record changes
   only by a new record revision. (The brief allows "goes to the record **or is refused**".)
2. **Firm-locked content** (spec): same, modal and refused.
3. **Two kinds of check:**
   - **Blocking**, applied to the edit: an edit that is wrong whatever happens next (editing a chip,
     moving a locked box, an off-palette colour, a figure the record does not hold from the AI).
   - **Pending**, computed on the memo: an unfinished memo (a mandatory section missing, more items than
     allowed, text that does not fit, a person-writes box empty, a hand-typed figure not from the record).
     Pendings never block editing or exporting.
4. **Final review button.** Runs every check on the server: no pendings left, every chip matches the pinned
   record revision, nothing locked moved or deleted, formats and colours are the firm's, text fits (measured
   with the real fonts). If it passes, the memo is "Reviewed" (who, when, at which operation). Any later
   edit returns it to "Editing".
5. **Notifications bell** in the canvas toolbar: new record revision, new spec version, pendings,
   kept-aside items, AI proposals waiting, import reports. **Computed from state, not stored**, so a notice
   disappears when it stops being true.
6. **Deal record per firm and deal**, versioned: revision 1 is the original upload; every edit creates the
   next revision; nothing is overwritten.
7. **Specs:** a firm has several specs, each **named by the user** (e.g. "IC memo", "Screening"). Editing a
   spec creates its next **version** (optional label); a brand-new spec is another spec. An approved version
   never changes. Each version is a **full copy** of its rows (simple to read and paint; onboarding is rare).
8. **New record revision or spec version:** a notice, "See changes" (before → after), and two buttons:
   **Apply** or **Ignore**. Applying first closes and saves every open box, then goes in as one change set
   and moves the pin. Applying **never deletes content**: a slide whose layout is gone stays as a free slide,
   a chip whose value is gone shows "no value"; both appear as pendings and are marked ⚠ in "See changes".
9. **One person per box** (lock given by the server, in memory). Freed when the person leaves the box,
   disconnects, or after ~2 minutes without typing (what they wrote is saved first). No merging inside a box.
10. **Real time on our own websockets** (one room per memo in the backend; idea from memo-engine's `live.py`):
    - live channel, ephemeral, never stored: text being typed (sent every ~100 ms), a box being dragged;
    - commit channel: when the person pauses, leaves the box or drops it, the edit goes as an operation,
      checked and stored; a refused move snaps back for everyone.
    - Catch-up after a reconnection is not built for the demo (reload the memo); the infrastructure for it
      is the per-memo operation sequence, which exists anyway.
    - Growth path: the realtime module has a small interface (join, publish, receive, take/free a box);
      more server processes later means Redis or Postgres LISTEN/NOTIFY behind it, nothing else changes.
11. **AI edits** are always a proposal and always need one click to apply. The agent only sees what the
    person may edit; its proposal goes through the same checks before it is shown (failures are retried
    with the errors, never shown). It may use any value the record holds (choose it, cite it, change which
    record value a chip shows, write around it); it never writes a number the record does not hold.
    First cases: "shorten the title"; "rewrite this paragraph in the firm's style" (chips untouched);
    "update the growth rate" = show another record value (e.g. FY25 instead of FY24) in text, table and chart
    at once, or, for a new value, answer that it lives in the record.
12. **A number typed by a person that is not a chip** (currency, percent, multiple): a pending "figure not
    from the record". "Convert to chip" lists the record values that match and **the person chooses**; no
    match, no offer. "It's fine" dismisses it for good. Never blocks, never converts by itself.
13. **Custom slides:**
    - **Blank slide:** a free slide on the firm's blank layout (or master): header, footer and logo already
      placed and locked; boxes added in the free zone snap to the firm's grid; text uses the firm's roles,
      colours its palette, figures are chips.
    - **Upload a PPTX** (drop the file): the native path; the same reader as onboarding turns each shape into
      an element; styles map to the firm's roles and palette; what is not native comes in as a flagged picture.
    - **Paste from PowerPoint:** a browser most likely receives only an image, text or HTML. **To test** before
      promising anything (desktop and web PowerPoint). A pasted image comes in flagged, with "upload the PPTX
      to get it editable".
    - **Promote to the template:** "propose to the template" on a free slide creates a draft spec version with
      that slide as a layout; a person approves; memos get the new-version notice. Built last.
14. **Version history UI** is not in the demo; the operation log that would feed it is.

## 3. Five stores, kept apart

```
DEAL RECORD              FIRM SPEC                         MEMO (live)                  HISTORY (append-only)
per firm and deal,       firm → specs (named) →            memos → slides → elements    operations, change_sets,
revisions immutable      versions (immutable once          → paragraphs → segments      conflicts (kept aside),
                         approved): everything the         pinned to spec version       memo_updates, exports,
                         onboarding produced               and record revision          imports, chat, reviews
                         bindings (concept → record path)
```

Presence, box locks and live drafts live in the backend's memory, not in the database.

## 4. Shared value columns (repeated in many tables)

- **Frame:** `x_in, y_in, w_in, h_in, rotation_deg, z_index`.
- **Text style:** `font, size_pt, bold, italic, caps, color_ref, align, anchor, line_spacing, space_before_pt,
  space_after_pt, bullet_char, bullet_color_ref, indent_in`. A `color_ref` points to the spec's palette, never
  a raw hex, so a palette change reaches every element.
- **Provenance** (every spec row): `source` (guide, template, memo, person, assumed, generated),
  `evidence_quote`, `evidence_ref`, `confirmed_by`, `confirmed_at`.

## 5. Firm spec

Every table below hangs from `spec_version_id` and carries the provenance columns.

| Table | Key columns | Notes |
|---|---|---|
| `firms` | id, name | Each firm is an isolated account: every top-level table carries `firm_id` and the API filters by the user's firm |
| `users` | id, email, display_name, firm_id | Mock auth: users picked by email, no real login |
| `specs` | id, firm_id, **name** (set by the user), created_by | "IC memo", "Screening" |
| `spec_versions` | id, spec_id, number, **label** (optional), status (draft, approved, superseded), created_from (onboarding run, edit of version N, promoted slide), approved_by/at | Immutable once approved; full copy of rows |
| `onboarding_runs`, `onboarding_steps` | run: firm, materials; step: number, status, model calls, tokens, seconds, review status | The pilot's steps and reviews |
| `materials` | run_id, kind (guide, template, memo, logo), file, extraction ref | |
| `conflict_decisions` | topic, chosen option, decided_by; `conflict_options` (source, value, evidence) | No fixed precedence: a person chooses |
| `palette_colors` | name, hex, use_rule | |
| `fonts` | role (heading, body, tables), family, font_file_asset_id, substitute_family | The server measures fit with the file |
| `text_roles` | name, applies_to, font, size_pt, min_size_pt, max_size_pt, bold, caps, color_ref | |
| `formats` | item (money, negative, percent, date…), context (prose, table, chart, any), rule, example | |
| `grid` | margin_top/right/bottom/left_in, columns, gutter_in, snap_in | One per spec version |
| `table_styles` **(G3)** | header_fill_ref, header_text_ref, header_bold, body_text_ref, banding_ref, rule_color_ref, rule_width_pt, header_rule_width_pt, total_bold, highlight_color_ref, number_align, min_size_pt, overflow (split, move_to_section) | Fields, not prose |
| `chart_styles` | native_only, labels_on, label_size_pt, gridlines, legend_rule, title_rule, forecast_treatment, focus_color_ref; `chart_series_colors` (position, color_ref) | |
| `citation_schemes` | scheme (per_slide_letters, per_slide_numbered_notes, global_numbered_references, none), marker, placement | |
| `footer_items` | position, kind (fixed, memo_data, fact, page_number), text, concept_id | Fills master boxes (G7) |
| `stages` | name, position | |
| `sections` | name, status (locked, mandatory, optional, free), count (one, any, n), must_contain, numbering_scheme, position | |
| `section_layouts` | section_id, layout_id | |
| `order_constraints` | section_id, kind (first, last, position, after, before, immediately_after, immediately_before, after_all), target_section_id, target_status, position | |
| `stage_sections` | stage_id, section_id, required | |
| `layouts` | name, source (imported, generated, promoted), is_master, is_blank, shows_master, **background_ref, background_image (G2)** | |
| `layout_zones` | layout_id, kind (locked, free), frame | Header/footer/sidebar locked; where new boxes may go |
| `layout_elements` | layout_id + the element model (§7) + firm lock | Same model as memo elements |
| `concepts` | name, kind (figure, fact, memo_data), value_kind (money, percent…), description | Catalogue the binding uses |
| `recipe_segments` | element (or paragraph), position, kind (fixed, figure, fact, memo_data, conditional, model_writes, person_writes), text, concept_id, when_rule, what, seen_in, memos_with_box; `recipe_must_include` (concept_id) | Per paragraph (G1) |
| `style_examples` | recipe_segment_id, memo_name, text | Firm wording for model-written parts |
| `table_templates` **(G4, G5)** | layout_element_id; `table_template_columns` (position, width_in, header_text, kind: label, period, fixed, data); `table_template_rows` (position, kind: header, group, data, total; label, concept_id, height_in); `table_template_cells` (row, column, fixed_text, fill_ref, kind: fixed or data); `table_column_rules` (actual_periods, forecast_periods, period_label_format) | Structure from the template's sample table |
| `chart_templates` **(G6)** | layout_element_id, chart_type, category_rule (periods, items), category_count; `chart_template_series` (position, concept_id, color_ref, forecast_color_ref, label_rule) | |
| `rules` | id, type (one of the 14 library types), section_id, says | The rule itself |
| `rule_params` | rule_id, name, value, value_kind, ref (section, concept, colour or layout when the value is one) | One row per parameter: e.g. (7, max, 5), (7, when_exceeded, appendix) |
| `bindings` | firm_id, record_schema, concept_id, kind (path, derived, missing), path, formula, approved_by | Outside onboarding; once per firm and record schema |

## 6. Deal record

| Table | Key columns |
|---|---|
| `deals` | id, name |
| `record_revisions` | id, deal_id, **firm_id**, number (1 = original upload), as_of, file, created_by, previous_id |
| `record_values` | revision_id, path, kind, value (number, text or date), unit, period |

## 7. The element model (spec layouts and memo slides share it)

| Table | Columns |
|---|---|
| `elements` | id, slide_id (memo) or layout_id (spec), origin_layout_element_id (memo: which spec box it came from; null if added), type (text, table, chart, image, shape, line, group), parent_id, **frame**, role, fill_kind (record, model, person, fixed), **firm_lock** (firm, content, free), **user_lock_by / user_lock_at** (🔒), fill_ref, line_ref, line_width_pt, **revision**, updated_by, updated_at, deleted_at |
| `element_paragraphs` **(G1)** | element_id, position, level, text style overrides |
| `segments` | paragraph_id, position, kind (text, chip, model_draft, person_slot, conditional), text, concept_id, binding_id, record_path, value_snapshot, format_ref |
| `element_tables` | element_id, table_template_id; `table_cells` (row, column, row_span, col_span, fill_ref, kind) whose text is paragraphs and segments |
| `element_charts` | element_id, chart_template_id, chart_type; `chart_series` (position, concept_id, record_path, color_ref, values snapshot) |
| `element_images` | element_id, asset_id, alt, **flagged** (a picture standing in for something that should be native) |
| `assets` | id, firm_id, kind (image, logo, font, upload, export), storage_path (Supabase Storage), hash |

A **chip** is a segment (or a cell, or a chart series) bound to a record path. Its text is never typed.
Applying a new record revision finds every chip by query and updates text, tables and charts in one change
set.

## 8. Memo, operations, collaboration, AI

| Table | Columns | Role |
|---|---|---|
| `memos` | id, firm_id, deal_id, **spec_version_id**, **record_revision_id**, stage_id, title, **sequence**, **status** (editing, reviewed), created_by | The pins |
| `slides` | id, memo_id, position, section_id, layout_id, kind (template, free, imported), revision | |
| `operations` | id, memo_id, **sequence**, change_set_id, actor_type (person, agent, import, update), actor_id, on_behalf_of, op_type, target_id, base_revision, payload (validated per op type), status (applied, refused, kept_aside), reason, created_at | Append-only; every editor writes here and nowhere else |
| `change_sets` | id, memo_id, actor_type, actor_id, on_behalf_of, summary, status (proposed, applied, rejected), source_message_id | One AI proposal, one import, one update |
| `conflicts` | id, memo_id, operation_id, element_id, kind (added_box, deleted_box, pasted_picture, changed_since_export, chart_data_changed), detail, status (open, accepted, discarded), resolved_by | Kept aside from a PPTX import; never lost |
| `pending_dismissals` | memo_id, kind, target_id, dismissed_by, at | "It's fine" on a pending; pendings themselves are computed |
| `memo_updates` | id, memo_id, kind (record, spec), from_id, to_id, status (applied, ignored), decided_by, change_set_id | "See changes" → Apply / Ignore |
| `reviews` | id, memo_id, sequence, by, at, passed, failures | Final review |
| `chat_threads`, `chat_messages` | thread: memo_id, created_by; message: author_type (person, agent), author_id, text, change_set_id | AI side panel |
| `agent_runs` | message_id, model, tokens, seconds, attempts, validation errors | Cost and evals |
| `exports` / `imports` | memo_id, sequence at export, format (pptx, pdf), file / export_id, file, change_set_id, report | Shapes named `memo:<element id>@<revision>` |

**Operation types:** set_text, set_cell, set_frame, set_style, set_chip_source (show another record value),
add_element, delete_element, add_slide, duplicate_slide, move_slide, delete_slide, set_user_lock,
apply_update.

**Write path:** client (canvas, agent proposal, import) → API → checks (locks, chips, rules, box held by
someone else) → apply, refuse or keep aside → bump the element's revision and the memo's sequence → store the
operation → broadcast the new state of the element to the room.

**PPTX import:** each shape is matched by its name. A box changed in the app since the export
(revision differs) → kept aside. A chip changed in the file → refused. Added, deleted or pasted things →
kept aside, as listed in `conflicts`.

## 9. Where JSON remains

Only `operations.payload` (its shape depends on the op type, validated per type; usual for an append-only
log). Everything else is columns and foreign keys.

## 10. Still open

- Paste from PowerPoint: what the browser actually receives (10-minute test before the canvas).
- What the firm's blank layout is when the template has none (use the master's zones).
