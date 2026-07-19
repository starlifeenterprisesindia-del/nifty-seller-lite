# Nifty Seller Lite — V2.6 Market Memory & Full Live Audit PDF

A read-only Streamlit decision-support app built around one authoritative DhanHQ
`MarketSnapshot` and exactly one canonical strategy brain:
`analysis/decision.py::calculate_final_decision`.

## What V2.6 includes

- All V2.5 market-memory, anti-flip and fake-move-filter functionality.
- Bounded same-session memory of the latest five fresh signal snapshots.
- Developing, Confirmed, Transition / Wait and rapid-reversal signal states.
- Conditional `Next 5–15 Min Market Outlook` with bullish, range and bearish paths,
  fake-move risk, signal memory and invalidation.
- A `Generate Full Live Audit PDF` button near the top of the app.
- The PDF freezes the exact current `MarketSnapshot`; it performs no API request and
  does not recalculate CE Sell, PE Sell, Iron Condor, WAIT or the outlook.
- Full report sections for feed freshness, compact evidence, final one-brain decision,
  support/resistance, indicators, NIFTY-futures volume, OI/option flow, PCR, walls,
  Top-7, VIX, FII/DII, events, strike plans, execution guard and position guardian.
- Raw active option-chain rows and recent completed NIFTY/futures candles.
- A 5-minute and 15-minute outcome-verification worksheet plus canonical snapshot JSON.

## One-brain workflow

1. One authoritative DhanHQ snapshot.
2. Price action, levels, EMA/MACD/RSI and NIFTY-futures volume.
3. Premium + OI + option-volume intelligence, movement windows, walls and contextual PCR.
4. Top-7, India VIX and optional FII/DII/event context.
5. One final brain calculates CE Sell, PE Sell, Iron Condor, WAIT, signal memory,
   fake-move risk and the conditional outlook.
6. Protected strike planner consumes the final action only.
7. Execution Guard applies freshness, confirmation, risk budget and one-trade rules.
8. Position Guardian monitors a manually marked trade; it never places or exits orders.
9. PDF Report reads the completed snapshot only and creates an immutable audit record.

## Live testing workflow

1. Fetch a fresh snapshot.
2. Generate and download the audit PDF.
3. Repeat at important checkpoints or after 5 and 15 minutes.
4. Compare snapshot IDs, times, NIFTY price, outlook, invalidation and later movement.
5. Classify the result as Correct, Partial, Wrong or Fake Move Avoided.

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

Decision-support only. Verify actual broker prices, spreads, liquidity, fills, margin,
charges and hedge prices before acting.
