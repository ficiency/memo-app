# Tidewell deal record

The system of record for the Tidewell deal. Take every fact in every memo from here and nowhere else.

`Tidewell_Deal_Record.json` is the master. `Tidewell_Deal_Record.xlsx` holds the same content in readable form: Meta, Fields, one sheet per table, Series, Narrative.

## Schema (JSON)

- `record_id`, `record_version`, `as_of`, `reporting_currency`
- `company`: identity and legal entities
- `fields[]`: one object per datum: `id`, `label`, `kind` (money, number, percent, count, date, text, months, multiple), `value`, `unit`, `period`, `basis`, `source`. Derived fields also carry `derived_from` (the field ids they are computed from) and `formula`.
- `tables`: named tables with typed `columns` and `rows` (arrays aligned to the columns).
- `series`: monthly series as parallel `x` and `y` arrays.
- `narrative`: thesis, risks, company description, market, team, diligence status, Series C terms.
- `angles`: one block per firm's view of the deal (`equity_coinvest`, `growth_loan`, `secondary`) with its own tables and assumptions. `field_prefixes` lists the field ids it draws on. An angle's scalar numbers live in `fields`, not in the angle.

## Units and conventions

- Money is whole pounds sterling (GBP), never thousands or millions. Each firm formats it its own way.
- Percentages are fractions (0.25 is 25%). Multiples are plain numbers (4.5 means 4.5x). Dates are ISO (YYYY-MM-DD).
- The fiscal year ends 31 March: FY2026 is April 2025 to March 2026. `period` is a date for balances and an FY label for flows.
- Revenue is net of payment pass-through costs. ARR means live subscription ARR; contracted-not-live is a separate field.
- Forecasts are management's latest (model v4.3) unless the label says otherwise.

## Stability

Field ids are stable and will not be renamed. Values are not stable. The record is as of 30 September 2026 (latest actuals: August 2026) and will change during the build, so render from it rather than copying numbers. A change can add, change or remove fields, tables, series points and narrative text; do not assume a field you use will still exist or keep its value.
