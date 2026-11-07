# Memo preview from the firm spec

Question: does the spec the onboarding produces (steps 1–4) carry enough to build a memo, on the canvas
and as a PowerPoint export, without opening the firm's own PowerPoint file?

```
spec (steps 1–4) ──► new memo: slides and resolved elements (elements.py)
                         ├──► HTML painter  (stand-in for the canvas)      memo.html
                         └──► PPTX painter  (the export, native shapes)    memo.pptx
```

Both painters read the same resolved memo; neither reads the other. A new memo has one slide per
layout the structure uses, in section order. With no deal record, figures show as chips (`[concept]`),
drafted parts as `‹model: …›`, person parts as `‹person: …›`. Every PPTX shape is named `spec:<element id>`.

Run (from `docs/pilots`): `python -m preview.run <firm>`, then `python -m preview.compare <firm>`
(review by code + side-by-side contact sheets). Outputs: `out/onboarding/<firm>/preview/`.

## How it was reviewed

- **By code** (`compare.py`, no firm names): every imported element against the box it came from in the
  design file (position, size, font, size, colour, fill, several text styles inside one box), and every
  element of every firm for content gaps (no recipe, master with no content, abstract tables, charts with
  no data model, likely overflow). 181 Merrow and 144 Corriemoor elements compared.
- **Visually**: all 39 slides, as pairs (the design file's sample slide next to the preview), 11 sheets.
  Lumen has no design file, so its 9 slides were reviewed alone.

## What works

- HTML and PPTX agree with each other (the two-painter "mirror").
- Imported boxes keep their position, size, font, size and colour exactly (0 differences in 325
  elements). This proves spec → memo → painters loses nothing; it does not prove the spec is complete,
  because step 3 copied those boxes from the template.
- **Lumen's master and pages were built entirely from its guide**: tab, label, logo, rules and footer
  are identical on all 9 pages; the Decision page follows the guide's anatomy.

## Gaps, and where each is fixed

All generic; none is specific to the three firms.

### In the spec (the steps must produce it; the schema must have a place for it)

| # | Gap | Seen in | Fixed in |
|---|---|---|---|
| G1 | **Several text styles inside one box**: a value and a smaller line under it (KPI and metric tiles), a label and a value (executive summary tiles), a role, a name, a decision and signature lines (approver boxes), bullets by level. The spec keeps one style per box. Also a bug: line breaks in memo text were turned into " / " before the recipe, so the recipe learnt "/" as text. | Merrow 12 boxes, Corriemoor 21 | Step 3 (keep per-paragraph and per-level styles; the extraction already has them) + step 4 (keep line breaks) |
| G2 | **Layout background lost**: Merrow's ivory cover and plum appendix divider render white; the divider's white logo and title disappear. | Merrow 2 slides | Step 3 (store the layout background; the extraction already has it) |
| G3 | **Table style stored as prose** ("plum header with white bold text"): painters cannot apply it; every table falls back to PowerPoint's default blue. | All three firms | Step 1 (table style as fields: header fill and text colour, banding, rule colours and widths, sizes, as palette names and numbers) |
| G4 | **Template table structure lost**: column widths (all columns come out equal), group rows inside a table (sub-headers in a covenant table), cell fills that carry meaning (risk matrix zones, RAG cells), fixed row and column labels of a matrix. | Merrow and Corriemoor tables; Corriemoor risk matrix | Step 3 (import a table's structure from the template's sample table) + step 4 (which cells are fixed, which are data) + step 5 (rules that colour cells, e.g. matrix zones) |
| G5 | **Abstract table recipes**: some recipes describe categories instead of rows ("Revenue or ARR metrics") or leave period columns unstated ("periods"). | Corriemoor financial summary, downside, key facts | Step 4 (concrete rows; a column rule such as "3 actual + 2 forecast periods" the generator expands) |
| G6 | **Charts have no data model**: chart boxes only describe what to build. | 7 chart boxes across the three firms | Step 4 (chart type, category rule, series concepts) + step 1 (series colours, already captured) |
| G7 | **A generated master has no content**: Lumen's footer boxes show their descriptions on every page. | Lumen, 3 boxes × 9 pages | Step 4 (give master boxes a recipe; the footer composition from step 1 fills it) |

### In the painters (not the spec)

| # | Gap | Fix |
|---|---|---|
| P1 | Picture placeholders filled with the firm's logo (a fallback meant for generated logo boxes) | Only fall back to the logo for firm-locked image boxes |
| P2 | Text boxes lose their border (Corriemoor approver boxes have an outline) | Draw the line of any element that has one, not only shapes |
| P3 | PowerPoint shapes get a default shadow | Remove the inherited shadow |
| P4 | Table rows stretch to fill the box | Use each row's own height |

### Not gaps (artefacts of a preview without data)

- "Likely overflow" (27 boxes): chips and drafted placeholders carry long names and descriptions; real
  values are short. Overflow must be measured again once the memo holds data.
- Drafted text is cut at 70 characters in the preview on purpose.

### Limits of the code review, now known

The first version of `compare.py` did not check layout backgrounds, table structure or borders; G2, G4
and P2 were found visually. A check for each belongs in the review the real system runs on every
generated memo.
