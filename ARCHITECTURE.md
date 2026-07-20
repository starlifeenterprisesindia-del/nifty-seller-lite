# Architecture — V2.8.1 Completed-Candle Guard Hotfix

## One market-data authority

Only `services/snapshot_service.py` reads DhanHQ and builds one `MarketSnapshot`.
Analysis modules do not fetch data. The same option-chain snapshot is reused for option
intelligence, strike planning, monitoring and PDF reporting.

## Completed-candle authority

Dhan may return the currently forming interval. `SnapshotService` marks intervals and
then removes every row whose `is_complete` flag is not true before the authoritative
1m/3m/15m frames are stored or analysed. Missing completion metadata fails closed.

Candle feed age is measured from the last completed 1-minute candle's closing time.
`services/pdf_report.py` repeats a defensive completed-row filter before printing raw
candle audit tables; it does not recalculate market evidence.

## Exactly one strategy brain

Only `analysis/decision.py::calculate_final_decision` may produce or stabilize CE Sell,
PE Sell, Iron Condor, WAIT, Final Action, Signal State, Fake-Move Risk and the conditional
5–15 minute outlook. `SnapshotService` calls it exactly once.

The evidence matrix, strike planner, execution guard, position guardian and PDF report
cannot select or override a strategy.

## Feed state versus execution state

Required feed health and execution readiness are separate:

- Feed health: quote + completed candles + option chain are independently shown as
  `PASS / LIVE` or blocked.
- Execution readiness: strategy score, flow windows, timeframe coherence, confirmations,
  entry time, one-trade lock and risk budget may still return `BLOCKED`.

## Execution guard boundary

`analysis/execution_guard.py` consumes the already-selected action. It may block or permit
that action but cannot choose another setup. Risk calculation is required only when a
concrete protected setup has actually been selected. For a selected setup that does not
fit the budget, the exact one-lot risk and budget are shown.

## PDF boundary

`services/pdf_report.py` consumes an already-built snapshot. It makes no API request and
performs no independent strategy calculation. Raw Snapshot JSON/code remains excluded.

## State and safety

- Live signal memory is same-session and bounded.
- Weekend/reference snapshots do not pollute live memory.
- Option history remains same-session and bounded.
- Dhan HTTP 429 responses are not immediately retried.
- Credentials, caches, PDFs and runtime journals stay outside source code.
- The app never places, modifies or exits broker orders.
