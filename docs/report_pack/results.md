# Results: what was tested, how, and what was not

All numbers come from the final runs on 9 Nov 2026, on the code as it is, on the three firms (Merrow: 16:9
template; Corriemoor: 4:3 template; Lumen: guide only, no template) and one deal (Tidewell). Each eval writes a
report in `evals/reports/` (named below). `decisions.md` explains the system; `where_it_breaks.md` lists every
failure found while building it.

## How the evals work

- **They act the way people do.** Each starts its own server and uses the same API the screens call, websockets
  as the browser canvas connects, and real PowerPoint files. The database is only read, to observe results.
- **What each edit should do comes from the firm's rules** (which sections are locked, allowed once or several
  times, where the free areas are), never from the firm's name, so the same suite runs on every firm.
- **PowerPoint itself is the judge** where layout matters (text overflow, rebuilding past memos): the files are
  opened in Microsoft PowerPoint and read through its automation interface.
- **Every case is checked in what the memo then is** (the memo as the API returns it, its pendings, the
  PowerPoint it exports), not only in what the server answered.
- **The suites catch failures**: in the canvas suite, two faults put in on purpose (pictures exported 0.1 in off
  their place; figures allowed to be typed over) turned 100% into 60% and flagged both; the faults were removed.
- Each memo the evals make is temporary and deleted afterwards; a record revision made by an eval is deleted too.

## The brief's suite: five edits in three editors

The brief names five edits: drop an image, drag into a locked zone, duplicate a slide, type over a figure, paste a
chart as a picture. Each is expected to be applied, refused or kept aside as the firm's rules say.

Two PowerPoint suites: one edits the exported file with a script, over five round trips (`*_powerpoint.md`); the
other has **PowerPoint desktop itself** open the file, make the edits and save it, through its automation interface
(`*_powerpoint_desktop.md`), so what comes back is a file PowerPoint wrote.

| Edit | Canvas (`*_canvas.md`) | Agent (`*_agent.md`) | PowerPoint (both suites) |
|---|---|---|---|
| Drop an image | On a free slide: placed, flagged as a pending, its bytes served back. On a template slide: refused, nothing added | "Add this image": no image placed (the agent receives no files) | On a template slide: kept aside as a conflict. On a free slide (desktop suite): applied, a picture to confirm |
| Drag into a locked zone | A box dragged into the header: refused, box unchanged. A template box dragged: refused. Control: a move inside the free area applied, snapped to the grid | "Move this box into the header": declined | A box moved onto a locked area: refused |
| Duplicate a slide | A section allowed several times: applied, copy with the same words and new identities. Allowed once: refused | Allowed once: declined. Allowed several times: applied | Allowed once: refused. A free slide (desktop suite): applied |
| Type over a figure | In text: refused, text unchanged. In a table cell: refused. Control: words written around a figure applied, figure unchanged | "Change … to 999": declined, told to change the record | Typed over in the file: refused, figure unchanged |
| Paste a chart as a picture | Placed but never taken as a chart: a pending until someone confirms it | not applicable | Kept aside |

| Result | Merrow | Corriemoor | Lumen |
|---|---|---|---|
| Canvas: edits and controls as the firm's rules say | 12/12 | 12/12 | 13/13 |
| PowerPoint (script): edits as the firm's rules say (5 round trips) | 30/30 | 30/30 | 20/20 |
| PowerPoint desktop: edits and checks as expected (made and saved by PowerPoint) | 15/15 | 15/15 | 14/14 |
| Agent: the brief's edits as the firm's rules say | 6/6 | 6/6 | 6/6 |

**Not covered by this suite:**
- In PowerPoint, Lumen: pasting a chart as a picture (its stage's memo has no native chart).
- The agent cannot place pictures by design.
- PowerPoint desktop is driven through its automation interface: the program opens, edits and saves the file
  itself, but no person clicks in it.

## Measure 1: edits that survive, conflicts surfaced rather than lost

Report: `2026-11-09_0832_two_people.md`. **Five rounds per firm**; each round:
1. Two people (A and B) connected to the same memo over websockets: both edit different boxes at the same moment;
   both reach for the same box at the same moment; one writes into a box the other holds; A edits from an older
   view; A types over a figure.
2. A asks the agent to fill a slide (or, when nothing is left to fill, to make a slide more concise) and applies its
   proposal.
3. In the middle round, B changes a value of the deal record and A applies the newer record.
4. The memo is exported to PowerPoint; B edits box X in the app after the export; the file edits X and an untouched
   box Y; the file is uploaded back.
5. The memo is exported again and every earlier edit (people's and the agent's) is checked to be there.

| Result | Merrow | Corriemoor | Lumen |
|---|---|---|---|
| All actions as expected | 195/195 | 185/185 | 110/110 |
| Applied edits present after each round | 123/123 | 113/113 | 38/38 |
| Same box at once: exactly one gets it, the other is told | 5/5 | 5/5 | 5/5 |
| Edit from an older view kept aside, with its conflict | 5/5 | 5/5 | 5/5 |
| PowerPoint edit of a box changed in the app since the export: kept aside | 5/5 | 5/5 | 5/5 |
| Agent proposal applied, none of its changes lost | 5/5 | 5/5 | 5/5 |
| Newer record applied; the figure follows it | 1/1 | 1/1 | 1/1 |
| Every kept-aside edit has a conflict | 10/10 | 10/10 | 10/10 |
| Changes lost (found with no operation) | 0 | 0 | 0 |

The PowerPoint suite (`2026-11-09_0835_powerpoint.md`, 5 round trips per firm, one person) adds: applied edits in
the next export 5/5 per firm, figures unchanged after typing over 5/5, kept-aside edits all with an open conflict,
0 lost.

The PowerPoint desktop suite (`2026-11-09_1007_powerpoint_desktop.md`) has PowerPoint itself save the edited file:
the boxes nobody edited are unchanged after PowerPoint saved it (170/170 Merrow, 132/132 Corriemoor, 34/34 Lumen)
and every shape keeps its identity (100/100, 107/107, 37/37). It found one failure, fixed: a picture PowerPoint saved
without an embedded image made the whole file be refused (`where_it_breaks.md` #46).

**Not covered:** more than two people at once; a person disconnecting and reconnecting mid-edit (the memo reloads;
missed messages are not replayed); network failures; a person clicking in PowerPoint (PowerPoint is driven through its
automation interface; the rounds edit the file with a script).

## Measure 2: how much of a canvas slide survives PowerPoint and back

Report: `2026-11-09_0840_canvas.md`. A free slide built through the canvas (text with a figure in it, a table from
the record, a chart from the record, two pictures), exported through the API, uploaded back unchanged, then each
element compared field by field: same identity, kind, place (within 0.02 in), content (text and figure kinds;
cells; chart points; the picture's bytes) and style. Then a person moves a picture and rewrites the text in the file
and uploads it: both applied, the figure still a figure.

| Result | Merrow | Corriemoor | Lumen |
|---|---|---|---|
| Canvas slide elements back as the same element | 5/5 | 5/5 | 6/6 |
| Edits made in the file on the canvas slide applied | yes | yes | yes |

The PowerPoint suite measures the whole memo: untouched elements identical after the round trip 460/460 (Merrow),
490/490 (Corriemoor), 145/145 (Lumen); element identity kept 475/475, 505/505, 160/160.

**Not covered:** shapes and lines drawn on a canvas slide (people add text, tables, charts and pictures only);
a slide uploaded from another PowerPoint going out and back (checked once: 0 changes, not in a suite).

## Measure 3: overflow on a long-content set we wrote

Report: `2026-11-09_0624_overflow.md`. Into every box people and the model write (titles, commentary, labels, one
cell per table), text of six fixed lengths (one short sentence to eight sentences), the same words for every firm,
written over the websocket. Then a **ladder**: boxes whose role may get smaller written at 45 to 180 words, each step
exported. PowerPoint judges every box: text past the box, size drawn, all words present.

| Outcome | Merrow | Corriemoor | Lumen |
|---|---|---|---|
| Cases (boxes written + ladder steps) | 31 + 36 | 46 + 18 | 10 + 36 |
| Fits at its own size | 29 | 47 | 25 |
| Shrunk to fit (never below the firm's minimum) | 6 | 1 | 4 |
| Reported as not fitting (pending + marked on the canvas; PowerPoint confirms) | 32 | 16 | 17 |
| **Cut silently** | **0** | **0** | **0** |
| Continued on another slide | not built | not built | not built |
| The system's measure and PowerPoint agree on fits / does not fit, every box of the memo | 91/92 | 97/98 | 20/20 |

Example of the ladder (Merrow, a body box, 12 pt, minimum 11): 45–75 words fit; 90 words shrink to 11.5 pt; 105 to
11 pt; from 120 words, reported.

**Not covered:** continuing or splitting long content onto another slide (not built: what does not fit is reported
for a person to shorten); text in languages or scripts other than English; Lumen's table font (Arial Narrow) is not
installed on this machine, so its tables are measured with a stand-in and flagged as approximate.

## Measure 4: AI edits that leave the memo invalid or its figures inconsistent

Report: `2026-11-09_0902_agent.md`. Per firm, on a new memo: the brief's edits
asked of the agent in words (part A); writing requests applied (fill a slide, write a title, shorten a slide; part
B); custom slides (a customer concentration slide, a chart added to it, a table asked for on a template slide; part
D); then code checks the whole memo: amounts typed instead of figures, figures that do not hold the record's value,
one figure showing two values, edits refused when applied; and the memo through PowerPoint and back.

| Result | Merrow | Corriemoor | Lumen |
|---|---|---|---|
| A · the brief's edits, done or declined as the firm's rules say | 6/6 | 6/6 | 6/6 |
| B · writing requests / agent edits applied | 5 / 25 | 5 / 26 | 5 / 32 |
| D · custom slides with a record table or chart (and a table on a template slide declined) | 3/3 | 3/3 | 3/3 |
| Edits refused when applied | 0 | 0 | 0 |
| Amounts typed instead of figures | 0 | 0 | 0 |
| Figures that do not hold the record's value (figures checked) | 0 (122) | 0 (83) | 0 (58) |
| One figure showing two values | 0 | 0 | 0 |
| A figure the firm's recipe expects removed (warned, left pending) | 0 | 1 | 0 |
| Changes found after PowerPoint and back | 0 | 0 | 0 |
| Model calls · cost · time | 15 · $1.19 · 259 s | 14 · $1.37 · 324 s | 17 · $1.51 · 452 s |

In the rounds of measure 1 the agent also edited in every round alongside two people: 15 proposals applied, none of
their changes lost.

**Not covered:** the agent acting on several slides in one request; the agent changing or removing people's own
tables and charts (done on the canvas); conversations longer than one request at a time.

## Measure 5: how closely a past memo can be rebuilt

Report: `2026-11-09_0709_rebuild.md`. The firms gave four past memos as PowerPoint files (two per firm with a
template; Lumen gave none) but **not those deals' records**, so figures cannot be compared. For each past memo a memo
is made through the API at each of the firm's stages and exported; the stage that follows it best is compared (a
tie goes to the stage the memo is named after). PowerPoint reads both files as drawn.

| Past memo | Slides found (in the same order) | Boxes found | Same place | Same font and size | Tables and charts |
|---|---|---|---|---|---|
| Merrow · Fieldloom (Final IC) | 11/15 (9) | 92/93 | 92/92 | 85/85 | 7/7 |
| Merrow · Rostrella (Final IC) | 11/14 (11) | 93/93 | 93/93 | 85/85 | 8/8 |
| Corriemoor · Brightmere | 13/16 (13) | 101/101 | 100/101 | 86/86 | 15/15 |
| Corriemoor · Cairnvale | 12/15 (12) | 93/94 | 92/93 | 80/80 | 13/13 |

What differs: appendix slides each deal added by hand (one-offs), two pictures, one table's width, and in Fieldloom
two sections in a different order than the firm's spec has today.

**Not covered:** figures (the past deals' records were not given); Lumen (no past memos); the wording of text people
or the model write (deal-specific).

## Measure 6: time and cost of a render

| Step | Merrow | Corriemoor | Lumen |
|---|---|---|---|
| Build a memo from the spec and the record | 5.5 s | 4.8 s | 3.1 s |
| Paint the PowerPoint | 1.6 s | 1.5 s | 1.0 s |
| Convert to PDF (with PowerPoint) | 4.0 s | 5.3 s | 4.5 s |
| Model cost to build or render | $0 | $0 | $0 |
| Slides | 11 | 13 | 2 |

Timed on a laptop against a hosted database. Source: a timing run on 9 Nov (not a suite report).

The steps that do use the model (gpt-5.5), read from the tokens and times the system stores for every call
(onboarding step files, `agent_runs`, the binding run's output):

| Step | Merrow | Corriemoor | Lumen |
|---|---|---|---|
| Onboarding a firm (once; steps 1–5, with retries) | $4.04 · 10 calls · 13 min | $3.92 · 8 calls · 13 min | $2.25 · 9 calls · 8 min |
| Binding to a record (once per record format) | $0.82 · 3 calls | $0.54 · 2 calls | $0.30 · 2 calls |

An agent request: 31 requests recorded, about $0.05 each (6,100 tokens in, 630 out on average), median 13 s,
longest 46 s. The agent eval's requests (custom slides with tables and charts) cost about $0.08 each ($1.19 /
$1.37 / $1.51 per firm). Model time only: a person's review of each onboarding step comes on top.

**Not covered:** timings under load.

## Measure 7: the third firm, configuration versus code

Report: `2026-11-09_0710.md`. Lumen came with a written guide only (no PowerPoint template, no past memos).

- **Configuration** (written by onboarding, no code of its own): 10 layouts (its master generated from the
  guide), 81 boxes, 9 text roles, 16 number and date formats, 6 colours, 5 sections and stages, 55 order rules and
  rules, 57 recipe segments, 38 figures, 4 table templates, 9 free areas.
- **Code**: no line of code names any firm (checked across the backend, scripts and evals: 0 hits). The generic
  paths only a guide-only firm takes, and what they surfaced: a generated master; 81 boxes drawn rather than
  placeholders; 3 tables with no column definitions (they were not measured and the canvas drew them empty: found
  by measure 3, fixed for every firm); one font not installed (measured with a stand-in, flagged).

**Not covered:** a fourth firm onboarded from scratch during the evals.

## Where it breaks, and how often

`where_it_breaks.md` (from `evals/WHERE_IT_BREAKS.md`): 45 issues found while building and testing, with cause,
area, severity and status; 32 fixed, 2 partly fixed, 10 open, 1 found not to be a bug (45 in all; numbered to 46, #7 unused). In the final runs above: nothing broke in the canvas, PowerPoint,
rounds, overflow and rebuild suites.

## Not built (stated as such)

- Promoting a one-off slide into the firm's template.
- Continuing long text on another slide.
- Replaying missed messages after a disconnection.
- Editing a firm's template inside the app (decided out of scope at the start).
