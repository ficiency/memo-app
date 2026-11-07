# BIBLE — One memo, every firm

Single source of truth: the Soal brief (`Soal_Technical_Assessment_One_Memo_Every_Firm.pdf`). If something is
not here, it is not a requirement; where the brief is silent we decide and defend it (section 3).
Delivery: live demo, moved from Fri 6 Nov 2026 to the following week. Last updated: Fri 6 Nov 2026.

**Status legend** (rebuild only; only verified things count): ✅ ran and works (with what, stated) ·
🟡 built, not yet verified · ❌ not built · ⬜ a decision or deliverable, not code.

**Where we are.** `memo-app` is a from-scratch rebuild. The old build, `../memo-engine` (FastAPI +
Postgres/Supabase + React), is used for **ideas and decisions only, not code**. Design is done (schema,
onboarding piloted, evals defined); the build starts now (section 6).

| Document | What it holds |
|---|---|
| [SCHEMA_DESIGN.md](SCHEMA_DESIGN.md) | Firm spec, deal record, memo, operations, real time, AI, as DB tables; the review decisions |
| [EVALS.md](EVALS.md) | What we measure, with what cases, and which build step unlocks each |
| [pilots/README.md](pilots/README.md) | Every pilot: question, scripts, results (onboarding and preview have their own READMEs) |

Language: everything in the repo is in English; conversation is in Spanish.

---

## 1. The idea

The **record** holds the facts, the **firm's spec** holds the rules, and the **memo** holds what people did
with both. The canvas, the AI agent and PowerPoint are three ways to edit the same memo, and **every edit
goes through the same checks**. A new firm must take **configuration, not code**.

- **Merrow Vale** (growth equity): 16:9, £m.
- **Corriemoor Credit** (lender): 4:3, £k with parentheses.
- **Lumen Strand** (secondaries, the third firm, already arrived): A4, EUR, **guide only, no PPTX**.
- Deal: **Tidewell**. Its record changes during the build.

---

## 2. What the brief asks (checklist for the rebuild)

### The 6 capabilities
1. **The firm's template lives in the platform.**
   - ❌ Versioned config in the DB: layouts, placeholders, grid, locked/reorderable/free zones, brand,
     components (KPI tiles, tables, charts), number/date/currency formats.
   - ❌ Start from the PowerPoint master and past decks; a model drafts, **a person approves** (onboarding).
   - ❌ A deal stays on its template version until someone moves it; moving shows **what changed and what
     needs a person**.
   - 🚫 Out of scope (decision 6 Nov): building a template from scratch in the app, and editing a template
     in the app. The element model is shared by layouts and slides so template editing can come later.
2. **One memo, every output.**
   - ❌ In-app preview, **PDF** for committee, **editable PPTX** natively on the firm's layouts.
   - ❌ A **master generated** from the spec when the firm has none (Lumen).
   - ❌ Text **measured in the real fonts**; long content continues or splits by the firm's rules, and where
     it cannot fit **the system says so**.
3. **A live editor in the app.**
   - ❌ Template slides locked: people edit content, not layout.
   - ❌ One-off slides on a bounded canvas: images, charts, tables, text, moved on the firm's grid inside
     locked header, footer and sidebar, figures still bound to the record.
   - ❌ Slides and sections reorder by drag where the firm allows it.
   - ❌ Every edit is an **operation checked on the server**, shown to everyone in real time.
4. **Custom slides, three ways in.**
   - ❌ Upload a PPTX or PDF slide: native elements where possible, a **flagged** picture where not.
   - ❌ Build one on the canvas.
   - ❌ Ask the **agent**, which builds it from the firm's components with figures bound to the record.
   - ❌ **Promote** a one-off slide into the firm's template, with a person's approval.
5. **AI edits are operations.**
   - ❌ "Make the title shorter", "add a customer concentration slide", "update the growth rate" become
     checked operations, never edits to files.
   - ❌ A figure in text, a table and a chart **changes in all three**; the model **never introduces a number
     the record does not hold**.
6. **PowerPoint is an editor, not an exit.**
   - ❌ Exported file edited by a person comes back: free edits kept; a number typed over a record value is
     caught and refused; broken rules refused or flagged.
   - ❌ When the record changes and the memo is generated again, **people's edits survive** and conflicts are
     **shown, not lost**.

**Foundation:** ❌ record, firm config, memo and every change kept apart, **with history, in a real DB**.

### Where the difficulty lives (and our answer)
| The brief says | Our answer |
|---|---|
| The platform is the source of truth; a firm with no PPTX works just as well | Spec in the DB; generated master; Lumen is the test |
| Identity that survives export and import; titles, order, positions are not identity | Shape names `memo:<element id>@<revision>` |
| Decide what each editor may change and what goes back to the record | Section 3, "Editing" |
| Flexibility is a trap; the canvas lives inside the firm's rules | Zones, grid, roles, palette; firm locks |
| A canvas slide leaves as native shapes and comes back as the same elements | Element model mirrors PPTX; round trip eval |
| Two people on one slide while the record changes: what wins | One person per box; record changes only applied by a person, open boxes saved first |
| A model that edits will be confidently wrong | Same door, same checks; proposals only; record values only |

### What this must not be
- ✋ Not PowerPoint in a browser: the canvas is bounded to the zones a firm leaves free.
- ✋ Not template filling: filling placeholders once, with no way back in.
- ✋ Not "let the model edit the file".
- ✋ No pictures in place of native slides or charts.
- ✋ The data model is graded, not the polish of the UI.

### The demo
- ✅ Two check-ins done; Lumen has arrived. Feedback: firm config **as fields, not JSON**; the platform must be
  **reusable for many firms**.
- ⬜ Two of them edit the same memo at once, on the canvas and through the agent.
- ⬜ They edit a PPTX we exported; we bring it back live.
- ⬜ They change the deal record; we regenerate.
- ⬜ API keys: OpenAI (`.env`; model `gpt-5.5`, override with `OPENAI_MODEL`).
- ⬜ Stretch, **only if they raise it**: the memo as a Word document.

---

## 3. Decisions (current; detail in the linked documents)

**Onboarding** (piloted; [onboarding_pilot/README.md](pilots/onboarding_pilot/README.md))
- **Code extracts, the model interprets, code checks the model.** Steps 0–5 (extract, house style,
  structure, layouts, recipe, rules), each **one model call** with a package code builds, code validation and
  up to two retries with the errors. Not a tool-using agent.
- **Human review after every step**, always. Code retries what it can prove wrong; the person decides what has
  no provably right answer (conflicts between materials, assumed values).
- **No fixed precedence between sources.** When guide, template and memos clash, the system shows the evidence
  and **a person chooses**; the choice is stored with its source.
- **Materials:** at least one of guide, template or past memos; ask for **2–3 past memos at most**.
- **Accidents do not leak:** style comes from theme, master, layouts or guide; from memos only content.
- **Guide-only firms** get a draft template with empty boxes, each with its kind of fill and a description.
  The memo is never fully AI-generated.
- **Model-written parts keep style examples** from past memos.
- **Binding** (concept → record path) is not onboarding: it runs at a firm's first memo, once per firm and
  record schema, with review.
- **Fixes must be generic:** phrased in terms of materials and structure, never a firm's content.
- **In the app** the onboarding runs as a backend service when a firm uploads its materials. The pilot's logic
  (prompts, validations, cuts, extractors, call-check-retry cycle) is ported and reshaped as services; storage
  moves to DB tables. The three firms are its regression tests.
- **Review: one screen per step**, with an "approve step" at the end. The layouts step shows all layouts
  together; the person opens the ones to check. Reviews show sample slides painted by the real canvas, so what
  is approved is what will be seen.

**Spec and record** ([SCHEMA_DESIGN.md](SCHEMA_DESIGN.md))
- Firm config in **DB tables and columns** (check-in feedback). Only JSON left: operation payloads.
- A firm has several **specs named by the user**; editing one creates its next **version** (full copy,
  immutable once approved); a new spec is not a version of another.
- **Deal record per firm and deal**, versioned from the original upload.
- For now every firm uses the Tidewell record schema; a record with different field names only needs a new
  **binding** (concept → record path), not code.
- **Record screen:** load the Tidewell record (JSON upload) and view and edit it in the app (fields, tables,
  series, narrative). Every save or upload creates the next record revision (who, when); the previous ones stay.
  This is how the demo's "we change the deal record" happens.
- **Creating a memo:** choose the spec, the record revision and the stage (Screen, IC…); the stage decides
  which sections are mandatory. The firm's binding is made the first time, if it has none.
- Every memo is **pinned** to (spec version, record revision). A new version shows a notice, **See changes**
  (before → after, ⚠ where a person is needed) and **Apply / Ignore**. Applying saves open boxes first, keeps
  people's words, swaps the chips, never deletes content (what lost its layout or value becomes a pending).

**Editing** ([SCHEMA_DESIGN.md](SCHEMA_DESIGN.md) §2)
- **One element model** (type, frame, role, fill kind, firm lock, user lock 🔒, revision) for layouts and memo
  slides; the canvas (HTML for text and tables, SVG for shapes and charts) and the PPTX painter both read it.
- **Every edit is an operation checked on the server before it lands**, through one door (canvas, agent, PPTX
  import, updates): it is refused if it changes a record figure or breaks the firm's spec (locks, zones, grid,
  palette, roles, rules).
- **Record figures are never edited in a memo** (canvas, AI, PPTX): refused with a modal explaining the figure
  comes from the deal record. Firm-locked content: same.
- **Blocking checks vs pendings:** an edit wrong in itself is refused; an unfinished memo shows pendings that
  never block editing or exporting. A **final review** button checks everything and marks the memo "Reviewed".
- A person-typed number that is not a chip becomes a pending; "convert to chip" lets the person choose among
  matching record values; "It's fine" dismisses it.
- **Notifications bell** computed from state.
- **PPTX round trip:** free edits applied; figures refused; moved template box refused; added, deleted or
  pasted things and boxes changed in the app since the export kept aside, never lost.
- **Custom slides:** blank slide on the firm's blank layout with zones and grid; PPTX upload is the native
  path; a **PDF slide** gives native text and tables where the guide reader extracts them, a flagged picture
  for the rest; paste from PowerPoint to be tested; promote to template built last.
- **The agent builds slides from the firm's components** = the spec's layouts and their elements (tiles,
  tables, charts); no separate component concept.
- **Charts** are data, not pictures: series bound to record values; SVG on the canvas, native charts in PPTX.
- **Identity:** every exported shape is named `memo:<element id>@<revision>` and every slide
  `memo-slide:<slide id>`, so duplicated and reordered slides are recognised on import.
- **Overflow:** never shrink below the firm's minimum. A long table splits into a continuation slide of the
  same section; a list over its maximum moves where the rule says (e.g. appendix); text that cannot fit even at
  the minimum becomes a pending "does not fit". Never cut silently.

**Canvas: from the spec to the screen**
1. **The spec** (DB) holds layouts as lists of elements, each with its role and firm lock, plus zones, grid,
   palette, styles by role and sections with their order rules.
2. **Creating a memo:** the generator walks the sections and, through the operations `add_slide` and
   `add_element`, copies each layout's elements into the memo, then fills their text from the recipe and their
   figures as chips through the binding.
3. **The memo in the DB is the only truth.** The canvas loads and paints it (role → font, size and colour;
   palette name → hex; inches → pixels), sends every change as an operation and receives the others' changes
   over the websocket.

What the canvas allows (checked on the server):

| Case | Allowed |
|---|---|
| Element with firm lock **fixed** (logo, "Strictly confidential") | Nothing |
| Element with firm lock **content** (template boxes) | Edit its text; not move or resize |
| **Free** element | Move and resize inside the free zones, snapped to the grid |
| **Chip** (record figure) | Never typed over: modal |
| **Template slide** | No boxes added |
| **Free slide** | Boxes added only in free zones; header, footer and sidebar stay locked |
| **Reorder slides or sections** | Only where the section rules allow; a locked section does not move; a mandatory one is not deleted (missing = pending) |
| **Styles** | Only the firm's roles and palette colours |
| **User 🔒** | Set and removed by a person; while set, nobody moves that element |

**Fit and fonts**
- **The server decides what fits**, measuring text with the firm's real font files (stored per firm). The canvas
  is a faithful preview, not the authority, so canvas, PPTX and PDF agree on what fits.

**Outputs**
- **Exports read the memo in the DB**, never the screen's HTML (which lacks identity, roles, chips and locks, and
  carries screen-only state). Pressing Export first commits the box the person has open.
- **The PDF is converted from the exported PPTX**, so both are identical: LibreOffice on the server, tested early
  against the three firms and compared with PowerPoint's own PDF; fallback for the demo: PowerPoint locally.

**Users**
- **Mock auth:** mock users identified by email, no real login. **Each firm is an isolated account:** a user
  belongs to one firm and only sees that firm's specs, records and memos.

**Real time**
- **One person per box**, lock given by the server, freed on leaving the box or disconnecting (no idle timeout; decision 7 Nov).
- **Own websockets** (one room per memo): typing and dragging shown live (ephemeral), committed as checked
  operations. Catch-up after reconnection later; the operation sequence already supports it.

**AI**
- Always a **proposal**, one click to apply; sees only what the person may edit; uses only values the record
  holds; failures retried, never shown. First cases: shorten a title, rewrite a paragraph, "update the growth
  rate" (show another record value everywhere at once).

### Open
- ⬜ Lumen's Arial Narrow (tables) is not installed: get the font file or define a substitute.
- ⬜ Record gaps found by the binding pilot: risk probability and severity 1–5 (Corriemoor matrix), residual
  risk rating (Merrow), GBP→EUR rate and "fund fit" (Lumen): record data, human input or an approved calculation.
- ⬜ Paste from PowerPoint: what the browser receives (test before the canvas).
- ⬜ The blank layout when a template has none (use the master's zones).
- ⬜ **Before the canvas (React) connects** (noted 7 Nov; the services exist, their routes and one move do not):
  1. one shared "resolved memo" service (role → font, size, colour; palette name → hex; inches) used by both
     painters: today the resolution lives inside the PPTX painter, and a second copy in React would drift;
  2. `GET /memos/{id}` returning that resolved memo (slides, elements, paragraphs, chips, tables, charts);
  3. the door returning the element's new state with each result (what real time broadcasts);
  4. routes to generate a memo, export and import a PPTX (upload and download), and list kept-aside conflicts.

---

## 4. Architecture

- **Backend:** FastAPI (Python) — onboarding steps, generator, operations door, checks, painters, websockets.
- **Database:** Postgres on Supabase, migrations in the repo. **Files** (uploaded PPTX and PDF, images, logos,
  font files, exports) in **Supabase Storage**.
- **Hosting:** frontend on **Vercel**; backend (FastAPI with websockets) on **Railway**, because Vercel's
  serverless functions cannot keep websockets open. Deployed, so two people can edit from their own machines.
- **Frontend:** React — canvas (HTML for text and tables, SVG for shapes and charts, drag and resize with
  react-rnd or similar; pixel-canvas libraries like Konva/Fabric rejected because text editing there is poor),
  onboarding review, AI side panel.
- **Model:** OpenAI, one call per onboarding step; the agent proposes operations.
- **Generic engine:** a new firm is its spec plus, at most, one generic piece in a library (rules, components,
  formats, citations, numbering, layout generators). Checked by a grep for firm names (0) and the
  configuration vs code report; an invented fourth firm with a spec only, **only if time allows**.

---

## 5. What the pilots taught us

Full results in [pilots/README.md](pilots/README.md). The essentials:
- One PPTX reader serves templates and memos; the model reads a compact view (~10k tokens), never the raw dump.
  Guides are read from **page image and text together**.
- The **guide matters**: without it, coincidences pass for firm phrases. Periods must be relative; memo data is
  not record data.
- The onboarding steps 0–5 ran on the three firms **with the same code**; template vs guide conflicts found by
  code matched the discovery one for one; every first answer had errors code caught and retries fixed.
- Rule library grew from 10 to **14 generic types**: the intended growth pattern.
- **Runs vary**, so the person's review of each step is not optional.
- The preview from the spec found gaps **G1–G7** (styles inside a box, background, table style, table
  structure, abstract tables, charts, master content) and four painter gaps. They are requirements of the
  schema and are fixed when the onboarding is ported.
- **G8, found when real figures were first written (7 Nov):** number and date formats were sentences, so
  nothing could write "£22.9m". The preview never wrote a figure, so it never showed. Fixed: formats are
  fields (scales with thresholds, decimals, symbol, unit inline or in the header, negatives, separators,
  date pattern, applies_to), and code checks that they write every example of the firm back exactly. One
  formatter (`app/services/formats.py`) serves the check and the generator.
- Lesson: a gap only shows when something **uses** the data. Two firm-specific-looking problems had one
  generic root: a requirement held by two steps gets lost between them; one owner per requirement
  (step 1 appearance, step 5 logic) fixed it.

---

## 6. Build order

**Order rule:** first, unlock the brief's evals as early as possible and pass them well; each eval runs as soon
as what it needs exists, and from then on on every change. **A step closes when its eval runs on the three firms
and its failures are fixed or listed with their cause** ([EVALS.md](EVALS.md)). Once the evals are out,
optimise what is visible in the demo.

**Always:** every step leaves the infrastructure ready for the next expansions, whatever the feature or part of
the system. Thin first, but not throwaway: what is expensive to change (schema, element model, operations door,
identity, realtime interface, eval runner) is built in full shape from the start; what is cheap (buttons,
operation types, library entries) grows.

| # | Step | Unlocks | Status |
|---|---|---|---|
| 0 | Pilots, schema design, evals design | — | ✅ |
| 1 | Skeleton (FastAPI, React, Supabase, migrations), full schema, the three firms' pilot specs loaded, eval runner | Schema fits the three firms with no special cases | ✅ every pilot item in the tables for the 3 firms; 0 firm names in code (`evals/reports/`) |
| 2 | Onboarding port with G1–G7 fixes, run on the three firms, written to the spec tables (moved up on 6 Nov: the generator and painters are built on the complete spec, not on the pilot's gaps) | Spec completeness (G1–G7); configuration vs code | ✅ steps 1–5 fixed and run on the 3 firms (~$12 of model); written as spec v2, then v3 with formats as fields (G8); every item lands in the tables; completeness G1–G7 measured (`evals/reports/2026-11-07_1309.md`). Writer to the DB; review screens come in step 10 |
| 3 | Spec → memo through operations; PPTX painter | Time and cost of a render; past memo rebuilt | ✅ generator and native PPTX painter on the 3 firms; render ~3 s, $0 of model. Past-memo rebuild not measured yet. Open issues in `evals/WHERE_IT_BREAKS.md` |
| 4 | Operations door; PPTX export and import | Round trip; edits survive; PowerPoint cases | ✅ the brief's PowerPoint edits in 5 round trips on the 3 firms: 100% as the firm's rules expect, 0 lost, 100% of untouched elements identical, identity kept (`evals/reports/2026-11-07_1530_powerpoint.md`) |
| 5 | Real time (box lock, live draft) with two scripted clients; canvas UI starts here | Two editors in repeated rounds | ❌ |
| 6 | Record screen (upload, view, edit → new revision); See changes → Apply / Ignore; bell | Record change during the rounds | ❌ |
| 7 | AI agent | AI edits never invalid; invented figure refused | ❌ |
| 8 | Real-font measuring and overflow | Long-content set | ❌ |
| 9 | Canvas toolbox, blank slide, grid and zones, PPTX upload | Remaining canvas cases | ❌ |
| 10 | PDF, final review, onboarding review screen, promote a slide | Final report | ❌ |
| 11 | Demo rehearsal: two editors, PPTX live, record change | — | ❌ |

**If time runs short,** cut from the end: step 10 (except the report), then parts of 9 and 8. **Never** cut
3–6 or the evals.

---

## 7. Evals

Defined in [EVALS.md](EVALS.md): the brief's suite (drop an image, drag into a locked zone, duplicate a
slide, type over a figure, paste a chart as a picture) on the canvas, through the agent and in PowerPoint, in
repeated rounds with two people; seven measures per firm; and **where the system breaks and how often**. The
report is generated by `evals/run.py`; results go here once the runner exists.
