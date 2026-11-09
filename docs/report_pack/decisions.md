# Key technical decisions

## What the system is

Investment firms write committee memos (slide decks) in their own house format. The system learns each firm's
format once (its **spec**), keeps each deal's numbers in a **deal record**, builds a memo from both, and lets people
edit it three ways: in the browser (the **canvas**), by asking an AI **agent**, or by downloading it as PowerPoint,
editing it there and uploading it back. Every edit, from any of the three, is checked against the firm's rules
before it lands, and nothing anyone did is lost. Three firms are loaded (Merrow, Corriemoor and Lumen; Lumen came
with a written guide only, no PowerPoint template), with one deal (Tidewell).

### Words used below

| Word | Meaning |
|---|---|
| Spec | A firm's format: its slide layouts, sections and their order, fonts, colours, number formats, what goes in each box, its rules. Versioned |
| Deal record | The facts and figures of one deal for one firm. Each edit or upload makes a new **revision** |
| Memo | One memo of one deal, built from one spec version and one record revision |
| Template slide / free slide | A slide whose layout the firm fixes (people write in its boxes) / a slide of a section the firm leaves open (people can also add their own boxes in its **free area**) |
| Figure (chip) | A number or fact in the memo that comes from the record, never typed by a person |
| The door | The one place on the server that checks and applies every edit |
| Operation | One edit as the door receives it ("write this box", "move this box", "duplicate this slide") |
| Kept aside | An edit that could not be applied without overwriting someone else's work: stored as a visible **conflict**, not lost |
| Pending | Something the memo still needs from a person (a figure the record lacks, text to write, a picture to confirm) |
| Binding | Which place in a deal record each of the firm's figures comes from |

## 0. Principles behind every decision

- **Facts, rules and work are kept apart.** The record holds the facts, the spec holds the rules, the memo holds
  what people did. Each is versioned, so one can change without corrupting the others.
- **One door for every edit**, whatever the editor. One place to enforce the rules and one log of what happened.
- **A new firm is configuration, not code.** No firm's name appears in the code (an eval checks it).
- **Never lose anyone's work.** What cannot be applied is kept aside and shown, never dropped.
- **Code does what can be checked; the model does what needs judgement**, and code checks the model's output.

## 1. Database

**Problem.** The brief grades the data model. A memo has to be painted to PowerPoint, edited by several people at
once, compared with a file that comes back, and traced back to the record and the firm's rules.

**Decision.** Postgres (hosted on Supabase), accessed with SQLAlchemy and changed with Alembic migrations. Four
groups of tables; the main ones and their shape:

*Firm spec* (every table carries the spec version it belongs to):

| Table | One row is | Key columns |
|---|---|---|
| `specs`, `spec_versions` | a firm's memo type; one version of it | version number, status (draft / approved), page size, who approved it |
| `sections`, `stages`, `stage_sections` | a section of the memo; a stage (e.g. First IC); which sections a stage requires | status (locked, mandatory, free), how many slides allowed, position |
| `order_constraints`, `rules` | an order rule (A after B); a logic rule from the guide | rule type, parameters |
| `layouts`, `layout_zones` | a slide layout; its free area | name, is master / blank; zone x, y, w, h |
| `text_roles`, `fonts`, `formats`, `palette_colors`, `grids` | the house style | font, size, minimum size; number format fields; colours by name |
| `concepts`, `recipe_segments` | a figure the firm uses; what goes in each layout box | concept name and kind; segment kind (fixed words, figure, model writes, person writes) |
| `table_templates`, `chart_templates` (+ rows, columns, series) | the firm's tables and charts | which concept each row or series shows, period rules |

*Deal record*: `deals`, `record_revisions` (revision number, who made it, from which revision), `record_values`
(one row per value: path such as `table:pl_summary[FY26].revenue`, kind, number or text or date, unit, period,
forecast flag). A revision is a full copy; nothing is overwritten.

*Memo*:

| Table | One row is | Key columns |
|---|---|---|
| `memos` | a memo | deal, spec version, record revision, stage, sequence (count of operations) |
| `slides` | a slide | position, section, layout, kind (template / free) |
| `elements` | a box on a slide (also used for the boxes of a layout) | type (text, table, chart, image, shape), place and size in inches, role, lock, text insets, revision, origin (the layout box it came from) |
| `element_paragraphs`, `segments` | a paragraph with its own style; a run of text in it | font, size, spacing, bullet; segment kind (text, figure, slot to write), the figure's record path and value |
| `element_tables`, `table_cells`; `element_charts`, `chart_series`, `chart_points` | a table and its cells; a chart, its series and points | cells hold paragraphs like a text box; a series names its record family |
| `element_images`, `assets` | a picture; a stored file | the file, whether it is flagged as a pending |

*History* (only ever added to): `operations` (every edit with its result and reason), `change_sets` (edits that go
together: an agent proposal, an import), `conflicts` (what was kept aside, with its content), `memo_updates`
(decisions on a newer record or spec version), `pending_dismissals` ("It's fine", who and when), `chat_messages`,
`agent_runs`, `exports`, `imports`.

**Why.**
- The data is relational (memo → slide → box → paragraph → piece of text), and history per box matters.
- **The same box model serves the firm's layouts and the memo's slides** and mirrors PowerPoint, so the canvas,
  the PowerPoint file, the text measure and the agent all read the same rows.
- **A figure is a piece of text that remembers where it came from** (its concept and its path in the record), so
  it can be updated, traced and protected from typing.
- **A memo is read in one database round trip**, because the hosted database answers each round trip slowly
  (about 0.3 s). A 16-slide memo reads in about 0.3 s.
- Row-level security is switched on for every table (the backend is the only client).

**Not done.** Slides stored as documents (JSON): no constraints, no history per box. Editing an approved spec
version in place: memos built on it would change under people's feet.

## 2. Onboarding a firm (learning its format)

**Problem.** Each firm sends different material: a PowerPoint template, a written brand and memo guide (PDF), past
memos, logos. Lumen sent only a guide. The format must come out complete enough to paint memos, and a person must be
able to check it.

**Decision.** Six steps, shown in the Firm setup screens. Each step after the first is one call to the model with
a package of material that code prepares, then code checks the answer and sends any errors back (at most two
retries), then **a person reviews and approves it**:

0. **Materials** (code only): read every file once (layouts and their boxes, styles, guide text and page images,
   past memos slide by slide).
1. **House style**: page, colours and what each is for, fonts, text roles (size and minimum size), number and date
   formats as fields (checked against the examples in the materials), footer and citations.
2. **Structure**: the sections, their status (locked, mandatory, free), how many slides each allows, what each
   must contain, the order rules, and which sections each stage requires.
3. **Layouts**: every layout as a list of boxes. When the template has the layout, code copies its boxes exactly
   and the model only names each box's role; when no template has it (Lumen), the model designs it from the guide.
4. **Recipe**: what goes in each box: fixed words, a named figure, text the model drafts per deal, text a person
   writes; which figure each table row and chart series shows.
5. **Rules**: the guide's logic as rules of a fixed library of rule types with parameters; what no type can
   express is listed as "unmapped", with its quote.

Where the guide and the template disagree, the conflict is shown with its evidence and a person decides. The
**writer** then turns the approved steps into a new spec version, works out each layout's free area, and records
each placeholder's text insets and paragraph format as PowerPoint would inherit them from the template.

**Binding** (connecting the spec to a deal record) runs after the first record arrives: one model call proposes
where each figure comes from; a review screen flags what needs a person (no value, broken path, values that have
diverged since the last upload, two figures showing the same value, wrong kind of value). A person can change any
binding; memos built before are offered the change.

**Why.** One large model call for the whole spec would be impossible to review or validate. Splitting by step
lets code check each answer against the material and lets a person approve one thing at a time.

## 3. Deal records and updates

**Problem.** The record changes while memos are being written; people's work in the memo must survive.

**Decision.** A memo never follows a newer record by itself. It shows that a newer revision exists (live, to
everyone in the memo); **See changes** lists every figure before → after, with warnings; then **Apply** or
**Ignore**. Each figure is worked out again the way it was made: through the binding when it is one of the firm's
figures (so "latest period" figures move on), otherwise by its place in the record. Applying changes figures only;
people's words stay.

**Not done.** Rebuilding the memo on every record change (it would erase people's work).

## 4. Building the memo, PowerPoint and PDF

**Decision.** The **generator** builds a memo from a spec version, a stage and a record revision: the layouts of
the sections that stage requires, each box filled by its recipe (figures from the record, "‹model: …›" or
"‹person: …›" where text is still to write, "[no data: …]" where the record has no value). It uses no model:
about 3–5 seconds, no cost. The **painter** writes a native PowerPoint on the firm's own template (its layouts,
footer and slide number, logo, native tables and charts), naming every slide and shape with its identity in the
memo. The **PDF** is that PowerPoint converted, so it reads exactly as the editable file.

**Not done / not tested.** A separate PDF renderer. Converting with LibreOffice (for a server) is written but was
not tested here: this machine converts with PowerPoint.

## 5. The door: how every edit is applied

**Problem.** Edits arrive from three editors, from several people, sometimes from a stale view.

**Decision.** Every edit is an **operation** checked on the server against the firm's rules before it lands: boxes
the firm locks, free areas, the grid, the palette, how many slides a section allows and in what order, figures
never typed over. A refused edit says why. Each box carries a revision number: an edit made on an older revision
is **kept aside** as a conflict rather than overwriting. A dry run answers "what would this do" without doing it
(used for agent proposals and warnings). A box edit takes about half a second against the hosted database.

## 6. Several people at once

**Decision.** One live room per memo over websockets; **one person per box** (the server gives the box; others see
who has it and the text as it is typed). Saves go through the door and everyone sees the result. Changes made
elsewhere (a newer record, a PowerPoint uploaded, a template version applied) refresh everyone's memo.

**Not done.** Merging two people's typing inside one box. Replaying missed messages after a disconnection (the memo
reloads instead).

## 7. PowerPoint as an editor

**Problem.** A file edited in PowerPoint must come back into the memo without losing either side's work. Titles,
order and positions cannot identify a box (people change them).

**Decision.** Identity lives in each shape's and slide's hidden name. An uploaded file is compared with the export
it came from; every difference becomes an operation through the door. What cannot be applied (a box changed in the
app since the export, a figure typed over, a box added on a template slide) is kept aside, never lost. **One Upload
button decides by the file**: this memo's own export comes back as changes; another memo's export is refused; any
other PowerPoint or PDF adds its slides.

## 8. The canvas, free slides, pictures and uploaded slides

**Decision.** Template slides are locked to the firm's layout: people write content, not layout. Free slides have a
free area, worked out from the layout (between its header and footer, inside the margins), where people add text,
tables and charts from the record, and pictures; boxes move and resize only inside it, snapped to the grid.
**Every picture is a pending until someone confirms it**, because a picture may be a chart pasted as an image.
Uploaded slides come in natively where possible (title into the firm's title box, text, tables, charts); pictures
and PDF pages come in flagged; what cannot come in is listed. Numbers that did not come from the record are flagged.

## 9. Figures and pendings

**Decision.** A figure is asked for by name and written from the record in the firm's format; typing over one is
refused everywhere. **Pendings are worked out from the memo as it is, never stored**, so one disappears by itself
when it stops being true; only a person's "It's fine" is stored. Pendings never block editing or exporting.

## 10. Text measured in the firm's real fonts

**Problem.** The brief: long content must continue or be split by the firm's rules, and where it cannot fit the
system says so.

**Decision.** One measure used by the canvas, the PowerPoint and the pendings: the firm's real font files, each
box's insets and paragraph format as the template defines them. Text that is too long shrinks by half points down to
the minimum size the firm allows for that role; if it still does not fit, the memo shows "does not fit" and marks
the box. Checked against PowerPoint itself, the measure agreed on 208 of 210 boxes across the three firms.

**Not done.** Continuing long text on a following slide.

## 11. Moving a memo to a newer template version

**Decision.** A memo stays on its spec version until a person moves it. See changes runs the move and undoes it,
so it shows exactly what Apply will do. Boxes are paired by layout name and role; boxes nobody touched are rebuilt
by the new version; boxes people wrote keep their words with the new look; boxes the new version no longer has are
kept aside with their content; slides whose layout is gone stay as they were, with a pending.

## 12. The AI agent

**Decision.** No agent framework and no tool loop: one model call per request, with a context code prepares (the
slide's boxes and their figures, what the firm's spec says goes in each box, the firm's rules, the memo's slides,
the record's figures). Its edits are operations checked by the door without applying them; anything wrong (an
amount typed instead of a figure, a box it may not edit, a table written as plain text) goes back to the model with
the reason, at most twice. **It never applies anything**: a person applies its proposal, change by change.

**Not done.** Letting the model write to the memo directly or type figures.

## 13. How the system is tested

**Decision.** The evals act the way people do: a server of their own, the same API the screens use, websockets like
the canvas, real PowerPoint files. What each edit should do comes from the firm's rules, not its name, so one suite
runs on every firm. Where layout matters, PowerPoint itself is the judge, not the system's own measure. Each suite
was shown to catch failures by breaking the system on purpose.

**Not done (designed, not built).** Promoting a one-off slide into the firm's template; continuing long text on
another slide; replay after a disconnection; editing a template inside the app.
