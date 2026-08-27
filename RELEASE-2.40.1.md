# v2.40.1 — Recent History (read-only)

Adds a compact Recent History card inside the expiry-cycle record panel:
- Observed 5/15 minute Nifty change, actual start/end times, recovery/pullback relative to current structural direction.
- Buying/selling evidence score deltas, only with live futures/options feeds and identical futures contract.
- Price/flow disagreement is descriptive, not proven absorption or a reversal signal.
- Latest exact-zone, same-day/expiry/version 3m reaction and last recorded price position.
- Rejects future, stale, mismatched, naive-time and invalid-price observations; repeated minutes do not create confirmation. Gaps >120 seconds block window comparisons. Each requested window requires a starting observation at least that old (up to 90 seconds tolerance), and latest observation must be <=120 seconds old.

No change to final AI action, weights, risk, trade selection, orders, journal or Telegram. Display thresholds (4 Nifty points flat band, 10 score difference flow disagreement) are not calibrated trading rules. No day-return claim without a verified previous close. No automated learning or accuracy guarantee. History data uses background recorder context, which can differ from manual app settings.

Report exposes up to 20 recent samples. Barrier event lookup uses the limited latest 100 event log; missing match is not proof no test occurred. Old untagged events remain in the existing diary, but do not become current matching reactions. No DB schema change, reset or deletion. Existing expiry rollover remains unchanged. New version requires fresh matching-version observations before comparisons appear.

Upload this cumulative ZIP only. Keep the existing /data Railway Volume and DAY_MEMORY_ENABLED=1. No new variable is required. Update Railway and reboot the app so both use the same version. Market-closed view stays reference-only. Verify actual recording and server-restart persistence during the next session; laptop restart alone does not test Railway persistence.
