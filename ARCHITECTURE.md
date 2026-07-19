# Architecture — V0.8 Options Intelligence

## Single data authority

Only `services/snapshot_service.py` orchestrates DhanHQ reads and creates one
`MarketSnapshot` containing:

- NIFTY quote and completed 1m/3m/15m candles
- nearest NIFTY futures quote and volume/OI candles
- dynamically resolved India VIX quote with a configured fallback
- official-weight Top-7 stock quotes
- nearest-expiry ATM±5 option chain
- Core Market Evidence
- Options Intelligence, Top-7 contribution and VIX context
- feed integrity, state integrity and session status

No file under `analysis/` imports `requests`, `DhanClient`, or performs network I/O.

## Canonical calculation modules

- `analysis/technical_utils.py`: completed-candle sanitation, ATR and confirmed swings
- `analysis/indicators.py`: EMA20/50, MACD 12/26/9 and RSI14
- `analysis/price_action.py`: structure, current event, invalidation and move stage
- `analysis/levels.py`: confluence zones and remaining room
- `analysis/volume.py`: time-normalized NIFTY futures participation
- `analysis/core_market.py`: core evidence-only integration
- `analysis/option_intelligence.py`: one canonical Premium/OI/Volume flow engine,
  movement windows, persistence, walls, clusters and PCR
- `analysis/heavyweights.py`: official-weight Top-7 contribution and breadth
- `analysis/market_risk.py`: India VIX context

Every concept has one active implementation. Replaced logic is deleted rather than kept
beside the current code.

## Bounded option state

`services/option_state_store.py` is the only persistence implementation.

- JSON schema version 1
- atomic temporary-file replacement
- process lock where supported
- current trading date only
- current expiry isolated by session key
- maximum 180 snapshots
- no credentials, tokens, orders or arbitrary session state
- no snapshots saved when the market session is not confirmed live
- 1m/3m/5m comparisons reject distant samples outside tolerance

The store does not fetch data and does not make decisions. It only preserves sanitized
comparison evidence received from `SnapshotService`.

## Top-7 basket

The active basket follows the NIFTY 50 factsheet dated 30-Jun-2026:
HDFC Bank, ICICI Bank, Reliance Industries, Bharti Airtel, Larsen & Toubro,
State Bank of India and Axis Bank. Combined covered index weight is 45.20%.

## No final strategy brain yet

V0.8 has no strategy-decision or order module. The future V1.0 single brain may consume
only the canonical evidence objects after live option-state continuity has been verified.
