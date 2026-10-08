# Architecture — V2.71 Observability / Replay / State Hardening

## Canonical live path — unchanged

`services/snapshot_service.py` builds one authoritative `MarketSnapshot`. The protected operational brain remains:

1. Trend / Regime — 40%
2. Options Flow — 25%
3. Participation — 20%
4. Barrier / Entry — 15%

Available evidence is normalized; missing evidence is NO VOTE. Market Intelligence has zero One Brain weight.

## New diagnostic lane — zero live decision weight

`analysis/snapshot_integrity.py` consumes only the existing `feed_status` already present in the snapshot. It reports:

- core decision feeds LIVE count
- context feeds LIVE count
- source-age values where the broker supplies them
- timestamped live-feed age skew
- GOOD / CAUTION / LIMITED / REFERENCE diagnostic state

Request-time option-chain data without an exchange timestamp is explicitly labelled instead of receiving a fake age. This diagnostic never gates One Brain in v2.71.

## Latency observability

SnapshotService already records stage timings with `perf_mark`. V2.71 keeps that critical-path mechanism unchanged and improves presentation only:

- current pipeline/build/finalize timing
- P50/P95 session latency
- top three slow stages
- refresh-budget status
- feed freshness + Snapshot Integrity state

No second timer-heavy pipeline and no extra API request are added.

## Replay/calibration lane — on demand only

Persistent Day Memory already uses Railway SQLite. V2.71 extends its explicit replay projection to include the Market Intelligence state that was recorded at that minute:

- Move Radar
- Move Pressure / velocity
- Pressure Quality
- Move Attack / Move Risk
- One Brain alignment
- liquidity hunt direction / next hunt zone
- snapshot sync state

`analysis/session_calibration.py` generates descriptive post-market summaries from retrospective labels. It never reconstructs an earlier signal using future information, never auto-tunes a threshold and never feeds results into the live predictor.

## State management

High-value durable session history stays in SQLite through Day Memory. Small same-day runtime JSON stores remain bounded and local. In 2.71, Big Player/activity state gains process-safe locking around the already atomic read-modify-write cycle. Option state, discipline state and context stores already use hardened persistence patterns.

A wholesale Redis/Postgres migration is intentionally deferred until multi-user/multi-replica production architecture requires it; adding an external service during single-user live validation would add complexity and failure modes without improving signal quality.

## Pressure Integrity rules retained

- early Move Radar must not wait for W/M or candle completion
- W/M and strong candles are supportive only
- high pressure is not a trade signal
- pressure that already produced a move is REALIZED, not retroactively fake
- opposite pressure begins with FLIP WATCH before FLIP CONFIRMED
- liquidity reclaim starts as REVERSAL WATCH before stronger follow-through

## Golden Rule

**Observability must observe; it must not become another trading brain.**

V2.71 adds no Dhan request, does not change protected One Brain weights/calculations, and keeps expensive replay/calibration outside the live critical path.
