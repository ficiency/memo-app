# Onboarding pilot

A step-by-step pilot of the firm onboarding: from whatever materials a firm sends (guide, template,
past memos, logos) to a firm spec, one step at a time. Each step is built, run on the three firms,
reviewed, and only then is the next one built.

Decisions: `docs/BIBLE.md` §3 (Onboarding). Tables the steps will fill in the app: `docs/SCHEMA_DESIGN.md`.
This is a pilot: formats are provisional and taught us what the real spec schema needs.

## How every step works

```
BEFORE (code)   cut from the step 0 extraction what this step needs + read what earlier steps approved
CALL            one model call: step instructions + that package → JSON of a fixed shape
AFTER (code)    validate every part; failures go back to the model with their errors (at most 2 retries);
                whatever still fails is left for the person; save step_N.json and step_N.review.txt
REVIEW          the person reads step_N.review.txt (in the pilot, approval is automatic)
```

Rules that keep it reusable:
- No firm, section or layout names in code or prompts. A grep over `*.py` for the firms' names and
  brand words returns nothing; names appear only in the manifests (`firms/*.json`), which are data.
- What each file is (guide, template, memo, logo) comes from the manifest, never from file names.
- Prompts give the **shape** of the answer, never content examples taken from a guide.
- A fix is accepted only if it is phrased in terms of materials and structure ("outside the page",
  "if the guide states it"), not in terms of a firm's content ("plum", "forecast headers").
- No deal record is used: onboarding describes the firm. Connecting its concepts to a record happens
  when the firm's first memo is created.

Run (from `docs/pilots`):

```
python -m onboarding_pilot.step0_extract onboarding_pilot/firms/<firm>.json
python -m onboarding_pilot.step1_house_style <firm>
python -m onboarding_pilot.step2_structure <firm>      # --review-only rebuilds the review without a model call
python -m onboarding_pilot.step3_layouts <firm>        # --review-only too
python -m onboarding_pilot.step4_recipe <firm>         # --review-only too
python -m onboarding_pilot.step5_rules <firm>          # --review-only too
```

Steps: 0 extract (code only) · 1 house style · 2 structure · 3 layouts · 4 recipe · 5 rules. Connecting
concepts to a deal record is not part of onboarding: it happens when a firm's first memo is created.

Outputs go to `docs/pilots/out/onboarding/<firm>/` (ignored by git).

## What each combination of materials changes

The steps never change; only what they read and how many questions they raise. At least one material is
required; more are recommended; up to 3 past memos.

| Materials | Step 1 | Step 2 | Step 3 | Step 4 | Piloted |
|---|---|---|---|---|---|
| Guide + template + memos | guide, checked against the template theme | guide + memo outlines | **imported** from the template | template prompts + memos + guide | ✅ Merrow, Corriemoor |
| Guide + memos | guide | guide + outlines | imported from the memos' own layouts | memos + guide | ❌ |
| Memos only | inferred from the memos' theme, master and number samples; more questions | inferred from outlines | imported from the memos' own layouts | memos only: more "seen in 2 of 2" questions | 🟡 recipe only (`memo_patterns.py`, see `../README.md`) |
| Template only | template theme and styles; formats asked | from layout names and template guidance boxes | imported | template prompts only; most boxes asked | ❌ |
| **Guide only** | guide (text + images) | guide | **generated** from the guide's description and page images | the guide's "must contain" only | ✅ Lumen |

## Files

| File | What it is |
|---|---|
| `firms/*.json` | manifests: which file is the guide, the template, each memo, each logo |
| `common.py` | where each firm's outputs live; save and load step results |
| `slices.py` | cuts of the step 0 extraction, one per need (theme and styles, text looks, colours used, numbers as written) |
| `llm.py` | the shared cycle: call, validate, retry with the errors |
| `checks.py` | checks shared by steps (quotes attributed to the guide must exist in it) |
| `step0_extract.py` | step 0 |
| `step1_house_style.py` | step 1 |
| `step2_structure.py` | step 2 |
| `step3_layouts.py` | step 3 |
| `step4_recipe.py` | step 4 |
| `step5_rules.py` | step 5 |

---

## Step 0 · Extract (code only)

**Does:** reads every material once and leaves it ready for the other steps.
- Guide → text in reading order (multi-column pages read by column, tables placed under their
  heading) + one image per page (`extractors/pdf_reader.py`).
- Template and each memo → the same reader (`extractors/pptx_reader.py`): the full raw read (every
  box, run, cell, colour and position) + a compact view grouped by layout (`extractors/pptx_view.py`).
- Logos → size, transparency, main colours.

**Input:** the manifest. **Output:** `extraction/` (raw JSON, views, guide text, page images, assets),
`step_0.json` (what was sent, what is missing, where each extraction is) and `step_0.review.txt`.

**Raw vs view:** the raw read is the source of truth; code cuts from it and checks against it. The model
only ever receives cuts or the compact view (the raw template read is ~42k tokens, its view ~10k).

**Connects to the next steps:** every later step reads `step_0.json` and cuts what it needs; no file is
opened again.

**Results**

| Firm | Materials found | Missing |
|---|---|---|
| Merrow | guide 3 pages / 4 tables; template 14 layouts; 2 memos (15 and 14 slides); 2 logos | nothing |
| Corriemoor | guide 3 pages / 5 tables; template 16 layouts (4:3); 2 memos (16 and 15 slides); 2 logos | nothing |
| Lumen | guide 2 pages / 5 tables; 1 logo | template, memo |

Fixes made while building it:
- Slide sizes are compared only between template and memos: a guide is a document to read, its page
  is not the slide.
- The deal record was removed from step 0 (decision: onboarding does not touch records).
- Guide extraction: when a table cell continues onto a second line, a repeated overlap of 3+ words is
  kept once. Regression check on the three guides: exactly one line changed (Lumen's vermilion row,
  which now reads as printed); nothing else moved.

---

## Step 1 · House style

**Fills:** identity (memo name, stages), page (slide size, margins), palette (name, hex, **what each
colour is for**), fonts, text styles by role, formats (money, negatives, percentages, multiples, dates,
periods, counts, currency, per share; by context: prose, table, chart), tables, charts, citations,
footer, and conflicts between materials.

**Input package (only what exists):**
- guide: full text + page images. The model reads the **images first** (layout, colour swatches,
  emphasis) and uses the text to copy exact values and to quote.
- design file (the template, or else the first memo): slide size, theme colours and fonts, master
  default text styles, every text look in use with the boxes that use it, every colour really used
  (boxes outside the page are skipped: they are notes, not design).
- memos: numbers as they were written, only when there is no guide.
- Nothing from earlier steps: this is the first.

**Code checks:**
- hex codes valid; palette names unique;
- text roles use palette colours and positive sizes; `size_pt` lies between `min_size_pt` and
  `max_size_pt` when the materials give a range ("10 to 14 pt", "never below 9pt"); chart series use
  palette colours;
- every colour the design file uses is in the palette or in a conflict;
- citation scheme from a fixed set (`per_slide_letters`, `per_slide_numbered_notes`,
  `global_numbered_references`, `none`, `other`); every format has examples;
- every quote attributed to the guide exists in the guide (tolerant match);
- every hex and size attributed to the guide appears in the guide text (catches digits misread from an image).

**Review:** a style sheet: palette with uses, text roles with font and size, formats with examples,
table and chart conventions, citations, footer, and the conflicts with each source's version.

**Connects to the next steps:** step 2 uses the memo name and stages; step 3 uses the palette and text
roles to style layout boxes; step 4 uses the formats to recognise figures.

**Results** (gpt-5.5; about 2 minutes and 21–33k input tokens per firm, retries included)

| | Merrow | Corriemoor | Lumen |
|---|---|---|---|
| Memo name, stages | Investment Committee Memorandum; Screen, First IC, Final IC | Credit Approval Paper; Pre-screen, Credit Committee | Decision Note; Opportunity, Decision |
| Slide | 13.333 × 7.5 in | 10 × 7.5 in | 11.69 × 8.27 in, side margins 0.45 in |
| Palette | 13 colours with uses (incl. RAG) | guide palette + supporting tints | 6 colours with uses |
| Citations | per_slide_letters | per_slide_numbered_notes | global_numbered_references |
| Tables / charts | plum header, plum tint banding, stone borders, never shrink; series plum → gold → warm grey → mid plum → lilac, forecast lilac, no gridlines, labels 10pt | teal header, pale grey banding, min 9pt, split rather than shrink; forecast teal 60%, coral only for risk and breaches | bold header between 1.25pt rules, focus cells vermilion bold; vermilion only for the focus series, no legend for one series, no chart title |
| Conflicts for the person | 3: table text size (template 12pt vs guide 10pt), appendix divider footer colour (lilac vs warm grey), white not in the guide palette | 2: template colours not in the guide palette, table text size | 0 (one source) |
| Model calls | 2 (first: white attributed to the guide, rejected) | 2 (first: a size attributed to the guide that it does not state, rejected) | 2 (first: a quote copied with table pipes, rejected) |

What we learned:
- The retry with errors works: every run had one rejected answer that the second call fixed.
- Checking values attributed to the guide against its text exposed real conflicts (white in the
  Merrow template is not a guide colour; three bullet levels in the template vs "one level of
  sub-bullets" in the guide, seen in one run).
- Lumen's margins were missed until the instruction said "fill every field the materials state".
- Not caught, on purpose: Merrow's forecast headers are plum in the template and mid plum in the
  guide. Finding it would need a prompt written for that firm; it belongs to comparing layouts with
  the guide (step 3).
- The spec schema needs its own `tables` and `charts` parts (before they were added, the model put
  them under formats "other").

---

## Step 2 · Structure

**Fills:** the sections of the memo: name, status (`locked`, `mandatory`, `optional`, `free`), the
layouts each uses, how many times it appears (`one`, `any`, a number), order rules, what it must
contain, how instances are numbered; and, per stage, the sections it requires.

Order rules use a fixed vocabulary: `first`, `last`, `position:<n>`, `after:<section>`,
`before:<section>`, `immediately_after:<section>`, `immediately_before:<section>`, `after_all:<status>`.

Layouts are exact names from the design file. When a material names or describes a layout that no
design file contains, it is `to_generate:<name>`; step 3 generates it.

**Input package (only what exists):**
- guide: full text + page images (images first, text to quote).
- design file: every layout with its boxes; notes left outside the page (the template's own
  instructions, found by position, not by name).
- memos: each memo's slides in order (number, layout, title).
- step 1 (approved): memo name and stages.

**Code checks:** section names unique; status from the fixed set; every layout exists (or is
`to_generate`); count well formed; order rules well formed and naming existing sections; **every layout
the memos use belongs to some section**; stages are those approved in step 1 and name existing
sections; quotes exist in the guide.

**After the answer, code checks every past memo against the proposed order rules** and reports what
breaks them as possible accidents in that memo (not as model errors).

**Review:** the section list with status, layouts, order rules and what each must contain; the
stages; conflicts; the past memos checked against the structure.

**Connects to the next steps:** step 3 builds (imports or generates) the layouts the sections use;
step 4 writes the recipe section by section.

**Results** (one call each after the fix below; 26–75 s; 8–13k input tokens)

| | Merrow | Corriemoor | Lumen |
|---|---|---|---|
| Sections | 3 locked in order (`position:1..3`), 8 mandatory after all locked, appendix free and last (3 layouts) | cover and executive summary locked first; 10 mandatory with `Covenants after:Facility terms`; sign-off `after_all:mandatory` and `immediately_before:Appendix`; appendix free | Decision on a page locked first; annexes free (6 layouts + blank, numbered A1, A2…); Annex Z locked last |
| Layouts | all imported | all imported | 9, all `to_generate:<name from the guide>` |
| Stages | Screen, First IC, Final IC with their sections and notes | Pre-screen (1, 2, 3, 5, 6, 11 and sign-off) and Credit Committee | Opportunity and Decision |
| Conflicts | 1: the KPI tile layout has no separate context-line box, the guide asks for one | 1: Cairnvale slide 10 uses CC Blank instead of the Security and structure layout | 0 |
| Past memos vs structure | no findings | 3 findings, all from Cairnvale slide 10 (section missing; a "free" layout before sign-off) | no memos |

What we learned:
- The order vocabulary was enough for three different structures without special cases.
- Checking memos against the proposed structure found, with no firm-specific code, the same Cairnvale
  slide that the discovery found by hand.
- Fix: for a firm with no PowerPoint, the first run lost the layout names the guide lists (the check
  rejected them, the model replaced them with a bare `to_generate`). With `to_generate:<name>` the
  structure keeps all 9 layouts the guide names.
- Ambiguous, for the person: whether Valuation is required at Merrow's Screen stage (the guide lists
  the Screen sections and adds "returns show the base case only"; the model made it required).
- Engineering: results are now saved before the review is built, and `--review-only` rebuilds a review
  without a model call (a review bug once cost three model calls).

---

## Step 3 · Layouts

**Fills:** every layout the structure needs, as a list of boxes in the element model the canvas and the
PowerPoint export will share: type, frame (inches), role, style, lock, fixed text, prompt text.

**Two paths per layout, decided by step 2:**
- **Imported** (the layout exists in the design file): code copies its boxes exactly. The model only
  names the role of each box: text boxes get a role from the step 1 text roles (or `new:<name>` when
  none fits, for the person to decide); tables also get the text role of their cells; other boxes get
  a short descriptive role. An empty placeholder is typed by its placeholder type (table, chart,
  picture), not by its empty content.
- **To generate** (no design file has it): the model designs its boxes from the guide (images first)
  with the page, margins, palette and text roles approved in step 1.

**Locks are set by code:** fixed shapes and fixed text are locked by the firm; placeholders change
content but do not move.

**Code checks:** every layout the structure needs is present; every box has a role; text roles exist
in step 1 (or are proposed as new); for generated layouts: on the page and inside the margins, no
overlapping content boxes, valid types and locks, palette colours, quotes exist in the guide.

**Template vs guide, computed by code:** every imported text box (and every table, through the style of
its cells in the template's own sample slides) is compared with its role's style where that role comes
from the guide. A size outside the role's range or a different colour is a conflict for the person.

**Review:** every layout drawn as boxes labelled with their roles (`out/onboarding/<firm>/step_3_layouts/`;
orange = locked by the firm, dark = content only, green = free); new roles proposed; template vs guide
conflicts; open questions.

**Connects to the next steps:** step 4 writes the recipe box by box, using these boxes and roles.

**Results** (final run; one call per firm; 34–129 s)

| | Merrow | Corriemoor | Lumen |
|---|---|---|---|
| Layouts | 14 imported | 16 imported | 9 generated + master |
| New text roles proposed | 7 (tile labels, RAG key text, cover deal team, appendix contents…) | 4 (paper label, cover deal team, decision requested, approver block) | 0 |
| Template vs guide conflicts | 7: Deal terms and Valuation tables 11pt (guide 9–10pt); Market commentary 13pt and Vote requested 15pt (11–12pt); appendix divider contents, footer and slide number in lilac (guide ink / warm grey) | 6: cover footer 10pt and grey #9AA5AB (guide 8pt slate); risk matrix 8pt and teal (guide 9–10pt slate); recommendation label 8pt in #B7CFD4 | 0 (no template) |

**Every conflict found matches what the discovery reviewers found by reading the templates by hand.**

What we learned (three fixes, all generic):
1. **Roles must not be forced.** With only step 1's roles to choose from, boxes nobody described
   (RAG key labels at 9pt) were put in the closest role and produced false conflicts. `new:<name>` lets
   the model say none fits.
2. **Guides give ranges.** "Body 10 to 14 pt" stored as 14pt made every 10–11pt box a conflict. Step 1
   now stores `min_size_pt` / `max_size_pt` and a conflict is a size outside the range. (Step 1 was re-run
   for the three firms; step 2 did not change: it only uses the stage names, which stayed the same.)
3. **Tables are compared by their cells.** A table placeholder's own default size (14pt in one template)
   is not what its cells use; empty placeholders were also being read as text boxes. Tables now take
   the cell style of the template's sample slides and are typed by their placeholder type.

Lumen's generated layouts passed every geometry check on the first call; its open question is honest:
annex content positions are estimated from the guide images.

---

## Step 4 · Recipe

**Fills:** for every content box of every layout (text, tables and charts that are not fixed words), what
goes in it, as segments: `fixed` (with how many memos write it), `figure` and `fact` (a named concept),
`memo_data` (stage, date, team: set when a memo is created), `conditional` (words added when a deal fact
holds), `model_writes` (drafted per deal, with the concepts it must include and **style examples**: what
the past memos wrote there, kept so the draft follows the firm's way of writing), `person_writes`.
Tables are described as structure (header, columns, one row per concept, one real example cell per row).
Out of it comes the firm's **concept catalogue**: what the binding will connect to a deal record.

**Input package, built box by box by code:**
- step 3: each layout's boxes (id, role, type, the template's prompt text) and the fixed words around them;
- step 2: the section each layout serves and what it must contain;
- past memos: for every slide on that layout, the text of every box, **matched to the layout's box by
  its placeholder number** (a PowerPoint mechanism, so it works for any firm that builds on its layouts);
  content people added outside the layout's boxes is sent apart as "unmatched", and the model decides
  whether it belongs to a box, is deal-specific, or is an accident;
- guide: full text + page images.
- With no past memos (guide only) the same call works from the guide: boxes stay empty with their kind
  of fill, concepts and a description; fixed text only where the guide writes the words itself.

**Code checks:** every content box has a recipe; segment types from the fixed set; concepts in
snake_case; conditionals say when; **every example (figures, facts, table cells, style examples) exists
as written in that memo's box**. After the answer, code recounts "seen in N of M memos" for fixed text,
builds the concept catalogue, and lists concepts written on their own (not in table cells) that take
different values inside one memo.

**Review:** every box written as it would read, with chips (`"Source: " [document] …`), fixed text with
its ⟨n/m⟩ count, the concept catalogue, unmatched content with its decision, accidents, questions.

**Connects to the next steps:** step 5 states rules over these concepts and sections (for example, values
that must be identical across sections); the concept catalogue is what the binding uses when the firm's
first memo is created.

**Results** (final run)

| | Merrow | Corriemoor | Lumen |
|---|---|---|---|
| Boxes with a recipe | 106 | 114 | 43 |
| Concepts | 45 | 47 | 10 (ticket, price, entry, returns, fund fit, FX rate…) |
| Accidents found | the chart pasted as a picture (Fieldloom slide 6) | Cairnvale: minimum liquidity covenant £3,500k on slides 3 and 7 vs £3,000k on slide 8; chart pasted as a picture (slide 7); Security and structure built on CC Blank (slide 10) | — |
| Unmatched content | 16 (appendix tables and pictures, the hand-built competitive map) | 20 (appendix tables; Cairnvale slide 10 reassigned box by box to its section) | — |
| Calls · time · tokens in/out | 1 · 3 min · 24k/24k | 2 · 8 min · 92k/65k | 1 · 1 min · 10k/7k |

What we learned:
- **Matching boxes by placeholder number** made the package far smaller than sending whole memos
  (12–16k tokens instead of 50–65k in the earlier pilot) and every example can be checked in its exact box.
- **Model-written parts keep style examples.** The recommendation sentence is drafted per deal with the
  figures it must include and the past memos' sentences as examples of the firm's wording (decision 6 Nov:
  "model writes, with required figures and style examples", rather than a rigid fixed sentence).
- The hurdle phrase came out as a conditional on the base-case MOIC and IRR.
- **Linked values:** preventing them from drifting in new memos is deterministic (the same record datum
  feeds every box; step 5 states which values must match). Finding drift in old memos is only a hint:
  the model flags what it notices (it found Cairnvale's covenant) and code compares only figures written
  on their own, not table cells (cells of one table differ by design; comparing them gave false positives).
- Bug fixed: box texts were cut at 600 characters, so long tables reached the model truncated (the model
  itself reported it).
- Cost: Corriemoor's run is the heaviest so far (two calls; style examples make the answer long).

---

## Step 5 · Rules

**Fills:** what the materials say that is logic, not looks: what the platform must compute or watch in
every memo. Every rule is an instance of a type from a **fixed library**, with parameters:

| Type | Controls |
|---|---|
| `linked_values` | values that must be identical wherever they appear in these sections |
| `derived_value` | a value computed from other concepts (a formula, possibly by case) |
| `conditional_statement` | when a fact about the deal holds, a section must state something |
| `cardinality` | how many items a section or box holds (exactly, at least, at most) |
| `min_size_overflow` | the smallest allowed size and what to do when content does not fit |
| `colour_usage` | what a colour may be used for |
| `native_content` | charts and tables must be native; where pictures are allowed |
| `naming` | which form of a name goes in which sections |
| `numbering` | how instances and references are numbered, and what updates when order changes |
| `freshness` | how recent a datum must be |
| `required_content` *(added)* | a section must state these things |
| `prohibited_content` *(added)* | something must not appear, in a section or anywhere |
| `ordered_items` *(added)* | the items of a list appear in a fixed order |
| `location_restriction` *(added)* | a kind of value may appear only in these sections |

`cardinality` also takes `when_exceeded` *(added)*: where extra items go (a section) or "split".

What no type can express goes to **unmapped**, with its quote: candidate extensions of the library, and
entries for the configuration vs code report.

**Input package:** the library; the names approved so far (sections, concepts, colours, text and box
roles); **what earlier steps already cover** (formats, citations, footer, tables, charts, palette uses,
section order and numbering, stages), so it is not repeated; the guide (images first); the template's
notes outside the page.

**Code checks:** every rule's type is in the library and has its required parameters; every section,
concept, colour and role it names exists; cardinality bounds are whole numbers; every rule and unmapped
item has a quote; quotes exist in the guide.

**Review:** rules grouped by type, each in one plain line with its parameters; the unmapped list with what
the library lacks.

**Results** (final run; one call each, two for Lumen)

| | Merrow | Corriemoor | Lumen |
|---|---|---|---|
| Rules | 28: 13 cardinality (3 pillars, 6 tiles, 4 metric tiles, 3 cases…), 11 required statements incl. the hurdle, 3 naming, 1 freshness | 36: 4 linked values (covenants on 3 sections, grade on cover and summary, sources = uses, headroom), 3 derived (headroom formula, totals), 13 required statements incl. CC5 → monthly liquidity reporting, 6 cardinality (3 tiles, 4 approvers…), 5 colour uses, 2 naming, 1 numbering | 27: FX conversion and returns in deal currency, Annex Z content, 5 tiles, 8 Price and terms rows, ≤ 5 why, ≤ 4 risks, annex renumbering with cross-references, source numbering by first appearance, ECB rate of the last business day |
| Unmapped | 3: "nothing else" on the recommendation slide; overflow of pillars and tiles to the appendix; lead partner first | 2: approver boxes in fixed order; screenshot source files filed | 5: title is a statement not a label; no mixed currencies on a page; originals only on the transaction annex; state the effect of a 5% FX move; no FX forecast |

What we learned:
- **"Unmapped" only means something if the model knows what is already covered.** The first run, without
  that, put 32 / 18 / 15 items there, mostly formats, order and table styling handled by steps 1–2.
  Passing a compact summary of what is covered brought it to the real gaps (3 / 2 / 5).
- **Over-correction is a risk too:** marking "what each section must contain" as covered made the model drop
  checkable rules (exactly three pillars, four approvers). That prose is only prose: checkable parts of it
  must become rules. Rule of thumb for the real system: a step owns a requirement only if it stores it in
  a form code can check.
- **What the library lacks, across three firms**, is a short and telling list: a "required content" type
  (the model used `conditional_statement` with no condition for "must state"), prohibited content
  ("nothing else", "no FX forecast"), order within a list (approvers, deal team), where overflow goes,
  and restricting a kind of value to one place (original currencies). These are the candidate extensions.
- A misreading no check catches: one run read Corriemoor's "CC plus one digit … plus or minus" as "no plus
  or minus". Semantic errors in a rule are what the person's review is for.

### Library extension (second round)

The first 10 types were designed before seeing the firms. Across the three firms, the "unmapped" lists
showed the same missing shapes, so four generic types and one parameter were added (code: a few lines in
`LIBRARY`; nothing firm-specific):

| Added | Needed by (examples) |
|---|---|
| `required_content` | every firm's "must contain" (before, the model forced `conditional_statement` with no condition) |
| `prohibited_content` | Merrow's "nothing else" on the recommendation slide; Lumen's "no FX forecast"; Annex Z "nothing else" |
| `ordered_items` | Lumen's five tiles in order; Merrow's bear / base / bull and lead partner first |
| `location_restriction` | Merrow's custom slides only in the appendix; Lumen's original-currency amounts only on the transaction annex |
| `cardinality.when_exceeded` | Merrow: more than three pillars or six tiles → appendix |

**Results after the extension**

| | Merrow | Corriemoor | Lumen |
|---|---|---|---|
| New types used | 10 required, 3 prohibited, 4 ordered, 1 location; overflow to the appendix on pillars and tiles | 13 required, 1 ordered | 4 required, 2 prohibited, 1 ordered |
| Unmapped | 2: the hurdle stated as a threshold; the "Name (Role)" pattern for deal team lines | 3: approver order (the guide says "fixed" without listing it; the order is in the template); CC5 → monthly liquidity reporting (section unclear); screenshot source files filed (a workflow rule, not a memo rule) | 5, all pointing upstream: one FX rate (step 4 named no FX-rate concept); original currencies only on the transaction annex and the risk review on the risk annex (the guide names kinds of annex that step 2 treats as free and untyped) |

What this tells us:
- The extension is the intended growth pattern: firms reveal a missing shape, a small generic type is added,
  and the addition is recorded for the configuration vs code report.
- What remains unmapped is mostly **not** the library's fault: Lumen's gaps come from the structure (annexes
  have kinds the guide names) and the recipe (no FX-rate concept); Corriemoor's approver order lives in the
  template, which this step does not receive in full. Fixing those belongs to steps 2, 4 and 5's inputs.
- **Runs vary**: Corriemoor's CC5 rule was a rule in one run and unmapped in the next. Every step's output
  is a proposal for the person, not the truth.
