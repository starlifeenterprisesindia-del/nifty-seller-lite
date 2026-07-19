# Architecture — V2.1 Pre-Market Integrity Audit

## One market-data authority

Only `services/snapshot_service.py` reads DhanHQ and builds one `MarketSnapshot`.
Analysis modules never fetch data. The option-chain response is parsed once and reused
for options intelligence, protected strike planning and exact-leg monitoring.

## One strategy brain

Only `analysis/decision.py::calculate_final_decision` can produce:

- CE Sell suitability
- PE Sell suitability
- Iron Condor suitability
- WAIT Need
- Final Action

`services/snapshot_service.py` calls that function exactly once. These downstream
modules cannot select or alter the strategy:

- `analysis/evidence_matrix.py` — display summary only
- `analysis/trade_plan.py` — protected strikes after the final decision
- `analysis/execution_guard.py` — pre-entry safety gate
- `analysis/position_guardian.py` — post-entry read-only monitor

The evidence matrix is never imported into or passed to the final decision brain.

## Freshness and continuity barriers

- LIVE requires a fresh NIFTY quote and a fresh completed current-session 1-minute
  candle.
- Quote, candle, VIX, Top-7 and futures-volume use states are tracked separately.
- Invalid option structure or chain/NIFTY spot mismatch blocks option intelligence,
  planning and execution readiness.
- Intraday option comparison uses only a prior snapshot within the bounded continuity
  age. A gap resets analysis to day-change warming-up mode.
- 1m/3m/5m windows retain their own age tolerance.
- Stale live futures volume becomes neutral/unavailable inside core evidence.

## Calculation ownership

- Price action owns market structure.
- EMA/MACD/RSI own indicator evidence.
- NIFTY futures owns participation/volume evidence.
- Premium + OI + option volume own options direction.
- PCR is contextual only and cannot independently create direction.
- Levels modify only the relevant strategy: support affects CE Sell, resistance affects
  PE Sell, and two-sided room affects Iron Condor.
- VIX, Top-7, FII/DII, event risk and freshness remain bounded context/risk inputs.

## State and execution safety

- Option history is bounded and same-session only.
- Instrument master cache refreshes every 24 hours with safe stale-cache fallback.
- Strike planner cannot change the strategy.
- Execution Guard requires fresh feeds, confirmation and risk allowance.
- Position Guardian freezes exact planned entry credit and never places an order.
- Runtime files and credentials remain gitignored.
