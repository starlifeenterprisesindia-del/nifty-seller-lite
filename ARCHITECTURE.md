# Architecture - V2.19

## One authoritative snapshot

`services/snapshot_service.py` builds one immutable `MarketSnapshot`. Screen and PDFs consume the same snapshot.

## One strategy brain

`analysis/decision.py::calculate_final_decision` is the only CE Buy, PE Buy, CE Sell, PE Sell, Iron Condor and WAIT selector. No UI or report function may recalculate or override it.

## Evidence modules

Price action, levels/barriers, futures volume, EMA/MACD/RSI, patterns, option flow/OI/PCR, Top-7, VIX, FII/DII and verified events contribute bounded evidence. News can add only fresh context; unavailable news has zero weight.

## Premium calculator

`analysis/spot_premium_calculator.py` is a read-only utility. Manual targets and automatic R1/R2/S1/S2 targets use the same option-chain/Greeks/smile estimator. `estimate_target_reach` adds only an ETA range and reach-chance context using the existing speed, expected-move and barrier data; it never emits a strategy decision.

## Presentation

`ui/components.py` renders compact One-Brain, barrier, evidence and strategy views. `ui/premium_calculator.py` owns the compact premium calculator. Raw developer data is environment-gated. Feed diagnostics, risk budget and Execution Guard are not shown in the normal UI.

## State and reports

FII/DII keeps a bounded 15-session journal with local and optional private-cloud persistence. Option-flow history remains bounded by session. Quick and Full Audit PDFs render the same presentation-safe snapshot and never call market APIs or the decision brain.

## Read-only boundary

The app does not place, modify or exit broker orders.
