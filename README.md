# Nifty Seller Lite — V0.5 Core Market Engine

Fresh, read-only Streamlit application built around one authoritative DhanHQ snapshot.
This release deliberately does **not** produce CE Sell, PE Sell, Iron Condor, WAIT,
strike selection, order placement, or any other trading command.

## What V0.5 adds

- 3-minute and 15-minute Price Action:
  - confirmed swing highs/lows
  - HH/HL, LH/LL, range and transition classification
  - pullback, recovery, breakout, breakdown, false break and rejection events
  - early, developing, mature and exhaustion-risk move stages
- Support and Resistance zones:
  - previous-day high/low/close
  - current-day high/low
  - opening-range high/low
  - confirmed 3m/15m swing levels
  - 3m/15m EMA20/EMA50 confluence
  - immediate/strong support and resistance, distance and remaining room
- NIFTY Futures Volume:
  - Dhan FUTIDX candles, not NIFTY index pseudo-volume
  - 3m and 15m relative volume
  - same-time-slot baseline across prior sessions
  - rising/falling participation and move confirmation
- Existing EMA20/50, MACD 12/26/9 and RSI14 connected to the same snapshot.
- Evidence-only Core Market summary:
  - Bullish Evidence %
  - Bearish Evidence %
  - Range/Mixed Evidence %
  - confidence, move stage, reasons and blockers

## Architecture lock

`One Dhan Snapshot -> isolated calculations -> Core Market Evidence`

Analysis modules never call Dhan or any network API. They receive immutable snapshot data
from `SnapshotService`. There is no decision engine and no hidden second advisor.

## Important

Core evidence percentages are **not strategy probabilities**. Final CE/PE/Condor/WAIT
scores will only be introduced after Options Intelligence, Top-7 contribution, VIX/PCR,
FII/DII and news-risk modules are validated.
