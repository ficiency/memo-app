# Where it breaks

A living list of what the system gets wrong, found while building (the brief asks to show where the system
breaks and how often). Each item: what shows, the root cause, the layer to fix, impact, status. Fixes are
made in batches by impact, and only when generic.

Impact: **high** = a wrong or unformatted figure, wrong data, or a broken demo path; **medium** = visible
quality issue; **low** = cosmetic. Grey italic slots (text the agent or a person writes) are expected.

## Generator and PPTX painter (7 Nov, Tidewell memos for the three firms, rendered and reviewed visually)

| # | What shows | Root cause | Layer | Impact | Status |
|---|---|---|---|---|---|
| 1 | Generating takes ~110 s, painting ~40 s (Merrow, Corriemoor) | One database round trip per row and per query, against a remote database | generator, painter | high | open |
| 2 | Slide numbers were a person slot | Page number is memo data the slide position gives | generator | medium | **fixed** |
| 3 | Deal team repeated on every paragraph | The whole team was written in each paragraph | generator | medium | **fixed** |
| 11 | Unformatted figures ("22,940,000", "5,000,000") | When the concept has no value kind, the record's kind is not used | generator | high | open |
| 12 | Case tables fill only the first case (Valuation: Bear only) | Columns that are cases (Bear, Base, Bull) are not matched to the family's rows | generator | high | open |
| 13 | Lumen writes GBP amounts with £ instead of EUR | The record has no FX rate (record gap) and nothing marks the conversion as missing | binding / generator | high | open |
| 14 | Template sample rows stay ("Guidance: workstream") and leftover rows appear ("Additional source item") | Rows whose labels are placeholders, and recipe rows the template lacks, are written as rows | writer (table template) | high | open |
| 5 | A table row appears twice ("Closing ARR" / "Closing ARRa") | Matching recipe rows to template rows is exact text (footnote letter) | writer (table template) | medium | open |
| 6 | Some period rows are empty (Merrow growth, ARR forecast) | The concept's binding is not a period family, or the record has no forecast for it | binding / record | medium | open |
| 15 | Chart axes unformatted ("100000000"); categories show paths; the financial chart shows FY26-FY31 while its table shows FY24-FY29 | Axis number format not set; non-period categories use the path; the chart ignores the column rule | painter / generator | medium | open |
| 8 | Cover descriptor overflows into stage and date (Merrow); Lumen headline overlaps the tiles | A long narrative bound to a short box; no fit check yet | binding / step 8 (fit) | medium | open |
| 16 | Record lists printed as JSON (Corriemoor security table) | A list value is written as its raw text | generator | medium | open |
| 17 | Risk matrix cells show "[no data]" instead of placing risks by probability x severity | Matrix placement is not implemented (the record has probability and severity) | generator | medium | open |
| 18 | Lumen footer date is ISO, not DD.MM.YYYY | The painter writes the master's date without the firm's date format | painter | medium | open |
| 19 | Lumen logo cut at the page edge | The generated master's logo box is drawn at the logo's own width | painter | low | open |
| 4 | Tenor shows "36" without "months" | Values of kind months have no format | generator / formats | medium | open |
| 9 | Footer source lines and footnotes show "[no data]" | Step 4 typed per-slide text (source line, footnotes, tile labels) as facts | onboarding step 4 | medium | open |
| 10 | Ownership "4%" | One format per kind and context; the firm writes ownership its own way | generator (format per concept) | medium | open |
| 7 | (merged into 14) | | | | |

## Record gaps (not system errors)

Approver names and capex (Corriemoor), competitors (Merrow), FX rate and fund fit (Lumen): the person
provides them or they stay as "no data" pendings.
