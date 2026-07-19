# Architecture — V2.7 Institutional Journal Integrity

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

## Date-wise institutional journal

`services/context_store.py` owns a single atomic JSON journal.

- One row per ISO trading date.
- Same-date save is an upsert.
- Different dates remain separate.
- Only the latest 15 dated rows are retained.
- Missing values remain `null`, never zero.
- FII cash and DII cash are primary background evidence.
- FII index futures is optional secondary confirmation.
- Medium/high event risk is used only when manually verified.
- JSON export/import provides manual backup for an ephemeral Streamlit filesystem.

The journal is background evidence only. It cannot call the decision engine or create a
second brain.

## Missing option data safety

An unavailable option chain is not treated as neutral decay evidence. CE Sell, PE Sell
and Iron Condor are marked `UNAVAILABLE` with zero suitability and Final Action remains
WAIT. This prevents a false Condor score from `range_score=100` placeholders.

## Support/resistance ownership

Current spot determines whether price is inside, above or below a level zone. Completed
candles determine whether a break or rejection is confirmed. A completed close above a
zone cannot label current spot inside that zone as already broken.

## Dhan request discipline

- App snapshot refreshes have a short user-interface cooldown.
- Dhan HTTP 429 responses are never immediately retried.
- Option-chain calls remain read-only and are not triggered by PDF generation.

## PDF boundary

`services/pdf_report.py` consumes an already-built snapshot. It makes no API request and
performs no independent strategy calculation. It also records the exact capital, risk
percentage and risk budget used for the snapshot.

## State and safety

- Live signal memory is same-session and bounded.
- Weekend/reference snapshots do not pollute live memory.
- Option history remains same-session and bounded.
- Credentials, caches, generated PDFs and runtime journals stay outside source code.
- The app never places, modifies or exits broker orders.
