# Nifty Seller Lite — V2.7 Institutional Journal Integrity

A read-only Streamlit decision-support app built around one authoritative DhanHQ
`MarketSnapshot` and exactly one canonical strategy brain:
`analysis/decision.py::calculate_final_decision`.

## What V2.7 adds

- Date-wise FII/DII journal: each trading date has its own row.
- Saving the same date updates that date only; another date is never overwritten.
- The journal keeps the latest 15 trading sessions, not 15 calendar days.
- Manual fields: FII cash net, DII cash net and optional FII index-futures net.
- Date selection reloads the saved values for that exact date.
- JSON backup download and restore for Streamlit restart/redeploy safety.
- Missing institutional values remain missing and never become zero.
- FII index futures is secondary confirmation; cash data remains primary.
- Missing option-chain data can no longer create a false Iron Condor/decay score.
- Current spot and completed-candle confirmation are separated for support/resistance status.
- Dhan HTTP 429 is not retried immediately, and app refreshes have a short cooldown.
- The audit PDF prints the exact capital, risk percentage and rupee risk budget used by the snapshot.

## Existing V2.6 functionality retained

- Bounded market memory, anti-flip confirmation and fake-move filter.
- Conditional 5–15 minute market outlook.
- Compact all-features evidence matrix.
- CE Sell, PE Sell, Iron Condor and WAIT One-Brain decision.
- Protected strike planner, execution guard and position guardian.
- Full immutable live-audit PDF generated from the same snapshot.

## One-brain workflow

1. One authoritative DhanHQ snapshot.
2. Price action, levels, EMA/MACD/RSI and NIFTY-futures volume.
3. Premium + OI + option-volume intelligence, movement windows, walls and contextual PCR.
4. Top-7, India VIX and optional 15-session FII/DII/event context.
5. One final brain calculates strategy scores, WAIT, signal memory, fake-move risk and outlook.
6. Downstream planner/guards consume that decision and cannot select another strategy.
7. PDF reporting reads the completed snapshot only and performs no API request.

## Institutional data workflow

1. Select the trading date.
2. Enter FII cash net and DII cash net.
3. Optionally enter FII index-futures net.
4. Save/update the selected date.
5. Download the JSON backup after completing the journal.

The app automatically calculates 5-, 10- and 15-session sums. NIFTY price, OI, VIX,
price action, Top-7 and candles are fetched automatically and should not be manually saved.

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
