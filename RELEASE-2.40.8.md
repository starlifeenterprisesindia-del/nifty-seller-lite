# 2.40.8 — Cycle price history and same-brain outlook

Cumulative update from 2.40.7. Upload this package only; preserve the existing Railway volume and credentials. Update both the app and Railway service if separately deployed. Version: `2.40.8_CYCLE_PRICE_OUTLOOK`.

## New view

Inside the existing expiry-record panel, open **Expiry-cycle Price History — 9:30 / 3:30 CE + PE**. Select CE and PE contracts independently. Both selected contracts remain fixed across the displayed cycle. Expiry, strike, side and security ID identify contracts; ambiguous duplicate rows are excluded.

The main table shows day, 09:30/15:30 IST, Nifty LTP, CE LTP and PE LTP. Only observations within the requested minute are used. A 15:28 sample is not substituted for 15:30. Missing observations display blank/Data missing; unavailable quotes are not zero. LTP is not an executable quote or official expiry settlement.

Details include actual snapshot timestamps, daily endpoint changes and percentages, observed Nifty/option high-low, OI, IV and bid/ask when saved. High-low comes from recorded minute observations, not every market tick. Days with no saved samples are not invented; the panel cannot establish which absent days were holidays versus full-day outages. Old records are not backfilled.

The view reads existing saved market records through the authenticated history report, without new Dhan calls or another recorder. The initial observed strike set is retained in future compact records where available from the existing source chain, even if spot moves away. Other strikes may have shorter histories. Existing expiry archival remains unchanged.

## Outlook and bugs corrected

- Existing 5m/15m/30m/1h view adds a 1-day row, meaning **next trading session**, not exactly 24 hours or today's remaining hours.
- Daily row is explicitly provisional same-brain context, not a trained daily forecasting model. It requires at least two recorded cycle days and agreement between current canonical direction and core view, otherwise PENDING/MIXED. This minimum is an availability gate, not statistical validation. No daily probability is invented.
- Intraday percentages are now a separate **Evidence /100** column, explicitly not success probability.
- Zero/invalid evidence previously defaulted to bullish because of dictionary order; now INSUFFICIENT DATA. Equal leading scores now show MIXED instead of arbitrarily bullish.
- A cached fast-monitor impulse can no longer overwrite the completed-candle 5m horizon. Fast alerts remain separate early warnings.
- Closed-market view explicitly says Last-session outlook.

No new directional weights, auto-learning or trading authorization was introduced. RSI strategy and spot-to-premium calculator remain intentionally independent advisory tools. Background and app snapshots use the same decision implementation but can have different inputs/timestamps; their results need not be identical.

## Audit performed

Static checks across 78 runtime Python files found no repeated module-level definitions, no identical nonempty runtime files, and no root-level duplicate timeframe_outlook.py. The canonical calculate_final_decision call is in services/snapshot_service.py. This is not proof of no possible bugs; regression and offline UI tests supplement the static checks.

Final local regression result: **386 tests passed**, 35 existing NumPy/PDF dependency deprecation warnings, no test failures.

## Deployment check

1. Confirm app and Railway version 2.40.8 after redeploy; do not delete the volume.
2. Expand the history panel, choose both contracts, and confirm old data remains visible.
3. Check missing target-minute rows remain missing rather than silently using nearest snapshots.
4. On a live day compare target-minute snapshot timestamps against source data. Existing feed/session rules may reject 15:30 observations; this update does not weaken freshness checks or manufacture a close. A dedicated verified closing-price collector is not included.
5. Verify the main decision is unchanged by opening/changing history selections and that 1-day context is labelled provisional.

Prediction logging/calibrated next-day forecasting, authoritative exchange holidays, full-cycle missing-day accounting and an exact closing-quote collector remain separate follow-up work, not completed claims in this release.

Commit: `Update v2.40.8: cycle CE/PE history, daily outlook context and duplicate-flow audit`
