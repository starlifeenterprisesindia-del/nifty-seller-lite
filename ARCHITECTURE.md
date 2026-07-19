# Architecture — V2.0 Compact Evidence & Integrity

## One market-data authority

Only `services/snapshot_service.py` reads DhanHQ and builds one `MarketSnapshot`.
Analysis modules never fetch data. The option-chain response is parsed once and reused
for option intelligence, protected strike planning and exact-leg position monitoring.

## One strategy brain

`analysis/decision.py` remains the only module that can produce:

- CE Sell suitability
- PE Sell suitability
- Iron Condor suitability
- WAIT Need
- Final Action

The following modules cannot change the final strategy:

- `analysis/evidence_matrix.py` — six-row display summary only
- `analysis/trade_plan.py` — protected strikes after the decision
- `analysis/execution_guard.py` — pre-entry safety gate
- `analysis/position_guardian.py` — post-entry read-only monitor

## Compact evidence matrix

`analysis/evidence_matrix.py` consumes the already-built snapshot and groups all
active evidence into six display rows. Directional values are normalized within each
row so Bullish + Bearish + Neutral = 100. The matrix is not stored as a second market
state and is not passed into `analysis/decision.py`.

The six rows cover:

1. 3m and 15m price action
2. premium, OI, volume, movement windows, persistence, walls and PCR
3. 3m and 15m EMA, MACD and RSI
4. support/resistance room and NIFTY-futures participation
5. Top-7 weighted contribution and FII/DII background
6. India VIX, feed integrity, market session and verified event risk

## VIX integrity

A VIX last price that is missing, zero or negative is invalid. It becomes:

- regime: `UNAVAILABLE`
- movement: `UNAVAILABLE`
- seller environment: `VIX DATA UNAVAILABLE`
- status: `INVALID / UNAVAILABLE`

The decision brain applies only a bounded caution/WAIT penalty. It never converts
invalid VIX into a false balanced-premium signal.

## Compact UI order

1. Market-session banner and NIFTY header
2. All Features — Compact Evidence
3. Final One-Brain Decision
4. Collapsed planner/execution/position section
5. Collapsed detailed core evidence
6. Collapsed detailed options intelligence
7. Collapsed raw market data and ISO-safe Snapshot JSON

No calculation or feature is removed; only the default presentation is shortened.

## Bounded local state

- `services/option_state_store.py`: same-day bounded option-flow snapshots
- `services/context_store.py`: bounded FII/DII and verified-event history
- `services/discipline_store.py`: current-day signals, one-trade lock, frozen manual
  trade and manual outcome

Runtime files are gitignored and may reset after a Streamlit redeploy. Credentials,
broker order IDs and account positions are never stored by the app.
