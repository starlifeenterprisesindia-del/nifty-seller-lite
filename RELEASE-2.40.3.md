# 2.40.3 — Score audit and timing clarification

- New risk-profile defaults: entry end 15:00 IST, forced-exit reminder 15:15 IST. Entry start remains 10:15. Existing saved trade profiles are not rewritten. This app does not execute broker orders.
- Main Trend (Core) card now shows the core direction and matching core score. Combined AI direction remains explicit in its note.
- Strategy audit exposes actual base contributions, aggregate net adjustments/caps/rounding, and final fit. Evidence quality is not multiplied a second time in the displayed module contribution. No trading thresholds were lowered.
- Overlapping support/resistance zones no longer produce an apparently clear range or position percentage. Original levels remain unchanged; this is a display/range-context correction, not a new trade trigger.
- Strike-wise Greeks diagnostics expose available raw API IV/delta/theta values. The API-versus-broker-screen discrepancy remains unresolved: invalid Greeks remain blocked and IV-warning retest estimates remain disabled.
- Includes previous shared-history and expiry-cycle recording changes. No historical records, secrets, or runtime databases are bundled.
