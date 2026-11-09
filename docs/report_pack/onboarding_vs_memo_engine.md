# Onboarding: this rebuild compared with the earlier build (memo-engine)

`memo-engine` was a first build of the same assessment. This system (`memo-app`) is a rebuild from scratch that
took ideas from it, not code. This page compares only **onboarding** (how a firm's format is learned). Source for
memo-engine: its onboarding README (`memo-engine/onboarding/README.md`), not a full code review.

## What memo-engine did

Five steps, **run by the team, outside the product**:
1. A script imports the firm's PowerPoint template into layouts (boxes with position and resolved default style,
   fixed elements, theme colours and fonts).
2. **Claude in a separate conversation**, following a written instruction file, turns the guide into rules
   (`guide.json`, each item with its source sentence); a person reviews the file.
3. A script cross-checks template against guide; Claude reviews the mismatches; a person reviews.
4. A script merges everything into one `config.json` per firm.
5. Scripts read past memos and the record's fields; Claude maps what goes in each box; a person reviews.

Precedence: **the guide wins; the template wins only where the guide is silent**, and every override is recorded.
The output is **JSON files per firm** on disk.

## What this rebuild does

Six steps **inside the product** (Firm setup screens): materials, house style, structure, layouts, recipe, rules.
Each step is one model call through the API with a package code prepares, **code validates the answer and retries
with the errors (at most twice), then a person approves it on screen**. A writer turns the approved steps into a
**versioned spec in database tables**; binding to a deal record is a separate, reviewable step.

## Side by side

| | memo-engine | memo-app |
|---|---|---|
| Who runs it | The team, by hand (scripts + a separate Claude chat) | The firm's people, in the app |
| Model use | Claude in a chat following instruction files | One API call per step, package built by code |
| Checking the model | A person reads the files | Code validates every answer (counts, references, formats against examples) and retries; then a person approves |
| Human review | Editing JSON files | Review screens per step; conflicts shown with evidence, decided and stored |
| Output | JSON files per firm, one merged config | Database tables, one row per item (layouts, boxes, roles, formats, sections, rules, recipe segments, concepts…) |
| Versions | None (files overwritten) | Every approval is a new spec version; memos stay on theirs until moved (with See changes) |
| Guide vs template | Guide wins, overrides recorded | Same idea, kept: conflicts shown with both sides and decided by a person |
| A firm with no PowerPoint template | Not described (step 1 needs the template) | Layouts designed from the guide; a master generated (Lumen) |
| Formats | Rules from the guide | Fields (decimals, scale, symbol, negatives…) checked against the examples in the materials (gap G8) |
| What goes in each box | A mapping file per firm | Recipe segments per box (fixed words, figure, model writes, person writes, conditional), tables and charts bound to concepts |
| Text layout from the template | Default style resolved per slot | Style, insets and paragraph format (indent, bullets, spacing) resolved as PowerPoint inherits them, kept on every box |
| Rules | Rules as data with their source sentence | Instances of a fixed library of rule types with parameters; what no type expresses listed as "unmapped" with its quote |
| Connecting to deal data | Part of the mapping step | A separate binding: one model call, a review screen with flags (missing, broken, diverged, mirror, unit), changes offered to existing memos |

## What was kept, what changed, and why

- **Kept**: reading the template into the system's own box model (not a PowerPoint dump); resolving a box's
  effective style through master and layout; the guide-versus-template precedence with every override recorded;
  the source sentence behind each rule.
- **Moved into the product**: the brief asks that "a model may read [the master and past decks] into a draft that a
  person approves". In memo-engine that loop lived in chats and files run by the team; here it is a screen the
  firm uses, so a new firm needs no engineer.
- **Code checks the model**: each answer is validated against the material before a person sees it, and errors go
  back to the model. Reviewing files by eye does not scale and lets silent mistakes through.
- **Tables, versioned, instead of a config file**: memos, the canvas and the evals query the spec; a version that
  changes cannot change memos built on it; a memo is moved to a new version on purpose.
- **A firm with only a guide works** (the brief's third firm): layouts and the master are designed from the guide.
- **Measured**: what painters need from a spec (gaps G1–G8: paragraph styles, backgrounds, table styles and column
  widths, table rows bound to figures, chart templates, master content, formats as fields) is measured per firm by
  the eval runner, and every onboarding item is checked to land in the tables.
