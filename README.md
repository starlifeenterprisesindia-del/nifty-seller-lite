# Nifty Seller Lite — V2.1 Pre-Market Integrity Audit

A read-only Streamlit decision-support app built around one authoritative DhanHQ
`MarketSnapshot`, one canonical final strategy brain, one protected strike planner,
one execution guard and one post-entry manual position monitor.

## What V2.1 hardens

- Confirms the strategy architecture has exactly one final brain:
  `analysis/decision.py::calculate_final_decision`.
- The compact evidence matrix remains display-only and cannot feed or override the
  final strategy calculation.
- A market-time clock is labelled `LIVE` only when both the NIFTY quote and the latest
  completed current-session candle pass their age guards.
- Option-chain analysis now requires CE/PE structure, a complete ATM pair, positive
  premiums and reasonable alignment between chain spot and the NIFTY quote.
- Option deltas reset after a continuity gap instead of comparing a fresh chain with an
  old intraday snapshot.
- PCR remains context only. It can reinforce matching premium+OI+volume evidence but
  cannot create a standalone bullish or bearish vote.
- Stale live NIFTY-futures volume cannot influence the core directional score.
- VIX and Top-7 quotes have separate live-age checks; stale context becomes unavailable
  or caution instead of being treated as fresh.
- CE, PE and Iron Condor rows receive only their own relevant support/resistance
  cautions.
- Compact directional rows are allocated in tenths so the displayed evidence mix adds
  to 100.0 after rounding.
- Manual Position Guardian freezes the planner's exact conservative entry-credit
  estimate without reconstructing it from rounded target fields.
- The Dhan instrument master refreshes daily and falls back safely to a stale cache if
  the remote master is temporarily unavailable.

## Existing protected workflow

1. One grouped market-quote request and one authoritative snapshot.
2. Price action, levels, EMA/MACD/RSI and NIFTY-futures volume.
3. Premium + OI + volume intelligence, movement windows, persistence, walls,
   clusters and contextual PCR.
4. Top-7 contribution, India VIX and optional FII/DII/event context.
5. One final brain: CE Sell, PE Sell, Iron Condor and WAIT Need.
6. Protected short-strike and mandatory hedge planner.
7. Freshness, confirmations, entry-window, risk-budget and one-trade guard.
8. Manual Position Guardian after the user marks a trade taken.

The app never places, modifies or exits a broker order. Live-market behaviour must be
verified with authenticated Dhan data before relying on the displayed setup.

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

Decision-support only. Verify broker positions, fills, spreads, slippage, charges,
margin, liquidity and hedge prices before acting.
