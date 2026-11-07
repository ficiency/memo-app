# Pilots

Exploratory scripts run before the build (6 Nov 2026), to learn what works before designing the app.
They are **not** app code: the app ports their ideas, prompts and checks. Outputs go to `out/`
(ignored by git). Run everything from this folder (`docs/pilots`). The OpenAI key is read from the repo's
`.env`.

| Pilot | Question it answered | Scripts | Results |
|---|---|---|---|
| Extraction | Can one reader turn any template, past memo or guide into data a model can read? | `extractors/` | below |
| Memos → recipe | What does a firm write in each box, and which numbers are figures? | `memo_patterns.py` | below |
| Recipe → record | Can the figures be connected to paths in the deal record? | `record_binding.py` | below |
| Memos only | What changes without guide or template? | `memo_patterns.py` | below |
| Guide only | Can a firm with only a guide get a structure and layouts? | `guide_layouts.py` | below |
| **Onboarding, steps 0–5** | The full onboarding on the three firms, one model call per step with checks and retries | `onboarding_pilot/` | [onboarding_pilot/README.md](onboarding_pilot/README.md) |
| **Memo preview** | Does the spec carry enough to paint a memo on the canvas and in PPTX? Gaps G1–G7 | `preview/` | [preview/README.md](preview/README.md) |

The first five are superseded by the onboarding pilot, which reuses their extractors and lessons; their
results are kept here because the design decisions rest on them.

## Extraction (`extractors/`)

- `python -m extractors.pptx_reader <file.pptx> [--out <dir>]`: page, theme, master, layouts (placeholders
  with their effective style), slides, assets. Inches and points; colours keep their theme slot.
- `python -m extractors.pptx_view <reader-output.json> [--out <file.txt>]`: compact view grouped by layout;
  table rows show a shared look `[...]` and cell exceptions `{...}`; boxes outside the page are flagged.
- `python -m extractors.pdf_reader <guide.pdf> [--out <dir>] [--dpi 150]`: text in reading order (columns
  found from vertical gaps), tables placed in that order, page images.

Results:
- One PPTX reader serves templates and past memos alike; it ran unchanged on Merrow, Corriemoor and Fieldloom.
- The full read of one template is ~42k tokens; the compact view grouped by layout is ~10k. The model reads
  the view, never the raw dump.
- Every slide of a PPTX names its layout, so grouping slides by layout is free and exact when layouts
  distinguish sections (true for Merrow and Corriemoor).
- Templates carry their own instructions: off-page "template guidance" boxes and placeholder prompt text.
  They are rules even when there is no guide.
- Table cell colours (RAG, header, banding) are extracted by code; interpreting them is the model's job.
- Guide PDFs: text in reading order and tables placed under their heading reach ~90%. The rest (colour
  swatches, cards with arrows, tables without rules) needs the page image. **The model always gets text and
  page image together**, and quote checks must be tolerant.
- Tables that come out with 2 rows or fewer are read again using text positions; continuation rows are
  merged by the key column; a repeated overlap of 3+ words between joined cell lines is removed (a
  regression run confirmed exactly one line changed).

## Memos → recipe (`memo_patterns.py`)

`python memo_patterns.py run <out-name> <memo.view.txt>... [--template <view>] [--guide <text>]`: one call
per firm with guide + template + 2 memos (~50–65k tokens in, ~2 minutes). The model returns, per section and
box, segments `fixed | figure | model_writes | person_writes`; code checks every quoted figure exists in
that slide, same concept same value within a memo, and every slide covered.

- It matched sections even when memos reorder slides, found the same accidents we found by hand (red text,
  a pasted chart picture, a table shrunk to 7pt, Cairnvale's £3,000k vs £3,500k covenant), and code verified
  every quoted figure.
- **The guide matters:** without it the model takes coincidences for firm phrases ("the bear case returns
  our capital" marked as fixed) and invents conditional phrases to explain differences. With it, titles are
  recognised as per-deal conclusions.
- With few memos some doubt always remains: the review shows "seen in N of M memos" and the person decides.
- Tables must be described as structure (rows, concept per row, one real example cell), not as joined text.
- A conditional phrase must refer to a fact of the deal (e.g. base IRR ≥ 20%); otherwise it is
  model-written prose.

## Recipe → record (`record_binding.py`)

One call per firm with the concept list and a compact index of the record (~10k tokens in, ~1–1.5 min);
code checks every returned path (`field:`, `table:`, `series:`, `narrative:`…) exists.

- Merrow: 62 concepts → 48 found, 9 derivable, 5 missing. Corriemoor: 51 → 39 found, 5 derivable, 7
  missing. Few real model errors; most "problems" were checker bugs, since fixed.
- **Memo data is not record data:** stage, memo and committee date, appendix letters and exhibit images are
  set when a memo is created. The recipe needs a "memo data" category.
- **Period concepts must be relative** ("prior actual year", "3 actual + 3 forecast years"), never a fixed
  year copied from an old memo.
- **Appendix content is per deal,** not part of the firm's recipe.
- Real record gaps go to the person (BIBLE, Open).
- Decision that followed: binding is **not** part of onboarding; it runs when a firm's first memo is created.

## Memos only (Merrow without guide or template, ~32k tokens in)

- Still matches sections, verifies figures and finds accidents, but marks more text as fixed (62 vs 56) and
  less as model-written (32 vs 43), and invents conditionals to explain differences (" to Sep-24" vs
  " to Dec-24" is a period figure, not a condition). More review work for the person; ask for the guide
  when it exists.

## Guide only (`guide_layouts.py`, Lumen)

`python guide_layouts.py run <name> <guide.view.txt> <page.png>...` then `check`: guide text + 2 page
images, ~8k tokens in, ~16k out, ~2.5 minutes. Code checks boxes on the page and inside margins, no
overlaps, no size below the minimum, palette colours, and every quote in the guide; then draws each layout.

- From the guide alone the model proposed the structure (Decision on a page locked first, free annexes,
  Annex Z last) and the 9 layouts the guide lists, 77 boxes, with no check failing.
- **Master positions read from the page images are close to the real ones:** against the vectors measured
  in the PDF, most elements are within 0.015 in, the logo within 0.09 in. The model followed the written rule
  (8pt footer) over the printed size (7.6pt).
- Weak spots: it added a box the guide does not ask for (flagged by the model itself), and marked every box
  "assumed", which blurs "I placed this box" with "I invented this box". The Decision page interior cannot be
  checked against anything real: the guide only describes it in words.

## Not piloted

Template only; a single memo; the binding inside the onboarding flow; a memo with data (preview with
Tidewell); charts and table structure as designed in `docs/SCHEMA_DESIGN.md`.
