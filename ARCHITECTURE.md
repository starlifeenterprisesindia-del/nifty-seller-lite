# Architecture — V0.5 Core Market Engine

## Single data authority

Only `services/snapshot_service.py` orchestrates DhanHQ reads. It creates one
`MarketSnapshot` containing:

- NIFTY quote and 1m/3m/15m completed candles
- nearest NIFTY futures quote and 1m/3m/15m candles with volume/OI
- India VIX quote
- Top-7 configured stock quotes
- nearest-expiry ATM-window option chain
- timestamps and feed-integrity states

No file under `analysis/` imports requests, DhanClient, or performs network I/O.

## Canonical calculation modules

- `analysis/technical_utils.py`: completed-candle sanitation, ATR and confirmed swings
- `analysis/indicators.py`: EMA20/50, MACD 12/26/9, RSI14
- `analysis/price_action.py`: structure, current event, invalidation and move stage
- `analysis/levels.py`: confluence zones and remaining room
- `analysis/volume.py`: time-normalized NIFTY futures participation
- `analysis/core_market.py`: evidence-only integration

Every concept has one canonical implementation. Replaced logic is removed rather than
kept beside new logic.

## No final strategy brain yet

V0.5 has no CE Sell, PE Sell, Iron Condor, WAIT or order module. The Core Market Engine
only standardizes evidence for the future single decision engine.

## Next big milestone

Options Intelligence will add persistent intraday snapshots and canonical analysis of:

- option premium change
- OI change speed and persistence
- option volume
- writing/buying/covering/unwinding
- PCR and PCR trend
- OI-wall migration and strike clusters
- Top-7 weighted contribution and VIX state
