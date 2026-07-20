# Nifty Seller Lite — V2.8.1 Completed-Candle Guard Hotfix

A read-only Streamlit decision-support app built around one authoritative DhanHQ
`MarketSnapshot` and exactly one canonical strategy brain:
`analysis/decision.py::calculate_final_decision`.

## V2.8.1 hotfix

- The authoritative snapshot now keeps only fully closed 1m, 3m and 15m candles.
- A currently forming 3m/15m candle can no longer appear in a table labelled
  “completed candles”.
- Candle freshness age is measured from the candle close time, not its opening timestamp.
- The PDF applies a second defensive completed-row filter.
- Live-feed integrity is displayed separately from execution readiness.
- The final PDF checklist now reports `PASS / LIVE` for healthy required feeds even when
  a strategy, confirmation, timing or risk gate correctly keeps execution `BLOCKED`.
- A WAIT state with no selected protected setup no longer produces the misleading
  “risk could not be calculated” blocker.
- When a selected protected setup exceeds the configured budget, the blocker prints the
  exact one-lot rupee risk and the available risk budget.

## Retained V2.8 safety gates

A live setup cannot become `ENTRY READY` unless:

- NIFTY quote, completed candles and option chain are LIVE.
- Flow confidence is at least 75%.
- 1m, 3m and 5m option-flow windows are all READY.
- Flow persistence is mature.
- The final action has two consecutive fresh confirmations.
- 3m and 15m agree with the selected directional setup.
- The protected plan fits the configured risk budget.
- The one-trade-per-day lock is unused and the entry window is open.

## Deployment

Upload the 16 files listed in `V2_8_UPLOAD_LIST.txt` into the existing GitHub repository
root and replace matching files. Do not recreate the Streamlit app or change Secrets.

Expected version after redeployment:

```text
2.8.1_COMPLETED_CANDLE_GUARD_HOTFIX
```

Decision-support only. Verify broker quotes, liquidity, margin, fills and hedge prices.
