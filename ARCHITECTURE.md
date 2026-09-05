# Architecture - V2.47

## One authoritative snapshot

`services/snapshot_service.py` builds one immutable `MarketSnapshot`. Screen and PDFs consume the same snapshot.

## Two brains, one canonical final gate

`analysis/decision.py::calculate_final_decision` owns Current Brain evidence.
`analysis/future_brain.py` owns the next-move forecast and never bypasses safety.
`analysis/decision_workspace.py` proposes one Future-compatible strategy from the
already protected plans. `analysis/execution_guard.py` validates that exact
candidate. Only an `ENTRY READY` guard may become Common Final `ENTRY ALLOWED`.
No UI, report, evidence or journal function may independently select or approve a
different strategy.

The production decision is one normalized 100-point calculation: completed 15m
permission + completed 3m trigger + indicators/core 40, option OI/flow 15,
futures volume 10, raw Futures+Top-9 activity 10, ATR-aware barrier room 15,
and confirmed special candle/W-M evidence 10. ENTRY READY additionally requires
75+ fit and all feed, persistence, risk and execution gates.

Directional entries require the completed 15m and completed 3m to agree with the
setup. Insufficient barrier room, explicitly opposite futures volume, or a confirmed
opposite pattern blocks the entry and keeps the canonical action at WAIT.

## Evidence modules

Price action, levels/barriers, futures volume, EMA/MACD/RSI, patterns, option flow/OI/PCR,
Top-9, VIX and verified events contribute bounded evidence or safety gates. FII/DII is
background/history context with zero live directional vote. News can add only fresh
context; unavailable news has zero weight.

## Premium calculator

`analysis/spot_premium_calculator.py` is a read-only utility. Manual targets and automatic R1/R2/S1/S2 targets use the same option-chain/Greeks/smile estimator. `estimate_target_reach` adds only an ETA range and reach-chance context using the existing speed, expected-move and barrier data; it never emits a strategy decision.

## Presentation

`ui/components.py` renders compact One-Brain, barrier, evidence and strategy views. `ui/premium_calculator.py` owns the compact premium calculator. Raw developer data is environment-gated. Feed diagnostics, risk budget and Execution Guard are not shown in the normal UI.

## State and reports

FII/DII keeps a bounded 15-session journal with local and optional private-cloud persistence. Option-flow history remains bounded by session. Quick and Full Audit PDFs render the same presentation-safe snapshot and never call market APIs or the decision brain.

Railway history is fetched before Future Brain so completed outcomes are
available, but the app observation is posted only after Future Brain, protected
strike re-ranking, the exact Execution Guard and Common Final decision are all
complete. Credit and debit spreads both freeze executable bid/ask legs.

## Read-only boundary

The app does not place, modify or exit broker orders.
