# 2.40.4 — Recording and Greeks diagnostics, reference candidate restored

## Confirmed fixes
- Main AI shows available reference strategy, strike/hedge and Fit % during live WAIT. WAIT remains unchanged; no candidate is invented for unavailable plans. Closed-market behavior remains reference-only.
- Raw IV display now uses the actual `source_implied_volatility` field. IV pair ratio and delta pair gap are exported for diagnosis. A ratio warning is explicitly not described as an API-versus-screen mismatch.
- Manual premium calculator now honors explicit invalid/model-mismatch Greek status. IV-warning calculations are conditional scenarios with a conservative 35/100 display ceiling, not a measured success probability. Automatic retest-price restrictions remain in place. Provider Greeks and numerical projection formulas are not recalibrated or force-matched.
- Recording schema 2 preserves raw API Greeks alongside usable fields, previous OI/volume/close, pair checks, and canonical background module results: core, price action, patterns, OI windows, heavyweights, volume, VIX/news/event context, decision score audit, trade plan, execution guard and risk profile.
- Recording panel shows latest sample coverage, raw-Greek row count, last app-AI event and shared-history feed message. Support ZIP includes a small recording_diagnostics.json with recorder health and history-use status, not a complete database export.

## Important limits
- Existing records are not backfilled. Deploy on Railway as well as the UI so the background recorder uses schema 2. Keep the existing persistent volume and DAY_MEMORY_ENABLED=1.
- One sample per minute maximum; request/build time can extend the interval. Gaps remain gaps, never synthesized observations. Options remain scoped to tracked/nearby contracts, not the whole exchange chain.
- Background canonical decisions and app-AI observations remain distinct. Saved fields do not imply fresh/valid evidence. No extra history vote, automatic training, or changed strategy thresholds.
- Latest supplied 14:25 bundle confirms shared Top-9 observations and ready 3m/5m OI windows; its 1m OI continuity is insufficient. It does not contain the full persistent database, so live disk completeness is not certified.
- Dhan web/API values are close in the supplied comparison, but independent pricing-model correctness is still unverified. Invalid Greek guards and hedge/risk checks remain enabled.
- Includes 2.40.3 timing defaults: new-entry end 15:00 IST and forced-exit reminder 15:15 IST, not automated broker execution.
