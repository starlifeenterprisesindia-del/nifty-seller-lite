# 2.40.0 — Expiry-cycle evidence and explain-only AI context

Complete cumulative package; supersedes the one-day retention rule in 2.39.0.
Existing capital Rs 900,000, risk limits, strategy weights and trade actions unchanged.

## Deployment

Upload this package's contents, not all old ZIPs. Attach a real persistent Railway volume at
`/data`, ensure `RAILWAY_VOLUME_MOUNT_PATH=/data`, and set `DAY_MEMORY_ENABLED=1`.
Existing Dhan credentials and LIVE_API_KEY remain unchanged. ONE replica/worker only.
Setting a path variable alone does not create a persistent volume. Recording stays off without
the switch and blocks if the volume path is missing. No remote deployment was done here.

Storage path remains `/data/nifty_day_memory/session.sqlite3` so a 2.39.0 database can migrate
without deleting its rows. No additional database service/subscription is required by the code.
CPU/RAM/network costs still depend on Railway usage; no fixed bill or free-usage promise.

## Retention and cleanup

- Keep detail across sessions through the actual captured contract expiry, not a hard-coded Tuesday.
- New session resets live reaction phases, NOT historical samples/candles/events.
- On first fresh session after cycle expiry, archive a compact summary, then purge old cycle detail
  in the SAME transaction. Failed summary write rolls back deletion. Keep last eight summaries.
- Early selection of a later expiry does not erase the active cycle. Every sample keeps actual
  contract expiry; comparisons never pair different expiry/contract IDs. Details collected during
  the active cycle are archived as that collection cycle, even if another expiry was selected.
- No fresh data, weekends or token failures do not trigger destructive rollover.
- FII/DII 15-session journal, shadow/actual journal and warm-up provider candles are not purged.
- Freed SQLite pages are reused; allocated database MB may exceed today's live rows after cleanup.

## Added evidence

- Greeks with quality/reason, days/hours to expiry, and futures contract ID/expiry. Invalid/missing
  quality Greeks are null, never zero; IV WARNING stays explicitly conditional.
- Original app candidate's protected legs are frozen at signal capture. Bid/ask, security ID, expiry,
  original action (including WAIT), score band and version are retained. Changed action/candidate/
  strikes/version/5-point score band starts another observation, not an order or fill.
- Selected pending legs stay in saved option rows while provider supplies them. Missing legs,
  crossed or >15%-wide books, wrong expiry/version/contract produce unavailable spread marks.
- Entry credit = sell bids minus hedge asks; exit cost = short asks minus hedge bids.
  Equal one-unit legs only. Net credit must be positive and below maximum wing width.
- At 5/15/30 minutes, first sample within a 2-minute tolerance records spot change and estimated
  spread points. Late/missing horizons are UNAVAILABLE, never zero profit or filled retrospectively.
- Observed favourable/adverse spot and spread movements use available minute samples. Gaps and
  incomplete spread paths are explicit. These are NOT true intraminute extrema and exclude fees,
  slippage, fill size/queue effects and position-management exits. No summing these as daily P&L.
- Cycle summaries group overlapping candidate observations by action/setup/horizon, with mean
  observed spread points, missing/gap counts and worst observed adverse spread points. Not win rates,
  independent trades, strategy validation or guaranteed profitability.
- App institutional context/date and cash/futures fields are kept separately in original observations.
  No FII identity inferred from OI, no new FII feed, no change to FII calculations or extra direction vote.

## How AI uses history in this stage

Main AI card and diary show an explain-only history context: same-day, same-expiry, same-version,
fresh observations only. Future timestamps, stale history and gaps are excluded. Sustained agreement
requires at least three observations spanning two minutes. Opposing recent flow is described as
possible recovery/pullback, not a proved reversal. Background reference can differ from app manual
settings; it is not relabelled as the actual app decision.

IMPORTANT: This stage links history to explanation and review, NOT to scoring, strategy switching,
entry thresholds or risk overrides. Calibrated history-driven decision changes require multi-cycle
evaluation later. No autonomous learning/weight tuning or accuracy claim.

## UI and operational limits

`Expiry-cycle record — Barrier, AI aur spread results` shows cycle date, measured MB, current-zone
historical reaction counts, latest 30 events (100 returned), last 30 horizon results and eight summaries.
Exact observed zone boundaries are matched; nearby shifted zones are not merged. Old events are not
fresh confirmation. No unsupported count of independent successful tests/trades.

Recording still samples at most once/minute, with 60 seconds between builds; long data fetches reduce
coverage. Market window and one-minute closing allowance unchanged. App-closed periods record
background market evidence but do not fabricate actual-app decisions or candidate entries.
Recorder does not dispatch new Telegram messages or create paper/broker trades.

## Verification

Offline tests cover five-session retention, holiday-adjusted expiry date, old database migration,
transaction rollback, eight-summary cap, same-contract conservative spread math, missed horizons,
invalid books, future/stale context rejection, no score mutation and protected journal preservation.
Synthetic 5-session fixture: 1,875 samples, 3,750 candle rows, 42 options/sample plus Greeks =
23,851,008 bytes (~22.75 MiB). Real size/CPU depends on changing contracts/events and runtime files.
Initial 25–75 MB cycle budget remains an estimate, not a hard limit.

Final verification: 325 tests passed; Ruff passed. Full suite completed in 59.18 seconds.

Still required after deployment: verify mounted-volume persistence/restart, browser display, actual
feed timing, recording with browser closed, Railway bill/usage, and several live cycles of outcomes.
Synthetic tests cannot establish trading accuracy. Existing NumPy timedelta deprecation warnings remain.
