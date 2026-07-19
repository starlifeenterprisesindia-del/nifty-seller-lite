# Nifty Seller Lite — V1.2 Hedged Strike Planner

A read-only Streamlit decision-support app built around one authoritative DhanHQ
`MarketSnapshot` and one canonical strategy brain.

## What V1.2 does

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
- Shows estimated credit, wing width, maximum-risk points and breakeven levels.
  Estimates use available bid/ask prices and use LTP only as fallback.
- Keeps all candidates reference-only when the market is closed and blocks
  execution planning whenever the final brain says WAIT.
- Supports optional bounded FII/DII and verified event-risk context.
- Never places orders.

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

Decision-support only. Verify actual bid/ask, slippage, margin, lot size,
liquidity and hedge pricing in the broker before any trade.
