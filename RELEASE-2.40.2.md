# 2.40.2 Shared market history

- Authenticated read-only Railway market-history route exposes bounded recorder OI and Top-9 observations. No broker requests, orders or trade-state sharing.
- App merges recorder observations with local observations before calculation. Same IST day, expiry (options), past timestamps and 30-minute retention are checked; timestamps are deduplicated.
- One-minute option comparisons accept up to 90 seconds to accommodate timer/fetch latency; actual elapsed time remains reported. Three/five-minute upper tolerance capped at 90 extra seconds. Missing comparisons remain unavailable.
- Top-9 allows an anchor up to 90 seconds older than its target; previous-day anchors rejected.
- On missing/older Railway endpoint, local history remains available and analysis_history feed reports the fallback.
- Deploy this complete package to BOTH Railway and the app. Existing /data volume and DAY_MEMORY_ENABLED stay unchanged. No data deletion or credential changes.
- Not changed: scoring weights, entry thresholds, risk budgets, confirmation rules, Greeks/IV validation, journal eligibility. History availability is not a profitability guarantee.
- Greek mismatch warnings require source/model investigation and are not bypassed by this release. Browser pause/runtime expiry can still interrupt app-only signal confirmations; recorder observations do not invent confirmations.
