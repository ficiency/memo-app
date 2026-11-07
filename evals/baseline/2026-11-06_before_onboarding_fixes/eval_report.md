# Eval report · 2026-11-06 21:40

## Step 1 · The three firms fit the schema with no special cases

**corriemoor** (spec version d4fd7ae1)

| Item | In pilot | In DB | |
|---|---|---|---|
| palette colours | 9 | 9 | ✅ |
| text roles | 12 | 12 | ✅ |
| formats | 11 | 11 | ✅ |
| sections | 14 | 14 | ✅ |
| stages | 2 | 2 | ✅ |
| layouts (+ master) | 17 | 17 | ✅ |
| layout elements | 146 | 146 | ✅ |
| sections with a layout | 14 | 14 | ✅ |
| recipe segments | 174 | 174 | ✅ |
| rules (+ unmapped) | 32 | 32 | ✅ |

**lumen** (spec version 704981b0)

| Item | In pilot | In DB | |
|---|---|---|---|
| palette colours | 6 | 6 | ✅ |
| text roles | 9 | 9 | ✅ |
| formats | 13 | 13 | ✅ |
| sections | 3 | 3 | ✅ |
| stages | 2 | 2 | ✅ |
| layouts (+ master) | 10 | 10 | ✅ |
| layout elements | 77 | 77 | ✅ |
| sections with a layout | 3 | 3 | ✅ |
| recipe segments | 54 | 54 | ✅ |
| rules (+ unmapped) | 25 | 25 | ✅ |

**merrow** (spec version 51d9dea4)

| Item | In pilot | In DB | |
|---|---|---|---|
| palette colours | 13 | 13 | ✅ |
| text roles | 15 | 15 | ✅ |
| formats | 10 | 10 | ✅ |
| sections | 12 | 12 | ✅ |
| stages | 3 | 3 | ✅ |
| layouts (+ master) | 15 | 15 | ✅ |
| layout elements | 183 | 183 | ✅ |
| sections with a layout | 12 | 12 | ✅ |
| recipe segments | 124 | 124 | ✅ |
| rules (+ unmapped) | 35 | 35 | ✅ |

## Spec completeness · what painters need (G1–G7)

| Measure | corriemoor | lumen | merrow |
|---|---|---|---|
| G1 elements with several paragraph styles | 0 | 0 | 0 |
| G2 layouts with a background | 0 / 17 | 0 / 10 | 0 / 15 |
| G3 table styles as fields | 0 | 0 | 0 |
| G4 table columns with a width | 0 / 41 | 0 / 5 | 0 / 20 |
| G4 table cells with fixed text or fill | 0 | 0 | 0 |
| G5 table rows bound to a concept | 39 / 39 | 5 / 5 | 28 / 28 |
| G5 table column rules | 0 | 0 | 0 |
| G6 chart templates (chart boxes) | 0 (3) | 0 (1) | 0 (3) |
| G7 master text boxes with content | 0 / 0 | 1 / 4 | 0 / 0 |

## Configuration vs code · no firm names in code
Words checked: corriemoor, lumen, merrow. Hits: 0.

## Where it breaks

Nothing failed.
