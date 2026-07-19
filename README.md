# Nifty Seller Lite — V1.8 Position Guardian

A read-only Streamlit decision-support app built around one authoritative DhanHQ
`MarketSnapshot`, one canonical strategy brain, one protected strike planner, one
execution guard and one post-entry manual position monitor.

## What V1.8 adds

- A manually marked `ENTRY READY` setup is frozen with its exact short and hedge
  strikes, entry prices, expiry, lots, lot size, entry spot and risk thresholds.
- The same Dhan option-chain response is retained internally for exact-leg
  monitoring, even when a moved strike falls outside the ATM table shown on screen.
- Current protected-combination close debit uses short-leg ask and hedge-leg bid,
  with LTP only as fallback.
- Shows estimated live P&L in points and rupees, target-capture progress and each
  leg's contribution.
- Deterministic alerts: `TARGET REACHED`, `SL TRIGGERED`, spot invalidation,
  compulsory-time exit, profit protection, rising risk and data-blocked state.
- Market-closed sessions remain `REFERENCE ONLY`; missing exact-leg prices never
  produce a false live P&L.
- Manual outcome recording stores the observed exit debit and estimated P&L.
- V1 discipline state migrates safely to the V2 journal schema.

## Existing production flow

1. One authoritative DhanHQ market snapshot.
2. Price action, levels, EMA/MACD/RSI and NIFTY-futures volume.
3. Premium + OI + volume intelligence, movement windows, persistence, walls,
   clusters and PCR.
4. Top-7 contribution, India VIX and optional FII/DII/event context.
5. One final brain: CE Sell, PE Sell, Iron Condor and WAIT Need.
6. Protected strike and mandatory hedge planner.
7. Freshness, confirmations, entry-window, risk-budget and one-trade guard.
8. Manual Position Guardian after the user marks the trade taken.

The app never places, modifies or exits a broker order.

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

Decision-support only. Combination debit and P&L are estimates from available
bid/ask values. Verify actual broker positions, fills, slippage, charges, margin,
liquidity and exit prices before acting.
