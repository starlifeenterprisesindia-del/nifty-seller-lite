# Architecture — V1.0 Final One-Brain

## Single market-data authority

Only `services/snapshot_service.py` orchestrates DhanHQ reads and creates one
`MarketSnapshot`. Analysis modules do not fetch data.

The snapshot contains:

- NIFTY, India VIX and Top-7 quotes
- completed NIFTY 1m/3m/15m candles
- nearest NIFTY futures quote and volume/OI candles
- nearest-expiry ATM±5 option chain
- canonical technical, level, volume and option-flow evidence
- optional institutional/event context
- one final read-only decision object
- feed/session integrity

## Canonical modules

- `analysis/indicators.py`: EMA20/50, MACD 12/26/9 and RSI14
- `analysis/price_action.py`: structure, event, invalidation and move stage
- `analysis/levels.py`: confluence zones and remaining room
- `analysis/volume.py`: NIFTY-futures participation
- `analysis/core_market.py`: completed-candle core evidence
- `analysis/option_intelligence.py`: Premium/OI/Volume flow, movement windows,
  persistence, walls, clusters and PCR
- `analysis/heavyweights.py`: official-weight Top-7 contribution
- `analysis/market_risk.py`: India VIX context
- `analysis/market_context.py`: optional FII/DII rolling context and verified
  event risk
- `analysis/decision.py`: the only final strategy brain

There is no second classifier, duplicate decision function or order module.

## Final strategy brain

The single brain consumes only canonical snapshot evidence. The architecture
weights are:

- Core price/trend/levels: 35%
- Options flow/PCR: 35%
- Top-7 heavyweights: 15%
- VIX/session/institutional/event background: 15%

CE Sell, PE Sell and Iron Condor are independent suitability percentages.
WAIT is a separate risk/uncertainty need and is not normalized against them.
Reference-only sessions force WAIT. Actionable output always includes
`WITH HEDGE`.

## Bounded local state

- `services/option_state_store.py`: same-day bounded option-flow snapshots
- `services/context_store.py`: maximum 120 dated FII/DII/event-context entries

Both use atomic replacement and lock files. Credentials and orders are never
stored. Runtime files are gitignored.
