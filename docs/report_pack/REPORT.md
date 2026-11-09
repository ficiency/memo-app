# One memo, every firm — report

**What was built.** A platform where each firm's memo format lives as data, memos are built from that format and a
deal's record, and people edit them three ways — in the browser, through an AI agent, or in PowerPoint and back —
with every edit checked against the firm's rules and nothing anyone did lost. Three firms are loaded (Merrow:
16:9 template; Corriemoor: 4:3 template; Lumen: a written guide only), one deal (Tidewell). Stack: FastAPI,
Postgres (Supabase), React; OpenAI for the model steps. Detail: `decisions.md`, `results.md`, `where_it_breaks.md`.

## The six capabilities

| Capability | Status |
|---|---|
| 1. The firm's template lives in the platform | Built. Onboarding in six reviewed steps (materials → house style → structure → layouts → recipe → rules): one model call each, validated by code, approved by a person; written as a versioned spec in tables. A memo stays on its version until someone moves it (See changes → Move; nothing lost). Editing a template inside the app: out of scope |
| 2. One memo, every output | Built. Native PowerPoint on the firm's own template; PDF converted from it by PowerPoint; master generated for the guide-only firm; text measured in the firm's real fonts, shrunk to the firm's minimum, else flagged "does not fit". Not built: continuing long text on another slide |
| 3. A live editor | Built. Template slides locked; free slides with a free area on the firm's grid; slides reorder where the rules allow; every edit is an operation checked on the server and shown to everyone live |
| 4. Custom slides, three ways in | Built: upload a PPTX/PDF slide (native where possible, flagged picture where not), build on the canvas, ask the agent. Not built: promoting a one-off slide into the template |
| 5. AI edits are operations | Built. The agent proposes operations, checked by the same server rules, applied only by a person; figures always from the record |
| 6. PowerPoint is an editor | Built. Identity in shape names; an edited file comes back as operations; typed-over figures refused, what cannot apply kept aside; a newer record is applied by a person, keeping people's words |

## Evals: results per firm

Every eval acts as people do (the screens' API, websockets, real PowerPoint files); expectations come from each
firm's rules, never its name; PowerPoint itself judges layout; faults planted on purpose were caught.

| Measure | Merrow | Corriemoor | Lumen |
|---|---|---|---|
| Brief's suite — canvas / PowerPoint (script, 5 round trips) / PowerPoint desktop / agent | 12/12 · 30/30 · 15/15 · 6/6 | 12/12 · 30/30 · 15/15 · 6/6 | 13/13 · 20/20 · 14/14 · 6/6 |
| **1** Edits that survive (2 people + agent + newer record + PowerPoint, 5 rounds) | 123/123 kept · 10/10 kept aside with a conflict · 0 lost | 113/113 · 10/10 · 0 lost | 38/38 · 10/10 · 0 lost |
| **2** Canvas slide through PowerPoint and back / whole memo untouched | 5/5 · 460/460 | 5/5 · 490/490 | 6/6 · 145/145 |
| **3** Long content: fits / shrunk / reported / **cut silently** | 29 / 6 / 32 / **0** of 67 | 47 / 1 / 16 / **0** of 64 | 25 / 4 / 17 / **0** of 46 |
| **4** AI edits: typed amounts / figures off the record / one figure two values | 0 / 0 of 122 figures / 0 | 0 / 0 of 83 / 0 | 0 / 0 of 58 / 0 |
| **6** Build + PowerPoint + PDF · model cost | 11.1 s · $0 | 11.6 s · $0 | 8.6 s · $0 |
| **7** Third firm | — | — | All configuration: 81 boxes, 57 recipe segments, 55 rules, 38 figures from onboarding; 0 lines of code name a firm |

**5** Past memos rebuilt from the spec (4 memos, structure only): boxes found 379/381, same place 377/379, same font and
size 336/336, exhibits 43/43, slides matched 47/60.

Also measured: the system's text measure agrees with PowerPoint on fits / does not fit for 208 of 210 boxes; boxes
nobody edited stay identical after PowerPoint desktop saves the file (336/336) and every shape keeps its identity
(244/244); the agent suite cost $1.19–1.51 per firm. Measure 5 compares structure only: the past memos' deal
records were not given, so their figures could not be checked.

## Where it breaks, and what was not tested

- **45 issues** found while building and testing, each with cause and status: 32 fixed, 2 partly fixed (chart axes
  and categories; onboarding naming chart series from their description), 10 open, 1 not a bug. The evals
  themselves found 3 real bugs late (tables with no column definitions not measured; template bullets and indents
  not kept; a picture saved by PowerPoint without an embedded image refusing a whole file) — all fixed at the root.
- **Not tested:** more than two people at once; reconnection mid-edit; a person clicking in PowerPoint (it is driven
  through its automation interface); past memos' figures (their deals' records were not given); load.
- **Not built:** promoting a slide to the template; long text continuing on another slide; replay after a
  disconnection; template editing in the app.

## Key decisions

1. **Facts, rules and work apart**: record, spec and memo are separate, each versioned; a memo is pinned to one spec
   version and one record revision until a person moves it.
2. **One door**: every edit from every editor is an operation checked on the server; per-box revisions mean a stale
   edit is kept aside as a conflict, never overwrites.
3. **One element model** for layouts, slides, canvas and PowerPoint; a figure is a text segment that remembers its
   record path, so it can be updated, traced and protected.
4. **Code checks the model**: onboarding steps and agent proposals are validated by code and retried with the errors;
   the agent never applies anything.
5. **Pendings are computed, not stored**: what a memo still needs disappears when it stops being true; only a
   person's "It's fine" is kept.
6. **Configuration, not code**: the guide-only firm went in entirely through onboarding.
