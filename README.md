# Nifty Seller Lite — V1.0 Final One-Brain

A read-only Streamlit decision-support app built around one authoritative DhanHQ
`MarketSnapshot`.

## What V1.0 does

- Fetches NIFTY, India VIX, Top-7 heavyweights, completed 1m/3m/15m candles,
  nearest NIFTY futures volume and the nearest-expiry ATM±5 option chain.
- Calculates Price Action, Support/Resistance, EMA20/50, MACD, RSI and
  time-normalized NIFTY-futures volume.
- Calculates Premium + OI + Volume flow, 1m/3m/5m continuity, persistence,
  OI walls, wall migration, clusters and PCR.
- Supports an optional bounded FII/DII journal and verified event-risk context.
  Missing FII/DII values remain missing and are never converted to zero.
- Uses one canonical final brain to display independent suitability scores for
  CE Sell, PE Sell and Iron Condor, plus a separate WAIT Need score.
- Forces reference-only/closed sessions to WAIT and requires a hedge for every
  actionable setup.
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

Decision-support only. Verify broker prices, spreads, margin, liquidity and hedge
before any trade.
