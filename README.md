# Nifty Seller Lite — V0.8 Options Intelligence

Read-only Streamlit application built around one authoritative DhanHQ snapshot.
This release adds the complete options-evidence layer but deliberately does **not**
produce CE Sell, PE Sell, Iron Condor, WAIT, strike selection, order placement, or
any other trading command.

## What V0.8 adds

- Persistent intraday option-chain comparison without extra option-chain API calls.
- Atomic, bounded, same-day option state:
  - current date and expiry are isolated
  - maximum 180 snapshots
  - quick identical refreshes are deduplicated
  - old trading dates are removed from the active file
  - credentials and order data are never stored
- Premium + OI + option-volume classification at every ATM±5 strike:
  - Long Buildup
  - Short Buildup
  - Short Covering
  - Long Unwinding
  - Noise / Flat
- Side-aware directional interpretation for CE and PE flows.
- 1-minute, 3-minute and 5-minute change windows with continuity guards.
- Flow persistence across recent valid snapshots.
- CE and PE OI walls, wall migration and strongest three-strike clusters.
- Near-ATM OI PCR, day-addition PCR, intraday-addition PCR and volume PCR.
- Official NIFTY 50 top-seven heavyweight basket and weighted contribution.
- India VIX regime, movement and seller-risk context.
- Existing Price Action, Support/Resistance, Futures Volume and EMA/MACD/RSI remain
  connected through the same `MarketSnapshot`.

## Architecture lock

`One Dhan Snapshot -> isolated calculations -> Core Evidence + Options Evidence`

Analysis modules never call Dhan or any network API. The option-state store receives
only sanitized rows from `SnapshotService`. There is no decision engine and no hidden
second advisor.

## Important

- Core Market scores are independent evidence indexes out of 100; they are not
  probabilities and do not need to total 100.
- Options Intelligence bullish/bearish/mixed values are a normalized evidence mix and
  total 100, but they are still not strategy probabilities.
- The first live snapshot uses day change and shows `WARMING UP`. Intraday intelligence
  becomes ready after another valid refresh. 1m/3m/5m windows appear only when nearby
  historical samples exist.
- Closed-market output is `REFERENCE ONLY` and is not saved into intraday state.
