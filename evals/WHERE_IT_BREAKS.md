# Where it breaks

A living list of what the system gets wrong, found while building (the brief asks to show where the system
breaks and how often). Each item: where it shows, the root cause, the layer to fix, impact, status.
Fixes are made in batches by impact, and only when generic.

Impact: **high** = a wrong figure or a broken demo path; **medium** = visible quality issue; **low** = cosmetic.

## Generator (first run, 7 Nov, Tidewell memos for the three firms)

| # | What shows | Root cause | Layer | Impact | Status |
|---|---|---|---|---|---|
| 1 | Generating a memo takes ~110 s (Merrow, Corriemoor) | One database round trip per row and per element query, against a remote database | generator | high (regenerate live in the demo) | open |
| 2 | Slide numbers show as a person slot | Page number is memo data computed from the slide position; the generator does not compute it | generator | medium | open |
| 3 | The deal team is repeated on every paragraph of the cover box | The recipe puts the team in each paragraph; the generator writes the whole team each time instead of one member per paragraph | generator | medium | open |
| 4 | Tenor shows "36" without "months" | Values of kind months have no format | generator / formats | medium | open |
| 5 | A table row appears twice ("Closing ARR" and "Closing ARRa") | The template's label carries a footnote letter; matching recipe rows to template rows is exact text | writer (table template) | medium | open |
| 6 | Some period rows are empty (Merrow growth; ARR forecast) | The concept's binding is not a period family, or the record has no forecast for it | binding / record | medium | open |
| 7 | Abstract leftover rows ("Additional financial rows") | A recipe row the template does not have is appended as a row | writer (table template) | low | open |
| 8 | Cover descriptor is a long paragraph | The descriptor concept is bound to the company description narrative | binding / recipe | medium | open |
| 9 | Footer source lines and footnotes show "[no data]" | Step 4 typed per-slide text (source line, footnotes, tile labels) as facts, so they wait for a record value | onboarding step 4 | medium | open |
| 10 | Ownership shows "4%" | One format per kind and context; the firm writes ownership with its own format | generator (format per concept) | medium | open |

## Record gaps (not system errors)

Approver names and capex (Corriemoor), competitors (Merrow), FX rate and fund fit (Lumen): the person
provides them or they stay as "no data" pendings.
