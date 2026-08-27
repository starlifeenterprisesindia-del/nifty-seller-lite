# Nifty Seller Lite 2.38.0 — consolidated review

Includes the previously undeployed 2.37.0 working code plus this review.
This is a complete source upload, not a patch ZIP. No broker order execution.

## Final direction weights

| Input | Base points out of 100 |
|---|---:|
| Price action (completed 15m 80%, 3m early 20%) | 25 |
| Completed 15m MACD | 8 |
| Completed 15m RSI | 9 |
| Completed 15m EMA | 3 |
| Matched 5m OI / option flow | 35 |
| Raw futures price + OI | 10 |
| Top-9 recent 15m weighted change | 10 |

Core = 45; options = 35; raw futures = 10; recent Top-9 = 10.
Missing inputs have no direction vote; remaining base weights are not inflated
to 100. Evidence-table percentages describe each available input, not a
calibrated probability of winning. Entry suitability can be lower than the
base score because location, risk and confirmation checks remain.

## Implemented changes

- Main AI selects hedged CE SELL, PE SELL or a genuinely range-qualified Iron
  Condor. Buy rows remain reference calculations, not competing main choices.
  Mixed/missing evidence is not an automatic Iron Condor.
- Canonical direction is preserved in the headline; Core disagreement is shown
  separately. WAIT does not advertise a live entry or active strikes.
- Strategy switches need candidate persistence in addition to existing
  direction timers. A change in recommendation never converts a recorded trade.
- Big Player distinguishes pressure, strength, repeated observations and actual
  price response. Two observations do not prove institutional participation.
  Futures use a recent fixed window; 3m volume is not replaced by whichever
  historical timeframe has the largest ratio. Stale shocks are not perpetuated.
- Top-9 shows day change AND 3m/15m change, recovery/further weakness and approximate
  weighted Nifty-point contribution. It needs about 15 minutes of observations
  after a cold start. Partial coverage is exposed. No invented residual index move.
- Options main score uses matched 5m expiry/strike/security observations.
  1m/3m are early context. Missing/unchanged OI does not manufacture a range vote.
- Barriers remain location/space checks, without a new independent base vote.
  Existing overlap/compression and breakthrough handling retained.
- FII/DII remains saved background context with zero live base vote. News that is
  old/missing has zero score/confidence penalty; verified fresh event/news risk
  still applies. No new news feed or subscription has been added.
- VIX is risk context, not bullish/bearish evidence. High-but-stable VIX alone is
  not a direction signal. Rising volatility can still raise risk.
- Protected pair selection compares up to six eligible short candidates with
  hedge alternatives; considers net credit versus defined maximum risk and
  executable bid/ask. Crossed/wide/missing books and invalid credit are rejected.
  This is bounded heuristic selection, not an exhaustive options optimizer.
- Hidden RSI Top–Bottom strategy remains separate; its 3m RSI/Barrier/OI/Big
  Player logic and actual-fill protection from the previous version are retained.

## Independent Strike Entry Planner

Inside Spot-to-Premium Calculator, in new-entry mode:

- Selected CE/PE and BUY/SELL; completed 3m candle + barriers + live quotes only.
- No Main AI score, vote or approval dependency.
- ENTRY NOW on a qualified retest hold or momentum continuation; otherwise
  WAIT FOR RETEST, NO CHASE, MISSED or CANCEL.
- Freeze the setup zone until explicit reset; cancel if invalidated.
- SELL requires a farther same-expiry hedge; combined spread credit/risk and
  configured quantity cap are checked. A 10% cost reserve is a conservative
  allowance, not an exact fees or guaranteed-fill model.
- Premium ranges are conditional estimates, not a guaranteed best entry.
  Retest estimates assume three minutes and unchanged IV; absent reliable Greeks,
  no target premium is fabricated. Quote validity is shown.
- Existing actual-entry inputs remain separate from the planner.

## Strong pattern alerts

Only strong CONFIRMED completed 3m candle or W/M patterns near a level and aligned
with already-bullish/bearish Core plus options evidence can alert. Forming,
weak and opposite patterns do not alert. No extra directional score is added.
App and authenticated Telegram gateway deduplicate pattern IDs.
These app-computed pattern alerts require app refreshes; they are not a new
autonomous background chart scanner. Gateway deduplication resets on restart.
Existing price-move Telegram functionality is retained.

## Paper journal

- Auto paper threshold: strategy score >=45 AND entry confidence >=45.
- Option-flow confidence >=55, at least two ready windows, live feeds, available
  protected plan, configured entry window, one-lot risk budget, cooldown and
  five-paper-trades/day cap remain.
- A WAIT main decision can supply a paper-only copied candidate; main WAIT is
  never changed. Each record stores the original AI action and test qualification.
- Real entry thresholds are separate; lowering paper threshold does not loosen
  live execution guards.
- Reason log, date history, 45–49 / 50–54 / 55+ score bands and persistence errors
  are visible. Missing exit quotes do not create a fictitious zero-P&L close.
- Manual paper-entry button is deferred as requested. No real order is sent.
- Preserve existing configured capital/risk/lot limits. 45 alone does NOT ensure
  a paper trade: a blocked candidate now exposes the reason. Do not raise risk
  merely to produce a journal entry.

## Verification and limits

Regression suite, targeted paper/entry/alert tests, Streamlit offline panel
checks, lint and syntax checks run before packaging. See handover for final count.
Tests use synthetic/stub data: no live Telegram message, broker order, GitHub push
or deployment was performed.

Weights and thresholds are engineering starting points, not backtested success
rates. No claim is made that every real-time feed, broker premium or market
direction will match. Greeks, IV, quote timing and chart candle completion can
legitimately differ. Top-9 configured weights are dated 2026-07-31, not auto-updated.
There is no new scenario backtester or guaranteed SL-fill mechanism.

## Upload

1. Keep a backup of your current repository and journal/FII-DII data.
2. Extract this ZIP. Upload the CONTENTS of nifty-seller-lite-main into the
   existing repository root; do not create an extra nested app folder.
3. Update both deployments that use this repository (Streamlit app and Railway
   gateway). Existing secrets/environment values stay unchanged.
4. Confirm version 2.38.0_CONSOLIDATED_REVIEW. Inspect data freshness and the paper
   journal reason log; allow flow/Top-9 history to warm up.
5. Forward-test with paper records before relying on changed scores for money.

Runtime data, secrets, caches, tests and old development notes are deliberately
not included in this small deployment package. Existing live journal records are
not deleted or replaced by this ZIP.
