# Where it breaks

A living list of what the system gets wrong, found while building (the brief asks to show where the system
breaks and how often). Each item: what shows, the root cause, the layer to fix, impact, status. Fixes are
made in batches by impact, and only when generic.

Impact: **high** = a wrong or unformatted figure, wrong data, or a broken demo path; **medium** = visible
quality issue; **low** = cosmetic. Grey italic slots (text the agent or a person writes) are expected.

## Generator and PPTX painter (7 Nov, Tidewell memos for the three firms, rendered and reviewed visually)

| # | What shows | Root cause | Layer | Impact | Status |
|---|---|---|---|---|---|
| 1 | Generating took ~110 s, painting ~40 s (Merrow, Corriemoor) | One database round trip per row and per query, against a remote database | generator, painter | high | **fixed**: spec and memo read once, memo saved in one batch per table; 2-3 s to generate, 0.2 s to paint |
| 2 | Slide numbers were a person slot | Page number is memo data the slide position gives | generator | medium | **fixed** |
| 3 | Deal team repeated on every paragraph | The whole team was written in each paragraph | generator | medium | **fixed** |
| 11 | Unformatted figures ("22,940,000", "5,000,000") | When the concept has no value kind, the record's kind is not used | generator | high | **fixed**: £22.9m, £24.0m, -12% |
| 12 | Case tables filled only the first case (Valuation: Bear only) | Columns that are cases (Bear, Base, Bull) were not matched to the family's rows | generator | high | **fixed** |
| 13 | Lumen wrote GBP amounts with £ instead of EUR | The record has no FX rate (record gap) and nothing marked the conversion as missing | binding / generator | high | **fixed**: shows "[no data: GBP→EUR rate]" until the rate exists |
| 14 | Template sample rows stayed ("Guidance: workstream") and leftover rows appeared ("Additional source item") | Template rows were kept whether or not the recipe had them | writer (table template) | high | **fixed**: the recipe decides the rows, the template gives structure; rows that change per deal are one marked slot |
| 5 | A table row appeared twice ("Closing ARR" / "Closing ARRa") | Matching recipe rows to template rows was exact text (footnote letter) | writer (table template) | medium | **fixed**: a footnote letter after a capital is ignored when matching |
| 6 | Some period rows are empty (Merrow growth, ARR forecast) | The concept's binding is not a period family, or the record has no forecast for it | binding / record | medium | open |
| 15 | Chart axes unformatted ("100000000"); categories show paths; the financial chart shows FY26-FY31 while its table shows FY24-FY29 | Axis number format not set; non-period categories use the path; the chart ignores the column rule | painter / generator | medium | **partly fixed**: charts follow the period rule (FY24A-FY29F) and money axes take the firm's symbol and the scale their values need; axes stay unformatted when a series concept has no value kind (#23) |
| 8 | Cover descriptor overflows into stage and date (Merrow); Lumen headline overlaps the tiles | A long narrative bound to a short box; no fit check yet | binding / step 8 (fit) | medium | open |
| 16 | Record lists printed as JSON (Corriemoor security table) | A list value is written as its raw text | generator | medium | **fixed**: a list is written one item per line, an item with fields as its texts |
| 17 | Risk matrix cells show "[no data]" instead of placing risks by probability x severity | Matrix placement is not implemented (the record has probability and severity) | generator | medium | open |
| 18 | Lumen footer date is ISO, not DD.MM.YYYY | The painter writes the master's date without the firm's date format | painter | medium | **fixed**: the master's date uses the firm's date format (15.10.2026) |
| 19 | Lumen logo cut at the page edge | The generated master's logo box is drawn at the logo's own width | painter | low | open |
| 4 | Tenor shows "36" without "months" | Values of kind months have no format | generator / formats | medium | **fixed**: "28.1 months" |
| 9 | Footer source lines and footnotes show "[no data]" | Step 4 typed per-slide text (source line, footnotes, tile labels) as facts | onboarding step 4 | medium | open |
| 10 | Ownership "4%" | One format per kind and context; the firm writes ownership its own way | generator (format per concept) | medium | **not a bug**: the firm writes ownership with no decimals ("11%" in its memos) |
| 20 | Some facts show the wrong record value (Merrow anti-dilution and information rights show "Security") | The binding checks a path exists and has a compatible kind, not that it is the right datum | binding | high | **fixed**: list items are named by their first text (series_c_terms[Anti-dilution]); a whole list is accepted only for concepts that are a list (table rows, chart series); concepts the memos write as words are facts, decided from their examples |
| 21 | Money in term-sheet tables has no unit ("Financial covenants 5,000" instead of "£5,000k") | The table format assumes the unit is in the column header; a term sheet has no unit header | generator (format context) | medium | **fixed**: a table leaves the unit out only when a header carries it; a format that writes the unit in the header is used only there |
| 22 | Corriemoor tenor shows "12 months" (the deal is 36 months, 12 interest-only) | The binding picks a path that exists and has the right kind, not always the right datum | binding | high | open: needs the person's review of the binding (no check can prove the datum) |
| 23 | Some chart axes stay unformatted (Merrow market, Corriemoor financial) and long category labels are cut | The series concept has no value kind; small charts truncate labels | onboarding step 4 / painter | medium | open |

## Canvas and API (7 Nov, the memo screens compared with PowerPoint's render of the same memo)

| # | What shows | Root cause | Layer | Impact | Status |
|---|---|---|---|---|---|
| 24 | The PowerPoint export had no footer ("Strictly confidential") and no slide number on template slides | python-pptx does not copy a layout's footer, date and slide-number placeholders to a new slide, and the painter skipped elements whose placeholder was missing | painter | high | **fixed**: the placeholders the memo fills are cloned from the layout |
| 25 | A wide logo ran past its box (Lumen, right edge) | The picture took the box's height and kept its ratio, whatever the width | painter, canvas | medium | **fixed**: the logo fits inside its box in both |
| 26 | A second line in a placeholder (Merrow KPI detail line) is Georgia on the canvas, Arial in PowerPoint | The paragraph's stored font is the box's first-level font; the template's own level-2 font is not read | onboarding step 3 | low | open |
| 27 | Letter spacing of small caps labels is missing on the canvas | Character spacing is not in the element model | schema / canvas | low | open |
| 28 | After a save, the box stayed held for ~2.5 s | The door locked the memo's row first, then read the spec and the element one query at a time, rewrote the text row by row and read the element again (~23 round trips, the memo locked for ~15 of them); the socket released the box only after the save | door, real time | medium | **fixed** (8 Nov): one read, checks in memory, the spec's rules kept per version, a compare-and-set on the element's revision with the memo's sequence in one statement, the writes and the commit sent together (4 round trips, ~0.5 s); the box is released at once and the others see the saved text ~0.6 s later; eight edits on one box at once: 1 applied, 7 kept aside |
| 29 | Opening a memo took ~3 s | The memo and the spec's styles were read in ~33 queries against the remote database | memo read | medium | **fixed** (8 Nov): read as two statements, 0.7 s |
| 30 | Editing the cheque in the record (field eq.cheque) did not reach the memo's "Cheque size" | The record holds the same figure in two places; the binding chose the other one, and nothing showed which place a memo reads | binding / record | high | **fixed** (8 Nov): the record screen marks the values memos use and, on saving, offers the other places that held the same value; the binding review flags places that were equal in the uploaded record and differ now; a person can change where a figure comes from, and memos built before are offered the new figures |
| 31 | 7 record paths were shared by 20 values (cap table "Larkspur Ventures" twice; four sources-and-uses rows keyed "Use") | The reader named a table row by its first value, which repeats in some tables | record reader | high | **fixed** (8 Nov): a repeated first value gets the next value that tells the rows apart; a record that would still repeat a name is refused; the three firms' records loaded again. Corriemoor's use_items binding pointed at the repeated name and shows as broken in the binding review until a person picks its place |
| 32 | A figure that should be some rows of a table (only the uses of a sources-and-uses table) cannot be bound | A binding is one value, a whole column or a formula; it cannot say "the rows whose first value is Use". Corriemoor's use_items showed one use at random while the rows shared a name, and is broken since they have their own | binding | medium | open: a binding that filters a column by another column's value; until then a person binds one row or marks it missing |
| 33 | A newer deal record did not reach the boxes people add, nor figures inserted by hand into a firm's box | Updates rebuilt the memo from the firm's templates and compared box by box: a box not in a template was skipped, and figures in a firm's box were paired by order, so an inserted one left the box un-updated with a count warning | updates | high | fixed: each figure is worked out again the way it was made (its concept through the binding, else its record row; a person's chart from its family), written in the format that wrote it; the firm's tables and charts still follow its period rule. Fresh memos of every firm and stage: 0 changes against their own revision; the 60 existing memos: same changes as before; a person's table, chart and two hand-inserted figures: 6 changes found and applied (0 before) |
| 34 | A KPI tile's value wider than the tile (Merrow, Unit economics: "28.1 months") overflows onto its label | Nothing measures text in the firm's fonts yet | painter, canvas | medium | fixed: it fits in PowerPoint; the canvas drew it in two lines with insets the template does not have. Text is now measured in the firm's fonts with each box's insets as its template resolves them (kept on every box); the canvas, the PowerPoint and the pendings use the same measure. Against PowerPoint's own layout: 212 of 213 boxes agree on fits / does not fit over the three firms (the one disagreement says does not fit by 0.06 in) |
| 35 | A chart of the firm's template shows its onboarding descriptions as series names ("Primary unit-economics series"), mixes a count and a percentage on one axis, and keeps its title slot | The spec's chart template took the description as the series label rule and two series of different units | onboarding (spec) | medium | partly fixed: corrected for Merrow in spec v8 (series named from what they show: Software SAM, Payments SAM, Live sites; one unit per axis), and memos move to it with See changes → Move, nothing lost. Open at the cause: onboarding names a chart's series from its description, so a new firm can get the same; the onboarding step should name them from their binding |
| 36 | A table whose template defines no columns (a firm with no PowerPoint template) was not measured, and the canvas drew it empty | The measure and the canvas read the columns a table defines; the painter drew one | fit, canvas, painter | high | fixed: all three use the columns the table defines, else as many as its cells use. Found by measure 3 (PowerPoint ran the table past its frame, nothing said so) |
| 37 | A placeholder's bullets, indent and spacing inherited from the firm's template were not kept: the measure and the canvas laid text out wider than PowerPoint (one line short in a 12-line box) | Only what a paragraph set itself was stored; PowerPoint inherits the rest from the layout's and the master's list styles | onboarding (spec), fit, canvas | high | fixed: each placeholder's paragraph format by level is resolved as PowerPoint inherits it and kept on the boxes' paragraphs (every version and memo filled in). Found by measure 3 |
| 38 | The agent failed on any slide with a table ("Failed to fetch", a server error) | A helper in the agent's context took the name of the list of editable boxes, so the next box read it as a function | agent | high | fixed: the helper renamed; the context of every slide of a real memo read without error |
| 39 | With a box selected, the agent called every other box of the slide not editable | The selection limited what the agent could edit to that box | agent | medium | fixed: the selected box is what the request is about, not a lock on the rest of the slide |
| 40 | Asked for a chart on the slide in view, the agent put it on another free slide | Nothing told it to add boxes on the slide it works on | agent | low | fixed: it adds on the slide asked about, unless the person names another or it adds one in the same answer |
| 41 | "Add a customer concentration slide": the agent declined, though the record has the largest customer's share, ARR, sites and notice | The agent could only cite the firm's concepts; record values with no concept (167 of them) were invisible to it, and to the canvas | agent, figures | high | fixed: any single value of the record is a figure by its path ({{field:…}}), written in the firm's format and followed by updates; the agent sees them |
| 42 | The agent laid a table out as text (lines split by \|) on a blank layout with a text box as its title | It could not write a new slide's own boxes in the same answer, and nothing forbade a table typed as text | agent | medium | fixed: a new slide's boxes are written as it is added (in the firm's style); a table typed as text goes back to the model |
| 43 | A WebP picture was refused (400) though the screen let it be picked | Only the formats PowerPoint holds were accepted | pictures | low | fixed: any picture that can be read becomes a PNG; only what is not a picture is refused |
| 44 | A PDF export would have become what an edited PowerPoint is compared with | Bring back took the memo's latest export of any format | PowerPoint import, PDF | high | fixed before it shipped: only PowerPoint exports are compared with |
| 45 | Boxes people added were drawn at 18 pt in PowerPoint and 12 pt on the canvas | The painter wrote no size for a box with no role, so PowerPoint used its own default | painter | medium | fixed: the painter writes the size and font the canvas and the fit measure resolve. Found comparing the fit measure with PowerPoint |

## How to see an open issue again

From `backend/` (the spec, binding and record must be loaded; see backend/README.md):

```
.venv/Scripts/python -m app.services.generator.memo <firm> "<stage>" --date 2026-10-15 --team "A (lead); B"
.venv/Scripts/python -m app.services.painters.pptx <memo id printed above> <out.pptx>
```

Open the PPTX (or render it with PowerPoint) and look at:

| # | Firm · stage | Where |
|---|---|---|
| 22 | corriemoor · Credit Committee | slide 3 Facility terms, row "Tenor and repayment" (binding of the tenor concept) |
| 6 | merrow · Final IC | slide 7 Financial performance, rows "Growth" and "Closing ARR" forecast columns |
| 8 | merrow · Final IC; lumen · Decision | Merrow slide 1 (cover descriptor); Lumen slide 1 (headline over the tiles) |
| 9 | merrow · Final IC | any content slide: footer "[no data: source_line]" and "[no data: footnotes]" |
| 17 | corriemoor · Credit Committee | slide 11 Key credit risks, the 5 × 5 matrix |
| 15, 23 | merrow · Final IC; corriemoor · Credit Committee | Merrow slide 6 Market chart; Corriemoor slide 6 Financial summary chart |
| 19 | lumen · Decision | any slide, logo at the top right edge |

Binding issues (22) are also visible without painting: list a firm's bindings and the value each path
resolves to with `app.services.records.Record` (resolve each `bindings.path`).

## Record gaps (not system errors)

Approver names and capex (Corriemoor), competitors (Merrow), FX rate and fund fit (Lumen): the person
provides them or they stay as "no data" pendings.
