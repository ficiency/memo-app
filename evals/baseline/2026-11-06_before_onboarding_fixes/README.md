# Baseline before the onboarding fixes (6 Nov 2026)

What the pilot onboarding (steps 1–5) produced, measured before porting it with the G1–G7 fixes.
Source: `docs/pilots/preview/compare.py` review of a memo painted from each firm's spec, and the eval runner.

## Preview review findings per firm

| Finding | merrow | corriemoor | lumen |
|---|---|---|---|
| abstract table columns | 0 | 1 | 0 |
| abstract table rows | 0 | 1 | 0 |
| chart without data model | 3 | 3 | 1 |
| likely overflow | 6 | 1 | 20 |
| master box without content | 0 | 0 | 27 |
| several text styles in one box | 12 | 21 | 0 |
| table style in prose | 1 | 1 | 1 |
| **total** | **22** | **28** | **49** |

## Model use of the pilot run (steps 1–5)

| Firm | Model calls | Tokens (prompt + completion) |
|---|---|---|
| merrow | 6 | 150,034 |
| corriemoor | 6 | 227,167 |
| lumen | 6 | 98,777 |

## Files

- `step1_schema_fit.md`: every pilot item lands in the DB tables (step 1 eval).
- `eval_report.md`: full eval run on the pilot import, including spec completeness (G1–G7) measured in the DB.
- `preview_review_<firm>.txt`, `preview_findings_<firm>.json`: the preview review, in full.

Known gaps not counted by the review (found visually): layout backgrounds (G2), template table structure (G4),
text-box borders and painter gaps P1–P4 (see `docs/pilots/preview/README.md`).
