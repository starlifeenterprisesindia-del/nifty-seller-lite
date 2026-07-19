# Nifty Seller Lite — V2.8 Pre-Live Final Integrity

A read-only Streamlit decision-support app built around one authoritative DhanHQ
`MarketSnapshot` and exactly one canonical strategy brain:
`analysis/decision.py::calculate_final_decision`.

## What V2.8 adds

- A visible pre-entry data-readiness banner on the main screen.
- Live entry requires option-flow confidence of at least 75%.
- All 1m/3m/5m option-flow windows must be READY.
- Option-flow persistence must be out of WARMING UP.
- Two consecutive fresh confirmations remain mandatory.
- Directional entry requires 3m and 15m trend coherence.
- Manual FII/DII values above 100,000 crore are rejected as likely unit/input errors.
- The audit PDF no longer prints raw Snapshot JSON/code.
- PDF bullet lists render as real line breaks instead of visible `<br/>` text.
- PDF currency labels use `Rs.` to avoid unsupported-glyph black boxes.
- The PDF ends with a compact live-test checklist rather than a code appendix.

## Existing functionality retained

- Date-wise 15-session FII/DII journal with backup/restore.
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
6. The execution guard may only block or permit that selected action; it cannot choose another setup.
7. PDF reporting reads the completed snapshot only and performs no API request.

## Pre-entry safety gates

A live setup cannot become `ENTRY READY` unless:

- NIFTY quote, completed candles and option chain are LIVE.
- Flow confidence is at least 75%.
- 1m, 3m and 5m flow windows are all READY.
- Flow persistence is mature.
- The final action has two consecutive fresh confirmations.
- 3m and 15m agree with the selected directional setup.
- The protected plan fits the configured risk budget.
- The one-trade-per-day lock is unused and the entry window is open.

## Institutional data workflow

1. Select the trading date.
2. Enter FII cash net and DII cash net in crore.
3. Optionally enter FII index-futures net amount in crore, not contracts.
4. Save/update the selected date.
5. Download the JSON backup after completing the journal.

The app automatically calculates 5-, 10- and 15-session sums. Missing values remain
missing and never become zero.

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
