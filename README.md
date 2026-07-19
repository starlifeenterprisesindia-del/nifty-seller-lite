# Nifty Seller Lite — V1.5 Execution Guard

A read-only Streamlit decision-support app built around one authoritative DhanHQ
`MarketSnapshot`, one canonical strategy brain and one post-decision execution
safety gate.

## What V1.5 does

- Fetches NIFTY, India VIX, Top-7 heavyweights, completed 1m/3m/15m candles,
  nearest NIFTY futures volume and the nearest-expiry ATM±7 option chain.
- Calculates Price Action, Support/Resistance, EMA20/50, MACD, RSI and
  time-normalized NIFTY-futures volume.
- Calculates Premium + OI + Volume flow, 1m/3m/5m continuity, persistence,
  OI walls, wall migration, clusters and PCR.
- Uses one canonical final brain to display independent suitability scores for
  CE Sell, PE Sell and Iron Condor, plus a separate WAIT Need score.
- Converts the already-selected final action into protected short-strike and
  farther-OTM hedge candidates from the same option-chain snapshot.
- Applies a separate execution guard that cannot change the selected strategy.
  It checks consecutive fresh confirmations, entry time, protected-plan risk,
  one-trade/day state and live feed integrity.
- Calculates risk budget, risk per lot, permitted lots, target-credit capture,
  premium-loss trigger and compulsory exit time from editable risk settings.
- Defaults to a conservative 0.5% risk budget, one-lot cap, 10:15–11:30 entry
  window, 35% credit-capture target, 40% spread-loss trigger and 14:30 exit.
- Stores a small same-day local discipline journal. A manually marked trade
  locks the day; target/manual exit or SL outcome can be recorded afterward.
- Keeps every output reference-only when the market is closed.
- Supports optional bounded FII/DII and verified event-risk context.
- Never places, modifies or exits broker orders.

## Streamlit secrets

```toml
[dhan]
client_id = "YOUR_CLIENT_ID"
access_token = "YOUR_24_HOUR_ACCESS_TOKEN"
```

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
ruff check .
ruff format --check .
```

Decision-support only. Premium, target, stop and risk values are estimates based
on the current snapshot and configured inputs. Verify contract lot size,
bid/ask, slippage, margin, liquidity, broker fills and hedge pricing before any
trade.
