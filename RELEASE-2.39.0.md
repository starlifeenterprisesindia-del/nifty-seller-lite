# 2.39.0 — One-day market memory

Complete cumulative deployment package based on 2.38.2; capital remains Rs 900,000.
No broker orders, extra Telegram messages, journal trades, or extra AI votes added.

## Railway activation (required)

1. Upload this cumulative package's contents to the existing repository.
2. Attach a Railway persistent volume to the existing live-server service, mount `/data`.
3. Ensure `RAILWAY_VOLUME_MOUNT_PATH=/data` points to that actual mounted volume.
   Setting the variable without attaching a real volume does NOT provide durability.
4. Set `DAY_MEMORY_ENABLED=1`. Keep existing Dhan credentials and LIVE_API_KEY unchanged.
5. Use ONE replica / one uvicorn worker. A file lock prevents duplicate workers on the same volume.
6. Deploy/restart. Open `Aaj ka record — Barrier aur important changes` below the compact Barrier.
   Check recorder status, record date, first/last sample time, sample count and measured DB MB.
7. In live hours close the browser for several minutes, reopen, and verify sample count advanced.
   Restart service and verify today's rows remain. These require live deployment validation.

Off by default. Missing volume blocks recorder, not the existing app. To stop new recording,
set DAY_MEMORY_ENABLED=0; no deletion or token changes required.

## What is recorded

- Background build runs the existing SnapshotService through the shared rate-limited Dhan gateway.
  One build at a time, with 60 seconds between completed cycles. Long requests reduce coverage;
  no catch-up request burst. Live premium alerts retain their existing cadence.
- Scheduled weekdays 09:15–15:30 IST plus a one-minute closing fetch allowance. Price/candle
  freshness must pass to admit a sample. Holidays/expired tokens cannot create fresh samples
  from old prices. Exact holiday calendars are not added in this release.
- At most one compact sample per minute. Not a tick archive, and fast intraminute events may be missed.
- Nifty/futures completed 1m candles are deduplicated by instrument and timestamp. Candle backfill
  from the provider may include earlier same-day candles; it does not recreate missed OI or signals.
- Selected-expiry options within 500 Nifty points plus barrier-adjacent and available protected
  CE/PE/condor legs; identity, strike, side, premium, OI, volume, IV and bid/ask. No full raw HTTP payloads.
- Futures/VIX/Top-9 quote fields and source timestamps, feed states, 3m/15m indicator snapshots,
  frozen observed barrier levels, and activity context.
- Position relative to observed zones; completed 3m break, rejection, failed break and retest-hold
  evidence. Consecutive identical states deduplicate. Original zones remain tracked after nearest
  changes. Slightly moved zones are distinct, not silently merged or counted as the same level.
- Strong aligned patterns reuse existing eligibility checks and are recorded ONLY, not sent.
- App AI action/status/version is sent by the UI on its minute refresh while fresh. It is explicitly
  separate from the background reference direction, since manual FII/event/risk/journal settings
  may differ. App-closed intervals have no fabricated actual-app AI decisions.

## Limits and safety

- This is an evidence diary, not an exact full-market replay or a new reversal predictor.
- Background observer has isolated runtime state; does not update the app's journal, manual trades,
  FII/DII cloud journal or direction weights. No automatic use of diary history as a strategy vote.
- Feed failures skip fresh samples; gaps/status remain visible. Missing option-chain data saves no
  stale option rows. A gap over two minutes invalidates the prior retest phase.
- Prior day diary is cleared on the first fresh sample of a new session, not midnight or a holiday
  failure. Current day remains available after close. Indicator warm-up candles still come from the
  existing provider path. FII/DII and actual/shadow journals are not deleted.
- SQLite is stored under `/data/nifty_day_memory/session.sqlite3`. Reusable freed pages may keep
  the file's allocated size above today's live data. Supporting analysis checkpoints also use space.
- Test fixture: 375 samples, 750 candle rows, 42 options/sample, simple few-event day = 3,457,024
  SQLite bytes (~3.30 MiB). This is NOT a real-market size/performance guarantee. Budget 5–15 MB
  for compact diary as an initial estimate; additional runtime files/network usage are separate.
- Full option-chain fetching remains as existing provider API permits; saving selected rows does
  not itself reduce API response bandwidth. No new purchased data source or token automation.
- Day memory endpoint requires LIVE_API_KEY even if other legacy endpoints have no configured key.

## UI

Simple expandable daily history shows latest 30 of the 100 returned events, recording status/date,
first/last sample and measured database size. Events keep original observation timestamps.
Barrier labels say Level strength, distinguish zone distance, and mark after-hours maps as last
available. Strength scores are evidence, not reversal probability. Existing barrier scores unchanged.

## Verification

Offline regression tests, synthetic one-day size test, restart/dedupe/rollover/missing-feed checks,
old-zone tracking, 3m retest logic, authenticated endpoint and single-worker lock checks.
Final run: 314 tests passed; Ruff passed. Existing NumPy timedelta deprecation warnings remain.
Real Railway volume, deployed credentials, browser rendering and live-market timing must still be
verified after upload. No deployment or external alerts/orders were executed during development.
