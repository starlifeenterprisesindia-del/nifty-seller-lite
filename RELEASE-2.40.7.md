# 2.40.7 — History, pair selection and recording audit

Cumulative deploy package based on 2.40.6_LOCAL_GREEKS_GUARD. Existing risk settings and earlier fixes are retained. This release does not place broker orders or claim validated prediction accuracy.

## Implemented

- OI history: compare matching contracts in the same expiry/session across 3/5/15-minute windows. Show OI, premium and volume changes; skip resets, duplicate identities and discontinuous windows. Buying/selling pressure is inferred evidence, NOT counts of buyers or sellers.
- Futures VWAP: completed current-session futures candles only, with partial-session and stale-data labels. Compare futures price with futures VWAP, not spot with futures VWAP.
- FII/DII: separate prior published cash-flow and index-futures-contract series, with availability counts and 3/5/15-observation summaries. Missing data is not zero; futures-contract sums do not establish new intraday positions.
- Protected CE/PE pair selection: evaluate eligible short/hedge combinations together and display the top three. Compare credit, maximum expiry loss, buffer and pair score. Liquidity ranks use a common candidate population. Scores are not win probabilities; expiry payoff is not an intraday premium forecast.
- Recording: schedule the next wall-clock minute after completion rather than adding a minute to build time. Slow builds can still miss slots; no overlapping catch-up requests. Report observed-span coverage and distinguish app heartbeat from last changed AI event.
- Export: authenticated on-demand compressed JSONL export of recorded market tables through the expiry-record panel. No tokens are included. This export is not the separate paper journal or every runtime file.
- Expiry rollover: write a compressed detailed archive before purging current-cycle tables. Archive failure prevents that rollover deletion. Archives remain under the persistent recording root's `archives` directory; detailed archive files currently are not automatically pruned.
- Background paper monitoring: the app registers shadow positions with Railway; the recorder monitors registered positions even without the app open. Closed positions cannot be reopened by stale registration. Missing/stale executable quotes show EXIT DUE / PRICE UNVERIFIED, not a fabricated fill. Actual paper exit time is the first eligible observed snapshot, not an exact scheduled execution guarantee.
- Offline replay audit: `python -m services.replay_audit evidence.jsonl.gz` labels subsequent moves during WAIT from continuous same-session/version data. This identifies episodes for review, not missed guaranteed profits or a strategy backtest.

## Active calculation versus review context

Existing canonical OI and market scoring remain active. New OI-history, futures-VWAP and institutional-history panels are observation/review context with extra vote zero; they do not silently add duplicate directional weights. Pair ranking and recording/paper-monitor safeguards are active code changes.

No broker-private strike-selection algorithm was inferred or copied. Local Greeks validation from 2.40.6 remains: invalid or unverified rows are not made valid by hiding warnings. No automatic weight learning, newly calibrated probability, or proven accuracy uplift is claimed.

## Upload and verify

1. Upload the extracted contents of this cumulative ZIP into the existing repository root. Do not upload all older ZIP versions again.
2. Update/redeploy both app and Railway service if they are separate deployments. Confirm version `2.40.7_HISTORY_PAIR_AUDIT`.
3. Keep the existing persistent volume and credentials. Do not delete the volume. Recording must remain enabled and the app must have its configured authenticated Railway URL/key.
4. During a live session check sample timestamps, observed-span gaps, and history-window readiness. Closed-session or unavailable feeds must not be interpreted as valid new observations.
5. Open the expiry-record panel, prepare/download full evidence, and retain that export for the post-session audit. A successful deployment alone does not prove recording persistence; verify a real saved sample survives a restart.
6. Verify a registered paper position is checked in the background. Missing quotes must leave an unverified exit, never a synthetic profit.

Suggested commit: `Update v2.40.7: OI history, protected pair ranking, record export and paper monitoring`

## Validation boundary

Final local regression run: **378 passed**, 32 NumPy timedelta deprecation warnings, no test failures. Offline Streamlit panel rendering is included. Warnings concern future dependency compatibility and have not been suppressed.

Automated regression and offline UI tests cover contract matching, history gaps, VWAP, missing institutional data, archive rollback, pair payoffs, ambiguous exit quotes, deadline handling, paper registration and replay labels. Live Dhan/Railway execution and a full exported trading-session replay still require verification. Minute-based polling can react late; it is not tick-level execution.

Archive storage grows with retained cycles. Existing market/session feed checks remain in force; this release does not add a new authoritative exchange-holiday calendar.
