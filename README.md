# Nifty Seller Lite — V2.0 Compact Evidence & Integrity

A read-only Streamlit decision-support app built around one authoritative DhanHQ
`MarketSnapshot`, one canonical final brain, one protected strike planner, one
execution guard and one post-entry manual position monitor.

## What V2.0 changes

- Adds one six-row **All Features — Compact Evidence** table at the top of the app.
- The table covers every active input without creating a second strategy brain:
  1. Price Action
  2. OI & Options Flow
  3. EMA / MACD / RSI
  4. Levels & Volume
  5. Top-7 & FII/DII
  6. VIX / Data / Event Risk
- Every directional row shows normalized Bullish, Bearish and Neutral evidence that
  adds to 100. These values are evidence mix, not profit probability.
- The Final One-Brain Decision remains immediately below the compact table.
- Detailed planner, execution, position, core, options and raw-data sections are kept
  inside compact expanders so no feature is removed and the page stays short.
- Zero or missing India VIX is now `VIX DATA UNAVAILABLE`; it can never be treated as
  a balanced premium environment.
- Missing VIX adds a decision caution and a bounded WAIT penalty during a live session.
- Nested datetimes in Snapshot JSON are serialized to ISO-8601 strings.
- Long decision cautions move into a dedicated expandable table for better desktop and
  mobile readability.

## Existing protected workflow

1. One authoritative DhanHQ market snapshot.
2. Price action, levels, EMA/MACD/RSI and NIFTY-futures volume.
3. Premium + OI + volume intelligence, movement windows, persistence, walls,
   clusters and PCR.
4. Top-7 contribution, India VIX and optional FII/DII/event context.
5. One final brain: CE Sell, PE Sell, Iron Condor and WAIT Need.
6. Protected short-strike and mandatory hedge planner.
7. Freshness, confirmations, entry-window, risk-budget and one-trade guard.
8. Manual Position Guardian after the user marks a trade taken.

The compact evidence table is presentation-only. It does not change any score,
strategy, strike, risk limit, trade status or alert. The app never places, modifies or
exits a broker order.

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

Decision-support only. Verify actual broker positions, fills, spreads, slippage,
charges, margin, liquidity and hedge prices before acting.
