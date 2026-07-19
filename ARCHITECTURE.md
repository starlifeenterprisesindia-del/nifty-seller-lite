# Architecture — V2.8 Pre-Live Final Integrity

## One market-data authority

Only `services/snapshot_service.py` reads DhanHQ and builds one `MarketSnapshot`.
Analysis modules do not fetch data. The same option-chain snapshot is reused for option
intelligence, strike planning, monitoring and PDF reporting.

## Exactly one strategy brain

Only `analysis/decision.py::calculate_final_decision` may produce or stabilize CE Sell,
PE Sell, Iron Condor, WAIT, Final Action, Signal State, Fake-Move Risk and the conditional
5–15 minute outlook. `SnapshotService` calls it exactly once.

The evidence matrix, strike planner, execution guard, position guardian and PDF report
cannot select or override a strategy.

## Execution guard boundary

`analysis/execution_guard.py` consumes the already-selected action. It only applies:

- live-feed readiness,
- 75% minimum flow confidence,
- 1m/3m/5m flow-window maturity,
- option-flow persistence,
- two consecutive fresh confirmations,
- 3m/15m coherence,
- entry-time, risk-budget and one-trade rules.

It may return `ENTRY READY`, `WATCH`, `BLOCKED` or `REFERENCE ONLY`, but it cannot
change CE Sell into PE Sell or create a second strategy path.

## Date-wise institutional journal

`services/context_store.py` owns one atomic JSON journal.

- One row per ISO trading date.
- Same-date save is an upsert.
- Different dates remain separate.
- Only the latest 15 dated rows are retained.
- Missing values remain `null`, never zero.
- FII cash and DII cash are primary background evidence.
- FII index futures is optional secondary confirmation.
- Values above 100,000 crore are rejected as likely wrong units.
- JSON export/import provides manual backup for an ephemeral Streamlit filesystem.

## PDF boundary

`services/pdf_report.py` consumes an already-built snapshot. It makes no API request and
performs no independent strategy calculation.

The PDF contains readable audit tables, live-outcome checkpoints and a final completeness
checklist. Raw Snapshot JSON/code is intentionally excluded. Developer JSON remains only
inside the collapsed screen section.

## State and safety

- Live signal memory is same-session and bounded.
- Weekend/reference snapshots do not pollute live memory.
- Option history remains same-session and bounded.
- Dhan HTTP 429 responses are not immediately retried.
- Credentials, caches, generated PDFs and runtime journals stay outside source code.
- The app never places, modifies or exits broker orders.
